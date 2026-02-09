"""
NYC Crash Data Ingestion Module
Fetches Motor Vehicle Collisions data from NYC Open Data (Socrata API)
"""
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from pathlib import Path
from typing import Optional
import json


class CrashDataIngestion:
    """Fetches and processes NYC Motor Vehicle Collision data"""

    # NYC Open Data - Motor Vehicle Collisions endpoint
    BASE_URL = "https://data.cityofnewyork.us/resource/h9gi-nx95.json"

    def __init__(self, cache_dir: str = "cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.raw_data: Optional[pd.DataFrame] = None
        self.geo_data: Optional[gpd.GeoDataFrame] = None

    def fetch_crashes(
        self,
        limit: int = 50000,
        year_start: int = 2023,
        use_cache: bool = True
    ) -> pd.DataFrame:
        """
        Fetch crash data from NYC Open Data API

        Args:
            limit: Max records to fetch (API limit is 50k per request)
            year_start: Filter crashes from this year onward
            use_cache: Use cached data if available

        Returns:
            DataFrame with crash records
        """
        cache_file = self.cache_dir / f"crashes_{year_start}_{limit}.parquet"

        if use_cache and cache_file.exists():
            print(f"Loading cached data from {cache_file}")
            self.raw_data = pd.read_parquet(cache_file)
            return self.raw_data

        print(f"Fetching up to {limit} crash records from NYC Open Data...")

        # Socrata Query Language (SoQL) parameters
        select_cols = ",".join([
            "crash_date",
            "crash_time",
            "borough",
            "zip_code",
            "latitude",
            "longitude",
            "on_street_name",
            "cross_street_name",
            "number_of_persons_injured",
            "number_of_persons_killed",
            "number_of_pedestrians_injured",
            "number_of_pedestrians_killed",
            "number_of_cyclist_injured",
            "number_of_cyclist_killed",
            "number_of_motorist_injured",
            "number_of_motorist_killed",
            "contributing_factor_vehicle_1",
            "vehicle_type_code1"
        ])

        # Paginate: Socrata caps at 50k per request
        PAGE_SIZE = 50000
        all_records = []
        offset = 0
        remaining = limit

        try:
            while remaining > 0:
                page_limit = min(PAGE_SIZE, remaining)
                params = {
                    "$limit": page_limit,
                    "$offset": offset,
                    "$where": f"crash_date >= '{year_start}-01-01T00:00:00'",
                    "$order": "crash_date DESC",
                    "$select": select_cols
                }
                response = requests.get(self.BASE_URL, params=params, timeout=60)
                response.raise_for_status()
                page = response.json()
                if not page:
                    break
                all_records.extend(page)
                offset += len(page)
                remaining -= len(page)
                if len(page) < page_limit:
                    break
                if offset > page_limit:
                    print(f"  Fetched {offset} records so far...")
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            fallback = self._find_fallback_cache("crashes_*.parquet")
            if fallback is not None:
                print(f"WARNING: API request failed ({e.__class__.__name__}). "
                      f"Using fallback cache: {fallback}")
                self.raw_data = pd.read_parquet(fallback)
                return self.raw_data
            raise ConnectionError(
                f"Cannot fetch crash data and no cached files found in {self.cache_dir}/.\n"
                f"Run once with network access to populate the cache, "
                f"or place a .parquet file in {self.cache_dir}/."
            ) from e

        self.raw_data = pd.DataFrame(all_records)

        # Cache for faster subsequent runs
        self.raw_data.to_parquet(cache_file)
        print(f"Cached {len(self.raw_data)} records to {cache_file}")

        return self.raw_data

    def _find_fallback_cache(self, pattern: str) -> Optional[Path]:
        """Find any available cache file matching the pattern"""
        candidates = sorted(
            self.cache_dir.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        return candidates[0] if candidates else None

    def clean_and_geocode(self) -> gpd.GeoDataFrame:
        """
        Clean data and create GeoDataFrame with valid coordinates

        Returns:
            GeoDataFrame with geometry column
        """
        if self.raw_data is None:
            raise ValueError("No data loaded. Call fetch_crashes() first.")

        df = self.raw_data.copy()

        # Convert coordinates to numeric
        df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
        df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

        # Filter valid coordinates (NYC bounding box)
        valid_mask = (
            df["latitude"].notna() &
            df["longitude"].notna() &
            (df["latitude"] > 40.4) &
            (df["latitude"] < 41.0) &
            (df["longitude"] > -74.3) &
            (df["longitude"] < -73.6)
        )

        df = df[valid_mask].copy()
        print(f"Valid geocoded records: {len(df)}")

        # Parse datetime
        df["crash_datetime"] = pd.to_datetime(
            df["crash_date"].str[:10] + " " + df["crash_time"],
            errors="coerce"
        )
        df["hour"] = df["crash_datetime"].dt.hour
        df["day_of_week"] = df["crash_datetime"].dt.dayofweek
        df["month"] = df["crash_datetime"].dt.month

        # Calculate severity score (weighted)
        # Note: number_of_persons_injured/killed is already the total of
        # pedestrians + cyclists + motorists, so use only the total columns
        # to avoid double-counting.
        all_numeric_cols = [
            "number_of_persons_injured",
            "number_of_persons_killed",
            "number_of_pedestrians_injured",
            "number_of_pedestrians_killed",
            "number_of_cyclist_injured",
            "number_of_cyclist_killed",
            "number_of_motorist_injured",
            "number_of_motorist_killed"
        ]

        for col in all_numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        # Severity: killed=10, injured=2, incident=1
        df["severity"] = (
            df["number_of_persons_killed"] * 10 +
            df["number_of_persons_injured"] * 2 +
            1  # base incident weight
        )

        # Create geometry
        geometry = [Point(xy) for xy in zip(df["longitude"], df["latitude"])]
        self.geo_data = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")

        return self.geo_data

    def get_processed_data(self) -> gpd.GeoDataFrame:
        """Get processed GeoDataFrame, fetching if necessary"""
        if self.geo_data is None:
            self.fetch_crashes()
            self.clean_and_geocode()
        return self.geo_data

    def get_stats(self) -> dict:
        """Return summary statistics of the loaded data"""
        if self.geo_data is None:
            return {}

        gdf = self.geo_data
        return {
            "total_crashes": len(gdf),
            "date_range": {
                "start": str(gdf["crash_datetime"].min()),
                "end": str(gdf["crash_datetime"].max())
            },
            "by_borough": gdf["borough"].value_counts().to_dict(),
            "total_injured": int(gdf["number_of_persons_injured"].sum()),
            "total_killed": int(gdf["number_of_persons_killed"].sum()),
            "avg_severity": float(gdf["severity"].mean())
        }
