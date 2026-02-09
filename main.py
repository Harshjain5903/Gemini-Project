#!/usr/bin/env python3
"""
NYC Safe Routing - Geospatial Risk Analysis Pipeline
====================================================

Main orchestration script that:
1. Ingests NYC crash data
2. Ingests NYC crime data (NYPD complaints)
3. Calculates H3 grid-based risk scores (crash + crime)
4. Maps risk to road segments
5. Analyzes time-of-day patterns
6. Blends crash + crime risk for travel-mode-aware routing
7. Exports data for routing engine and visualization
8. Generates validation statistics

Usage:
    python main.py                    # Full pipeline with defaults
    python main.py --limit 10000      # Smaller dataset for testing
    python main.py --no-cache         # Force fresh data fetch
"""

import argparse
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.data_ingestion import CrashDataIngestion
from src.crime_ingestion import CrimeDataIngestion
from src.grid_risk import GridRiskCalculator
from src.crime_risk import CrimeRiskCalculator
from src.segment_risk import SegmentRiskMapper
from src.time_patterns import TimePatternAnalyzer
from src.export import RiskExporter
from src.validation import ValidationStats


def run_pipeline(
    limit: int = 50000,
    year_start: int = 2023,
    use_cache: bool = True,
    h3_resolution: int = 9,
    output_dir: str = "output"
):
    """
    Run the complete geospatial risk analysis pipeline

    Args:
        limit: Max crash records to fetch
        year_start: Start year for crash data
        use_cache: Use cached data if available
        h3_resolution: H3 grid resolution (9 recommended)
        output_dir: Directory for output files
    """
    print("=" * 60)
    print("NYC SAFE ROUTING - GEOSPATIAL RISK ANALYSIS")
    print("=" * 60)

    # Initialize components
    ingestion = CrashDataIngestion(cache_dir="cache")
    crime_ingestion = CrimeDataIngestion(cache_dir="cache")
    grid_calc = GridRiskCalculator(resolution=h3_resolution)
    crime_calc = CrimeRiskCalculator(resolution=h3_resolution)
    segment_mapper = SegmentRiskMapper()
    time_analyzer = TimePatternAnalyzer()
    exporter = RiskExporter(output_dir=output_dir, h3_resolution=h3_resolution)
    validator = ValidationStats()

    # ===========================================
    # STEP 1: Crash Data Ingestion
    # ===========================================
    print("\n[1/8] INGESTING CRASH DATA...")
    print("-" * 40)

    ingestion.fetch_crashes(
        limit=limit,
        year_start=year_start,
        use_cache=use_cache
    )
    crash_gdf = ingestion.clean_and_geocode()

    print(f"Loaded {len(crash_gdf)} geocoded crashes")
    stats = ingestion.get_stats()
    print(f"Date range: {stats['date_range']['start'][:10]} to {stats['date_range']['end'][:10]}")
    print(f"Total injured: {stats['total_injured']}, killed: {stats['total_killed']}")

    # ===========================================
    # STEP 2: Crime Data Ingestion
    # ===========================================
    print("\n[2/8] INGESTING CRIME DATA (NYPD Complaints)...")
    print("-" * 40)

    crime_ingestion.fetch_crimes(
        limit=limit,
        year_start=year_start,
        use_cache=use_cache
    )
    crime_gdf = crime_ingestion.clean_and_geocode()

    print(f"Loaded {len(crime_gdf)} geocoded street crimes")
    crime_stats = crime_ingestion.get_stats()
    print(f"Date range: {crime_stats['date_range']['start'][:10]} to {crime_stats['date_range']['end'][:10]}")
    print(f"Crime types: {len(crime_stats['by_crime_type'])} categories")
    for crime_type, count in list(crime_stats['by_crime_type'].items())[:5]:
        print(f"  {crime_type}: {count}")

    # ===========================================
    # STEP 3: H3 Grid Risk Calculation (Crashes)
    # ===========================================
    print("\n[3/8] CALCULATING CRASH GRID RISK...")
    print("-" * 40)

    crash_gdf = grid_calc.assign_h3_cells(crash_gdf)
    grid_calc.calculate_cell_risk(crash_gdf, time_weighted=True)
    grid_calc.apply_spatial_smoothing()
    grid_gdf = grid_calc.create_grid_geodataframe()

    print(f"Created {len(grid_gdf)} crash H3 cells at resolution {h3_resolution}")
    high_risk = grid_calc.get_high_risk_cells(threshold=60)
    print(f"High crash risk cells (score >= 60): {len(high_risk)}")

    # ===========================================
    # STEP 4: H3 Grid Risk Calculation (Crime)
    # ===========================================
    print("\n[4/8] CALCULATING CRIME GRID RISK...")
    print("-" * 40)

    crime_gdf = crime_calc.assign_h3_cells(crime_gdf)
    crime_grid_df = crime_calc.calculate_cell_crime_risk(crime_gdf, time_weighted=True)
    crime_time_df = crime_calc.calculate_crime_time_patterns(crime_gdf, h3_resolution)

    print(f"Created {len(crime_grid_df)} crime H3 cells")
    high_crime = crime_grid_df[crime_grid_df["crime_risk"] >= 60]
    print(f"High crime risk cells (score >= 60): {len(high_crime)}")

    # ===========================================
    # STEP 5: Road Segment Risk Mapping
    # ===========================================
    print("\n[5/8] MAPPING RISK TO ROAD SEGMENTS...")
    print("-" * 40)

    segment_mapper.aggregate_by_street(crash_gdf)
    segments_gdf = segment_mapper.create_segment_geometries()
    intersections_gdf = segment_mapper.create_intersection_risk(crash_gdf)

    print(f"Mapped risk to {len(segments_gdf)} street segments")
    print(f"Identified {len(intersections_gdf)} intersection hotspots")

    high_risk_streets = segment_mapper.get_high_risk_segments(threshold=60)
    print(f"High risk streets (score >= 60): {len(high_risk_streets)}")

    # ===========================================
    # STEP 6: Time-of-Day Analysis
    # ===========================================
    print("\n[6/8] ANALYZING TIME PATTERNS...")
    print("-" * 40)

    hourly_df = time_analyzer.calculate_hourly_risk(crash_gdf)
    period_df = time_analyzer.calculate_period_risk(crash_gdf)
    cell_time_df = time_analyzer.calculate_cell_time_risk(crash_gdf, h3_resolution)

    safest = time_analyzer.get_safest_times(top_n=1)
    dangerous = time_analyzer.get_peak_danger_times(top_n=1)

    if safest:
        print(f"Safest time: {safest[0]['time_period']} on {safest[0]['day_type']}")
    if dangerous:
        print(f"Most dangerous: {dangerous[0]['time_period']} on {dangerous[0]['day_type']}")

    print(f"Generated {len(cell_time_df)} crash cell-time combinations")
    print(f"Generated {len(crime_time_df)} crime cell-time combinations")

    # ===========================================
    # STEP 7: Blend Crash + Crime Risk
    # ===========================================
    print("\n[7/8] BLENDING CRASH + CRIME RISK...")
    print("-" * 40)

    crash_grid_df = grid_calc.grid_data
    combined_grid, combined_time = CrimeRiskCalculator.blend_risks(
        crash_grid_df, crime_grid_df, cell_time_df, crime_time_df
    )

    both_data = combined_grid[(combined_grid["risk_score"] > 0) & (combined_grid["crime_risk"] > 0)]
    print(f"Combined grid: {len(combined_grid)} total cells")
    print(f"Cells with BOTH crash + crime data: {len(both_data)}")
    print(f"Cells with crash data only: {len(combined_grid[combined_grid['risk_score'] > 0])}")
    print(f"Cells with crime data only: {len(combined_grid[combined_grid['crime_risk'] > 0])}")

    # ===========================================
    # STEP 8: Export All Formats
    # ===========================================
    print("\n[8/8] EXPORTING DATA...")
    print("-" * 40)

    exports = exporter.export_all(
        grid_gdf=grid_gdf,
        segments_gdf=segments_gdf,
        intersections_gdf=intersections_gdf,
        hourly_df=hourly_df,
        period_df=period_df,
        cell_time_df=cell_time_df,
        combined_grid_df=combined_grid,
        combined_time_df=combined_time
    )

    for name, path in exports.items():
        print(f"  {name}: {path}")

    # Validation
    print("\nGENERATING VALIDATION REPORT...")
    print("-" * 40)

    validator.data_quality_check(crash_gdf)
    validator.spatial_coverage_check(crash_gdf, grid_gdf)
    validator.risk_distribution_analysis(grid_gdf)
    validator.temporal_validation(hourly_df, period_df)
    validator.hotspot_analysis(grid_gdf, segments_gdf)
    validator.cross_validation_summary(crash_gdf)

    report = validator.generate_full_report()
    validator.export_report(f"{output_dir}/validation_report.json")

    print(f"\nData Quality Score: {report['summary']['data_quality_score']}/100")
    print(f"Model Confidence: {report['summary']['model_confidence']}/100")

    # ===========================================
    # SUMMARY
    # ===========================================
    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print(f"""
OUTPUT FILES FOR TEAM:
----------------------
For Routing Engine:
  - {output_dir}/routing_risk_api.json  <- PRIMARY: crash + crime risk with time modifiers
  - {output_dir}/grid_risk.json         <- Alternative: simple cell lookup

For Frontend:
  - {output_dir}/grid_risk.geojson      <- H3 hexagon heatmap
  - {output_dir}/segment_risk.geojson   <- Street segment lines
  - {output_dir}/intersection_risk.geojson <- High-risk intersections
  - {output_dir}/time_patterns.json     <- Time slider data

Quality Assurance:
  - {output_dir}/validation_report.json <- Data quality & model metrics

RISK MODEL:
  Walking: risk = crime_risk * 0.7 + crash_risk * 0.3
  Driving: risk = crash_risk * 0.9 + crime_risk * 0.1
""")

    return {
        "crash_gdf": crash_gdf,
        "crime_gdf": crime_gdf,
        "grid_gdf": grid_gdf,
        "segments_gdf": segments_gdf,
        "intersections_gdf": intersections_gdf,
        "exports": exports,
        "validation": report
    }


def main():
    parser = argparse.ArgumentParser(
        description="NYC Safe Routing - Geospatial Risk Analysis"
    )
    parser.add_argument(
        "--limit", type=int, default=50000,
        help="Max crash records to fetch (default: 50000)"
    )
    parser.add_argument(
        "--year", type=int, default=2023,
        help="Start year for data (default: 2023)"
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Force fresh data fetch (ignore cache)"
    )
    parser.add_argument(
        "--resolution", type=int, default=9,
        help="H3 grid resolution (default: 9)"
    )
    parser.add_argument(
        "--output", type=str, default="output",
        help="Output directory (default: output)"
    )

    args = parser.parse_args()

    run_pipeline(
        limit=args.limit,
        year_start=args.year,
        use_cache=not args.no_cache,
        h3_resolution=args.resolution,
        output_dir=args.output
    )


if __name__ == "__main__":
    main()
