"""
Validation and Statistics Module
Provides offline validation metrics for the risk model
"""
import pandas as pd
import geopandas as gpd
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import json


class ValidationStats:
    """Generate validation statistics and model quality metrics"""

    def __init__(self):
        self.validation_results: Dict = {}

    def data_quality_check(self, gdf: gpd.GeoDataFrame) -> Dict:
        """
        Check data quality and completeness

        Args:
            gdf: Raw crash GeoDataFrame

        Returns:
            Dict with quality metrics
        """
        total = len(gdf)

        quality = {
            "total_records": total,
            "geocoded_pct": round((gdf.geometry.notna().sum() / total) * 100, 2) if total > 0 else 0,
            "has_time_pct": round((gdf["crash_datetime"].notna().sum() / total) * 100, 2) if ("crash_datetime" in gdf and total > 0) else 0,
            "has_street_pct": round((gdf["on_street_name"].notna().sum() / total) * 100, 2) if ("on_street_name" in gdf and total > 0) else 0,
            "has_severity_pct": round((gdf["severity"].notna().sum() / total) * 100, 2) if ("severity" in gdf and total > 0) else 0,
            "date_range": {
                "min": str(gdf["crash_datetime"].min()) if "crash_datetime" in gdf else None,
                "max": str(gdf["crash_datetime"].max()) if "crash_datetime" in gdf else None
            }
        }

        self.validation_results["data_quality"] = quality
        return quality

    def spatial_coverage_check(
        self,
        gdf: gpd.GeoDataFrame,
        grid_gdf: gpd.GeoDataFrame
    ) -> Dict:
        """
        Validate spatial coverage of the grid

        Args:
            gdf: Crash points
            grid_gdf: H3 grid with risk scores

        Returns:
            Dict with coverage metrics
        """
        # Bounding box coverage
        crash_bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]
        grid_bounds = grid_gdf.total_bounds

        coverage = {
            "crash_bbox": {
                "min_lng": round(crash_bounds[0], 4),
                "min_lat": round(crash_bounds[1], 4),
                "max_lng": round(crash_bounds[2], 4),
                "max_lat": round(crash_bounds[3], 4)
            },
            "grid_cells_count": len(grid_gdf),
            "cells_with_crashes": int((grid_gdf["crash_count"] > 0).sum()),
            "avg_crashes_per_cell": round(grid_gdf["crash_count"].mean(), 2),
            "max_crashes_in_cell": int(grid_gdf["crash_count"].max()),
            "total_crashes_covered": int(grid_gdf["crash_count"].sum())
        }

        self.validation_results["spatial_coverage"] = coverage
        return coverage

    def risk_distribution_analysis(self, grid_gdf: gpd.GeoDataFrame) -> Dict:
        """
        Analyze the distribution of risk scores

        Args:
            grid_gdf: Grid with risk scores

        Returns:
            Dict with distribution statistics
        """
        risk_scores = grid_gdf["risk_score"]

        distribution = {
            "mean": round(risk_scores.mean(), 2),
            "median": round(risk_scores.median(), 2),
            "std": round(risk_scores.std(), 2),
            "min": round(risk_scores.min(), 2),
            "max": round(risk_scores.max(), 2),
            "percentiles": {
                "p10": round(risk_scores.quantile(0.10), 2),
                "p25": round(risk_scores.quantile(0.25), 2),
                "p50": round(risk_scores.quantile(0.50), 2),
                "p75": round(risk_scores.quantile(0.75), 2),
                "p90": round(risk_scores.quantile(0.90), 2),
                "p99": round(risk_scores.quantile(0.99), 2)
            },
            "category_counts": grid_gdf["risk_category"].value_counts().to_dict() if "risk_category" in grid_gdf else {}
        }

        self.validation_results["risk_distribution"] = distribution
        return distribution

    def temporal_validation(
        self,
        hourly_df: pd.DataFrame,
        period_df: pd.DataFrame
    ) -> Dict:
        """
        Validate temporal patterns make sense

        Args:
            hourly_df: Hourly risk data
            period_df: Period risk data

        Returns:
            Dict with temporal validation metrics
        """
        temporal = {}

        if hourly_df is not None:
            peak_hour = hourly_df.loc[hourly_df["crash_count"].idxmax()]
            low_hour = hourly_df.loc[hourly_df["crash_count"].idxmin()]

            temporal["hourly"] = {
                "peak_hour": int(peak_hour["hour"]),
                "peak_hour_crashes": int(peak_hour["crash_count"]),
                "low_hour": int(low_hour["hour"]),
                "low_hour_crashes": int(low_hour["crash_count"]),
                "peak_to_low_ratio": round(peak_hour["crash_count"] / max(low_hour["crash_count"], 1), 2)
            }

        if period_df is not None:
            temporal["periods"] = period_df.to_dict("records")

            # Sanity checks
            temporal["sanity_checks"] = {
                "rush_hours_higher": self._check_rush_hours(period_df),
                "night_lowest": self._check_night_low(period_df)
            }

        self.validation_results["temporal"] = temporal
        return temporal

    def _check_rush_hours(self, period_df: pd.DataFrame) -> bool:
        """Check if rush hours have higher risk than midday"""
        rush_periods = ["morning_rush", "evening_rush"]
        rush_avg = period_df[period_df["time_period"].isin(rush_periods)]["risk_multiplier"].mean()
        midday = period_df[period_df["time_period"] == "midday"]["risk_multiplier"].mean()
        return bool(rush_avg > midday)

    def _check_night_low(self, period_df: pd.DataFrame) -> bool:
        """Check if night has lower crash counts (less traffic)"""
        night = period_df[period_df["time_period"] == "night"]["crash_count"].mean()
        overall_avg = period_df["crash_count"].mean()
        return bool(night < overall_avg)

    def hotspot_analysis(
        self,
        grid_gdf: gpd.GeoDataFrame,
        segments_gdf: gpd.GeoDataFrame,
        top_n: int = 10
    ) -> Dict:
        """
        Identify and validate top hotspots

        Args:
            grid_gdf: Grid risk data
            segments_gdf: Segment risk data
            top_n: Number of top hotspots to return

        Returns:
            Dict with hotspot information
        """
        hotspots = {}

        # Top risk cells
        top_cells = grid_gdf.nlargest(top_n, "risk_score")[
            ["h3_cell", "risk_score", "crash_count", "center_lat", "center_lng"]
        ].to_dict("records")

        hotspots["top_cells"] = top_cells

        # Top risk streets
        if segments_gdf is not None and len(segments_gdf) > 0:
            top_streets = segments_gdf.nlargest(top_n, "risk_score")[
                ["street_name", "risk_score", "crash_count", "crashes_per_km"]
            ].to_dict("records")
            hotspots["top_streets"] = top_streets

        self.validation_results["hotspots"] = hotspots
        return hotspots

    def cross_validation_summary(
        self,
        gdf: gpd.GeoDataFrame,
        test_fraction: float = 0.2
    ) -> Dict:
        """
        Simple temporal cross-validation: train on older data, test on newer

        Args:
            gdf: Full crash dataset
            test_fraction: Fraction of most recent data to hold out

        Returns:
            Dict with cross-validation metrics
        """
        if "crash_datetime" not in gdf.columns:
            return {"error": "No datetime column for cross-validation"}

        sorted_gdf = gdf.sort_values("crash_datetime")
        split_idx = int(len(sorted_gdf) * (1 - test_fraction))

        train = sorted_gdf.iloc[:split_idx]
        test = sorted_gdf.iloc[split_idx:]

        # Get top risk cells from training data
        train_cells = train.groupby("h3_cell").size().nlargest(100).index.tolist() if "h3_cell" in train.columns else []

        # Check how many test crashes fall in high-risk training cells
        if "h3_cell" in test.columns and len(train_cells) > 0:
            test_in_train_hotspots = test["h3_cell"].isin(train_cells).sum()
            prediction_rate = test_in_train_hotspots / len(test)
        else:
            prediction_rate = 0

        cv_results = {
            "train_size": len(train),
            "test_size": len(test),
            "train_date_range": {
                "start": str(train["crash_datetime"].min()),
                "end": str(train["crash_datetime"].max())
            },
            "test_date_range": {
                "start": str(test["crash_datetime"].min()),
                "end": str(test["crash_datetime"].max())
            },
            "prediction_accuracy": {
                "top_100_cells_capture_rate": round(prediction_rate * 100, 2),
                "interpretation": "Percentage of test crashes in training hotspots"
            }
        }

        self.validation_results["cross_validation"] = cv_results
        return cv_results

    def generate_full_report(self) -> Dict:
        """
        Generate complete validation report

        Returns:
            Dict with all validation results
        """
        report = {
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "data_quality_score": self._calculate_quality_score(),
                "model_confidence": self._calculate_confidence_score()
            },
            "details": self.validation_results
        }
        return report

    def _calculate_quality_score(self) -> float:
        """Calculate overall data quality score (0-100)"""
        if "data_quality" not in self.validation_results:
            return 0

        dq = self.validation_results["data_quality"]
        score = (
            dq.get("geocoded_pct", 0) * 0.4 +
            dq.get("has_time_pct", 0) * 0.3 +
            dq.get("has_street_pct", 0) * 0.2 +
            dq.get("has_severity_pct", 0) * 0.1
        )
        return round(score, 2)

    def _calculate_confidence_score(self) -> float:
        """Calculate model confidence based on validation results"""
        score = 50  # Base score

        # Boost for temporal sanity
        if "temporal" in self.validation_results:
            checks = self.validation_results["temporal"].get("sanity_checks", {})
            if checks.get("rush_hours_higher"):
                score += 15
            if checks.get("night_lowest"):
                score += 10

        # Boost for good coverage
        if "spatial_coverage" in self.validation_results:
            coverage = self.validation_results["spatial_coverage"]
            if coverage.get("cells_with_crashes", 0) > 1000:
                score += 15

        # Boost for cross-validation
        if "cross_validation" in self.validation_results:
            cv = self.validation_results["cross_validation"]
            capture_rate = cv.get("prediction_accuracy", {}).get("top_100_cells_capture_rate", 0)
            if capture_rate > 30:
                score += 10

        return min(score, 100)

    def export_report(self, output_path: str = "output/validation_report.json") -> str:
        """Export validation report to JSON"""
        report = self.generate_full_report()

        with open(output_path, "w") as f:
            json.dump(report, f, indent=2, default=str)

        print(f"Validation report exported to: {output_path}")
        return output_path
