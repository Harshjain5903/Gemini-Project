"""
Chicago Crash Data Ingestion Module
Fetches Traffic Crashes data from Chicago Data Portal (Socrata API)
"""
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from pathlib import Path
from typing import Optional


class ChicagoCrashIngestion:
    """Fetches and processes Chicago Traffic Crash data"""

    # Chicago Data Portal - Traffic Crashes
    BASE_URL = "https://data.cityofchicago.org/resource/85ca-t3if.json"

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
        cache_file = self.cache_dir / f"chicago_crashes_{year_start}_{limit}.parquet"

        if use_cache and cache_file.exists():
            print(f"Loading cached Chicago crash data from {cache_file}")
            self.raw_data = pd.read_parquet(cache_file)
            return self.raw_data

        print(f"Fetching up to {limit} Chicago crash records...")

        select_cols = ",".join([
            "crash_date", "crash_hour", "crash_day_of_week", "crash_month",
            "latitude", "longitude",
            "street_no", "street_direction", "street_name",
            "injuries_total", "injuries_fatal",
            "injuries_incapacitating", "injuries_non_incapacitating",
            "most_severe_injury", "prim_contributory_cause",
            "crash_type", "first_crash_type"
        ])

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
            fallback = self._find_fallback_cache("chicago_crashes_*.parquet")
            if fallback is not None:
                print(f"WARNING: API request failed ({e.__class__.__name__}). "
                      f"Using fallback cache: {fallback}")
                self.raw_data = pd.read_parquet(fallback)
                return self.raw_data
            raise ConnectionError(
                f"Cannot fetch Chicago crash data and no cached files found.\n"
                f"Error: {e}"
            ) from e

        self.raw_data = pd.DataFrame(all_records)
        self.raw_data.to_parquet(cache_file)
        print(f"Cached {len(self.raw_data)} records to {cache_file}")

        return self.raw_data

    def _find_fallback_cache(self, pattern: str) -> Optional[Path]:
        candidates = sorted(
            self.cache_dir.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        return candidates[0] if candidates else None

    def clean_and_geocode(self) -> gpd.GeoDataFrame:
        if self.raw_data is None:
            raise ValueError("No data loaded. Call fetch_crashes() first.")

        df = self.raw_data.copy()

        # Convert coordinates
        df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
        df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

        # Chicago bounding box
        valid_mask = (
            df["latitude"].notna() &
            df["longitude"].notna() &
            (df["latitude"] > 41.6) &
            (df["latitude"] < 42.1) &
            (df["longitude"] > -88.0) &
            (df["longitude"] < -87.5)
        )
        df = df[valid_mask].copy()
        print(f"Valid geocoded records: {len(df)}")

        # Parse datetime
        df["crash_datetime"] = pd.to_datetime(df["crash_date"], errors="coerce")
        df["hour"] = pd.to_numeric(df.get("crash_hour", pd.Series([12]*len(df))), errors="coerce").fillna(12).astype(int)

        # Chicago day_of_week: 1=Sunday .. 7=Saturday → Python: 0=Monday .. 6=Sunday
        raw_dow = pd.to_numeric(df.get("crash_day_of_week", pd.Series([2]*len(df))), errors="coerce").fillna(2).astype(int)
        df["day_of_week"] = (raw_dow - 2) % 7
        df["month"] = pd.to_numeric(df.get("crash_month", pd.Series([1]*len(df))), errors="coerce").fillna(1).astype(int)

        # Injury columns
        for col in ["injuries_total", "injuries_fatal", "injuries_incapacitating", "injuries_non_incapacitating"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
            else:
                df[col] = 0

        # Map to NYC-compatible column names for downstream pipeline compatibility
        df["number_of_persons_injured"] = df["injuries_total"]
        df["number_of_persons_killed"] = df["injuries_fatal"]
        # Chicago main crash table doesn't split by person type
        df["number_of_pedestrians_injured"] = 0
        df["number_of_pedestrians_killed"] = 0
        df["number_of_cyclist_injured"] = 0
        df["number_of_cyclist_killed"] = 0
        df["number_of_motorist_injured"] = df["injuries_total"]
        df["number_of_motorist_killed"] = df["injuries_fatal"]

        # Severity: killed=10, injured=2, incident=1
        df["severity"] = (
            df["number_of_persons_killed"] * 10 +
            df["number_of_persons_injured"] * 2 +
            1
        )

        # Street name for segment mapping
        if "street_name" in df.columns:
            df["on_street_name"] = df["street_name"].fillna("UNKNOWN")
        else:
            df["on_street_name"] = "UNKNOWN"
        df["cross_street_name"] = None

        # Create geometry
        geometry = [Point(xy) for xy in zip(df["longitude"], df["latitude"])]
        self.geo_data = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")

        return self.geo_data

    def get_processed_data(self) -> gpd.GeoDataFrame:
        if self.geo_data is None:
            self.fetch_crashes()
            self.clean_and_geocode()
        return self.geo_data

    def get_stats(self) -> dict:
        if self.geo_data is None:
            return {}
        gdf = self.geo_data
        return {
            "total_crashes": len(gdf),
            "date_range": {
                "start": str(gdf["crash_datetime"].min()),
                "end": str(gdf["crash_datetime"].max())
            },
            "total_injured": int(gdf["number_of_persons_injured"].sum()),
            "total_killed": int(gdf["number_of_persons_killed"].sum()),
            "avg_severity": float(gdf["severity"].mean())
        }
