"""
Chicago Crime Data Ingestion Module
Fetches crime data from Chicago Data Portal (Socrata API)
for pedestrian safety risk scoring
"""
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from pathlib import Path
from typing import Optional


class ChicagoCrimeIngestion:
    """Fetches and processes Chicago crime data"""

    # Chicago Data Portal - Crimes (2001 to Present)
    BASE_URL = "https://data.cityofchicago.org/resource/ijzp-q8t2.json"

    # Chicago crime types with severity weights (pedestrian safety focus)
    CRIME_WEIGHTS = {
        "HOMICIDE": 10,
        "CRIM SEXUAL ASSAULT": 8,
        "CRIMINAL SEXUAL ASSAULT": 8,
        "ROBBERY": 5,
        "BATTERY": 4,
        "ASSAULT": 3,
        "WEAPONS VIOLATION": 3,
        "SEX OFFENSE": 3,
        "KIDNAPPING": 6,
        "THEFT": 1,
        "MOTOR VEHICLE THEFT": 1,
    }

    # Outdoor locations relevant to pedestrians
    OUTDOOR_LOCATIONS = {
        "STREET", "SIDEWALK", "ALLEY",
        "PARKING LOT/GARAGE(NON.RESID.)", "PARKING LOT",
        "PARK PROPERTY", "CTA PLATFORM", "CTA STATION",
        "CTA BUS", "CTA TRAIN", "CTA L PLATFORM", "CTA L TRAIN",
        "HIGHWAY/EXPRESSWAY", "BRIDGE",
        "DRIVEWAY - RESIDENTIAL", "GAS STATION",
        "LAKEFRONT/WATERFRONT/RIVERBANK",
        "SCHOOL, PUBLIC, GROUNDS", "SPORTS ARENA/STADIUM",
    }

    def __init__(self, cache_dir: str = "cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.raw_data: Optional[pd.DataFrame] = None
        self.geo_data: Optional[gpd.GeoDataFrame] = None

    def fetch_crimes(
        self,
        limit: int = 50000,
        year_start: int = 2023,
        use_cache: bool = True
    ) -> pd.DataFrame:
        cache_file = self.cache_dir / f"chicago_crimes_{year_start}_{limit}.parquet"

        if use_cache and cache_file.exists():
            print(f"Loading cached Chicago crime data from {cache_file}")
            self.raw_data = pd.read_parquet(cache_file)
            return self.raw_data

        print(f"Fetching up to {limit} Chicago crime records...")

        crime_types = "','".join(self.CRIME_WEIGHTS.keys())
        where_clause = (
            f"date >= '{year_start}-01-01T00:00:00' "
            f"AND latitude IS NOT NULL "
            f"AND primary_type IN('{crime_types}')"
        )
        select_cols = ",".join([
            "id", "date", "primary_type", "description",
            "location_description", "arrest", "domestic",
            "latitude", "longitude"
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
                    "$where": where_clause,
                    "$order": "date DESC",
                    "$select": select_cols
                }
                response = requests.get(self.BASE_URL, params=params, timeout=120)
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
                    print(f"  Fetched {offset} crime records so far...")
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            fallback = self._find_fallback_cache("chicago_crimes_*.parquet")
            if fallback is not None:
                print(f"WARNING: API request failed ({e.__class__.__name__}). "
                      f"Using fallback cache: {fallback}")
                self.raw_data = pd.read_parquet(fallback)
                return self.raw_data
            raise ConnectionError(
                f"Cannot fetch Chicago crime data and no cached files found.\n"
                f"Error: {e}"
            ) from e

        self.raw_data = pd.DataFrame(all_records)
        self.raw_data.to_parquet(cache_file)
        print(f"Cached {len(self.raw_data)} crime records to {cache_file}")

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
            raise ValueError("No data loaded. Call fetch_crimes() first.")

        df = self.raw_data.copy()

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

        # Filter to outdoor/street crimes
        if "location_description" in df.columns:
            outdoor_mask = df["location_description"].isin(self.OUTDOOR_LOCATIONS)
            df = df[outdoor_mask].copy()
            print(f"Filtered to {len(df)} outdoor/street crimes")

        # Parse datetime — Chicago format: "2024-01-15T12:30:00.000"
        df["crime_datetime"] = pd.to_datetime(df["date"], errors="coerce")
        df["hour"] = df["crime_datetime"].dt.hour
        df["day_of_week"] = df["crime_datetime"].dt.dayofweek

        # Map to NYC-compatible column names for downstream pipeline
        df["ofns_desc"] = df["primary_type"]

        # Assign severity based on crime type
        df["severity"] = df["primary_type"].map(self.CRIME_WEIGHTS).fillna(1).astype(float)

        # Boost severity for arrests (indicates more serious incident)
        if "arrest" in df.columns:
            arrest_mask = df["arrest"].astype(str).str.lower() == "true"
            df.loc[arrest_mask, "severity"] *= 1.3

        # Add law_cat_cd for pipeline compatibility (NYC uses FELONY/MISDEMEANOR)
        df["law_cat_cd"] = "FELONY"

        # Add prem_typ_desc for compatibility
        if "location_description" in df.columns:
            df["prem_typ_desc"] = df["location_description"]

        print(f"Valid geocoded crime records: {len(df)}")

        geometry = [Point(xy) for xy in zip(df["longitude"], df["latitude"])]
        self.geo_data = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")

        return self.geo_data

    def get_processed_data(self) -> gpd.GeoDataFrame:
        if self.geo_data is None:
            self.fetch_crimes()
            self.clean_and_geocode()
        return self.geo_data

    def get_stats(self) -> dict:
        if self.geo_data is None:
            return {}
        gdf = self.geo_data
        return {
            "total_crimes": len(gdf),
            "date_range": {
                "start": str(gdf["crime_datetime"].min()),
                "end": str(gdf["crime_datetime"].max())
            },
            "by_crime_type": gdf["primary_type"].value_counts().to_dict(),
            "avg_severity": float(gdf["severity"].mean())
        }
