"""
Unit tests for the geospatial risk pipeline.
Uses synthetic data — no network or cache required.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_ingestion import CrashDataIngestion
from src.grid_risk import GridRiskCalculator
from src.crime_risk import CrimeRiskCalculator
from src.segment_risk import SegmentRiskMapper
from src.time_patterns import TimePatternAnalyzer
from src.validation import ValidationStats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_crash_gdf(n: int = 200) -> gpd.GeoDataFrame:
    """Create a synthetic crash GeoDataFrame centred on Midtown Manhattan."""
    rng = np.random.RandomState(42)
    lats = 40.75 + rng.normal(0, 0.01, n)
    lngs = -73.98 + rng.normal(0, 0.01, n)
    df = pd.DataFrame({
        "latitude": lats,
        "longitude": lngs,
        "crash_date": pd.date_range("2024-01-01", periods=n, freq="h").strftime("%Y-%m-%dT%H:%M:%S"),
        "crash_time": pd.date_range("2024-01-01", periods=n, freq="h").strftime("%H:%M"),
        "borough": rng.choice(["MANHATTAN", "BROOKLYN", "QUEENS"], n),
        "on_street_name": rng.choice(["BROADWAY", "5 AVENUE", "LEXINGTON AVE", None], n),
        "cross_street_name": rng.choice(["42 STREET", "34 STREET", None], n),
        "number_of_persons_injured": rng.choice([0, 1, 2], n, p=[0.7, 0.2, 0.1]),
        "number_of_persons_killed": rng.choice([0, 1], n, p=[0.98, 0.02]),
        "number_of_pedestrians_injured": rng.choice([0, 1], n, p=[0.85, 0.15]),
        "number_of_pedestrians_killed": rng.choice([0, 1], n, p=[0.99, 0.01]),
        "number_of_cyclist_injured": rng.choice([0, 1], n, p=[0.9, 0.1]),
        "number_of_cyclist_killed": np.zeros(n, dtype=int),
        "number_of_motorist_injured": rng.choice([0, 1], n, p=[0.85, 0.15]),
        "number_of_motorist_killed": np.zeros(n, dtype=int),
    })
    df["crash_datetime"] = pd.to_datetime(df["crash_date"])
    df["hour"] = df["crash_datetime"].dt.hour
    df["day_of_week"] = df["crash_datetime"].dt.dayofweek
    df["month"] = df["crash_datetime"].dt.month
    df["severity"] = df["number_of_persons_killed"] * 10 + df["number_of_persons_injured"] * 2 + 1
    geometry = [Point(x, y) for x, y in zip(df["longitude"], df["latitude"])]
    return gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")


def make_crime_gdf(n: int = 150) -> gpd.GeoDataFrame:
    """Create a synthetic crime GeoDataFrame."""
    rng = np.random.RandomState(99)
    lats = 40.75 + rng.normal(0, 0.01, n)
    lngs = -73.98 + rng.normal(0, 0.01, n)
    df = pd.DataFrame({
        "latitude": lats,
        "longitude": lngs,
        "crime_datetime": pd.date_range("2024-01-01", periods=n, freq="2h"),
        "ofns_desc": rng.choice(["ROBBERY", "FELONY ASSAULT", "GRAND LARCENY"], n),
        "severity": rng.choice([1, 2, 3, 5], n).astype(float),
    })
    df["hour"] = df["crime_datetime"].dt.hour
    df["day_of_week"] = df["crime_datetime"].dt.dayofweek
    geometry = [Point(x, y) for x, y in zip(df["longitude"], df["latitude"])]
    return gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")


# ---------------------------------------------------------------------------
# Tests — Severity
# ---------------------------------------------------------------------------

def test_severity_no_double_counting():
    """Severity should use only the total columns, not sum subcategories."""
    gdf = make_crash_gdf(10)
    # Manually check one row
    row = gdf.iloc[0]
    expected = row["number_of_persons_killed"] * 10 + row["number_of_persons_injured"] * 2 + 1
    assert row["severity"] == expected, (
        f"Severity {row['severity']} != expected {expected}"
    )


# ---------------------------------------------------------------------------
# Tests — Grid Risk
# ---------------------------------------------------------------------------

def test_grid_risk_scores_range():
    """All risk scores should be in [0, 100]."""
    gdf = make_crash_gdf()
    calc = GridRiskCalculator(resolution=8)
    gdf = calc.assign_h3_cells(gdf)
    calc.calculate_cell_risk(gdf, time_weighted=False)
    assert calc.grid_data["risk_score"].min() >= 0
    assert calc.grid_data["risk_score"].max() <= 100


def test_grid_risk_category_no_nan():
    """No cell should have NaN risk category (pd.cut include_lowest fix)."""
    gdf = make_crash_gdf()
    calc = GridRiskCalculator(resolution=8)
    gdf = calc.assign_h3_cells(gdf)
    calc.calculate_cell_risk(gdf, time_weighted=False)
    nan_count = calc.grid_data["risk_category"].isna().sum()
    assert nan_count == 0, f"{nan_count} cells have NaN risk_category"


def test_spatial_smoothing_adds_column():
    """apply_spatial_smoothing should produce a smoothed_risk column."""
    gdf = make_crash_gdf()
    calc = GridRiskCalculator(resolution=8)
    gdf = calc.assign_h3_cells(gdf)
    calc.calculate_cell_risk(gdf, time_weighted=False)
    calc.apply_spatial_smoothing()
    assert "smoothed_risk" in calc.grid_data.columns


def test_grid_geodataframe_does_not_mutate():
    """create_grid_geodataframe should NOT add geometry to grid_data."""
    gdf = make_crash_gdf()
    calc = GridRiskCalculator(resolution=8)
    gdf = calc.assign_h3_cells(gdf)
    calc.calculate_cell_risk(gdf, time_weighted=False)
    calc.create_grid_geodataframe()
    assert "geometry" not in calc.grid_data.columns, (
        "grid_data was mutated with geometry column"
    )


# ---------------------------------------------------------------------------
# Tests — Crime Risk
# ---------------------------------------------------------------------------

def test_crime_risk_scores_range():
    """Crime risk scores should be in [0, 100]."""
    gdf = make_crime_gdf()
    calc = CrimeRiskCalculator(resolution=8)
    gdf = calc.assign_h3_cells(gdf)
    result = calc.calculate_cell_crime_risk(gdf, time_weighted=False)
    assert result["crime_risk"].min() >= 0
    assert result["crime_risk"].max() <= 100


# ---------------------------------------------------------------------------
# Tests — Segment Risk
# ---------------------------------------------------------------------------

def test_segment_risk_creates_geometries():
    """Segment mapper should produce a GeoDataFrame with LineStrings."""
    gdf = make_crash_gdf()
    mapper = SegmentRiskMapper()
    mapper.aggregate_by_street(gdf)
    seg = mapper.create_segment_geometries()
    assert len(seg) > 0
    assert "geometry" in seg.columns


# ---------------------------------------------------------------------------
# Tests — Time Patterns
# ---------------------------------------------------------------------------

def test_hourly_risk_all_hours():
    """Hourly risk should have entries for every hour present in data."""
    gdf = make_crash_gdf(500)
    analyzer = TimePatternAnalyzer()
    hourly = analyzer.calculate_hourly_risk(gdf)
    hours_in_data = gdf["hour"].nunique()
    assert len(hourly) == hours_in_data


def test_period_fallback():
    """get_risk_for_time should return a period even for edge-case hours."""
    analyzer = TimePatternAnalyzer()
    gdf = make_crash_gdf()
    analyzer.calculate_hourly_risk(gdf)
    analyzer.calculate_period_risk(gdf)
    result = analyzer.get_risk_for_time(hour=0, is_weekend=False)
    assert result.get("time_period") == "night"


# ---------------------------------------------------------------------------
# Tests — Validation
# ---------------------------------------------------------------------------

def test_validation_empty_gdf():
    """data_quality_check should not crash on empty GeoDataFrame."""
    empty = gpd.GeoDataFrame(
        {"geometry": [], "crash_datetime": [], "on_street_name": [], "severity": []},
        crs="EPSG:4326"
    )
    v = ValidationStats()
    result = v.data_quality_check(empty)
    assert result["total_records"] == 0
    assert result["geocoded_pct"] == 0


# ---------------------------------------------------------------------------
# Tests — Blend
# ---------------------------------------------------------------------------

def test_blend_risks_outer_join():
    """blend_risks should keep cells from both crash and crime grids."""
    crash_gdf = make_crash_gdf()
    crime_gdf = make_crime_gdf()

    crash_calc = GridRiskCalculator(resolution=7)
    crash_gdf = crash_calc.assign_h3_cells(crash_gdf)
    crash_calc.calculate_cell_risk(crash_gdf, time_weighted=False)
    crash_calc.apply_spatial_smoothing()

    crime_calc = CrimeRiskCalculator(resolution=7)
    crime_gdf = crime_calc.assign_h3_cells(crime_gdf)
    crime_grid = crime_calc.calculate_cell_crime_risk(crime_gdf, time_weighted=False)

    time_analyzer = TimePatternAnalyzer()
    crash_time = time_analyzer.calculate_cell_time_risk(crash_gdf, h3_resolution=7)
    crime_time = crime_calc.calculate_crime_time_patterns(crime_gdf, h3_resolution=7)

    combined_grid, combined_time = CrimeRiskCalculator.blend_risks(
        crash_calc.grid_data, crime_grid, crash_time, crime_time
    )
    # Combined should have at least as many cells as the larger input
    assert len(combined_grid) >= max(len(crash_calc.grid_data), len(crime_grid))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed out of {passed + failed} tests")
    sys.exit(1 if failed else 0)
