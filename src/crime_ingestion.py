"""
NYC Crime Data Ingestion Module
Fetches NYPD Complaint Data from NYC Open Data (Socrata API)
for pedestrian safety risk scoring
"""
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from pathlib import Path
from typing import Optional


class CrimeDataIngestion:
    """Fetches and processes NYPD Complaint (crime) data"""

    # NYC Open Data - NYPD Complaint Data Historic
    BASE_URL = "https://data.cityofnewyork.us/resource/qgea-i56i.json"

    # Crime types relevant to pedestrian safety, with severity weights
    CRIME_WEIGHTS = {
        "MURDER & NON-NEGL. MANSLAUGHTER": 10,
        "RAPE": 8,
        "ROBBERY": 5,
        "FELONY ASSAULT": 4,
        "ASSAULT 3 & RELATED OFFENSES": 2,
        "DANGEROUS WEAPONS": 3,
        "SEX CRIMES": 3,
        "GRAND LARCENY": 1,
        "PETIT LARCENY": 1,
        "GRAND LARCENY OF MOTOR VEHICLE": 1,
    }

    # Premise types that represent outdoor/street locations (relevant to pedestrians)
    OUTDOOR_PREMISES = {
        "STREET", "PARK/PLAYGROUND", "TRANSIT - NYC SUBWAY",
        "TRANSIT FACILITY (OTHER)", "OPEN AREAS (OPEN COVERAGE)",
        "PARKING LOT/GARAGE (PUBLIC)", "BUS STOP", "TUNNEL",
        "BRIDGE", "HIGHWAY/PARKWAY", "PEDESTRIAN OVERPASS",
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
        """
        Fetch crime data from NYC Open Data API.
        Filters to pedestrian-relevant outdoor crimes.
        """
        cache_file = self.cache_dir / f"crimes_{year_start}_{limit}.parquet"

        if use_cache and cache_file.exists():
            print(f"Loading cached crime data from {cache_file}")
            self.raw_data = pd.read_parquet(cache_file)
            return self.raw_data

        print(f"Fetching up to {limit} crime records from NYC Open Data...")

        # Filter to relevant crime types
        crime_types = "','".join(self.CRIME_WEIGHTS.keys())
        where_clause = (
            f"cmplnt_fr_dt >= '{year_start}-01-01T00:00:00' "
            f"AND latitude IS NOT NULL "
            f"AND ofns_desc IN('{crime_types}')"
        )
        select_cols = ",".join([
            "cmplnt_num",
            "cmplnt_fr_dt",
            "cmplnt_fr_tm",
            "ofns_desc",
            "law_cat_cd",
            "boro_nm",
            "prem_typ_desc",
            "loc_of_occur_desc",
            "latitude",
            "longitude",
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
                    "$where": where_clause,
                    "$order": "cmplnt_fr_dt DESC",
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
            fallback = self._find_fallback_cache("crimes_*.parquet")
            if fallback is not None:
                print(f"WARNING: API request failed ({e.__class__.__name__}). "
                      f"Using fallback cache: {fallback}")
                self.raw_data = pd.read_parquet(fallback)
                return self.raw_data
            raise ConnectionError(
                f"Cannot fetch crime data and no cached files found in {self.cache_dir}/.\n"
                f"Run once with network access to populate the cache, "
                f"or place a .parquet file in {self.cache_dir}/."
            ) from e

        self.raw_data = pd.DataFrame(all_records)

        # Cache for faster subsequent runs
        self.raw_data.to_parquet(cache_file)
        print(f"Cached {len(self.raw_data)} crime records to {cache_file}")

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
        Clean crime data and create GeoDataFrame with valid coordinates.
        Filters to outdoor/street crimes and assigns severity weights.
        """
        if self.raw_data is None:
            raise ValueError("No data loaded. Call fetch_crimes() first.")

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

        # Filter to outdoor/street crimes (most relevant for pedestrians)
        if "prem_typ_desc" in df.columns:
            outdoor_mask = df["prem_typ_desc"].isin(self.OUTDOOR_PREMISES)
            df = df[outdoor_mask].copy()
            print(f"Filtered to {len(df)} outdoor/street crimes")

        # Parse datetime
        if "cmplnt_fr_dt" in df.columns and "cmplnt_fr_tm" in df.columns:
            df["crime_datetime"] = pd.to_datetime(
                df["cmplnt_fr_dt"].str[:10] + " " + df["cmplnt_fr_tm"].fillna("12:00:00"),
                errors="coerce"
            )
        else:
            df["crime_datetime"] = pd.to_datetime(df["cmplnt_fr_dt"], errors="coerce")

        df["hour"] = df["crime_datetime"].dt.hour
        df["day_of_week"] = df["crime_datetime"].dt.dayofweek

        # Assign severity based on crime type
        df["severity"] = df["ofns_desc"].map(self.CRIME_WEIGHTS).fillna(1).astype(float)

        # Felonies get a boost
        df.loc[df["law_cat_cd"] == "FELONY", "severity"] *= 1.5

        print(f"Valid geocoded crime records: {len(df)}")

        # Create geometry
        geometry = [Point(xy) for xy in zip(df["longitude"], df["latitude"])]
        self.geo_data = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")

        return self.geo_data

    def get_processed_data(self) -> gpd.GeoDataFrame:
        """Get processed GeoDataFrame, fetching if necessary"""
        if self.geo_data is None:
            self.fetch_crimes()
            self.clean_and_geocode()
        return self.geo_data

    def get_stats(self) -> dict:
        """Return summary statistics of the loaded crime data"""
        if self.geo_data is None:
            return {}

        gdf = self.geo_data
        return {
            "total_crimes": len(gdf),
            "date_range": {
                "start": str(gdf["crime_datetime"].min()),
                "end": str(gdf["crime_datetime"].max())
            },
            "by_borough": gdf["boro_nm"].value_counts().to_dict() if "boro_nm" in gdf.columns else {},
            "by_crime_type": gdf["ofns_desc"].value_counts().to_dict(),
            "avg_severity": float(gdf["severity"].mean())
        }
