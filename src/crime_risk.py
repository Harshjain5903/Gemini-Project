"""
Crime Risk Calculation Module
Calculates crime-based risk scores on H3 hexagonal grid,
then blends with crash risk for travel-mode-aware combined scores.
"""
import h3
import pandas as pd
import numpy as np
from typing import Optional


class CrimeRiskCalculator:
    """Calculate crime risk scores on H3 hexagonal grid"""

    def __init__(self, resolution: int = 9):
        self.resolution = resolution
        self.grid_data: Optional[pd.DataFrame] = None

    def assign_h3_cells(self, gdf):
        """Assign H3 cell ID to each crime point"""
        gdf = gdf.copy()
        gdf["h3_cell"] = gdf.apply(
            lambda row: h3.latlng_to_cell(
                row.geometry.y, row.geometry.x, self.resolution
            ),
            axis=1
        )
        return gdf

    def calculate_cell_crime_risk(self, gdf, time_weighted=True):
        """
        Aggregate crimes by H3 cell and calculate crime risk scores.

        Returns DataFrame with crime_risk per cell (0-100 scale).
        """
        if "h3_cell" not in gdf.columns:
            gdf = self.assign_h3_cells(gdf)

        df = gdf.copy()

        # Time decay: recent crimes weighted more
        if time_weighted and "crime_datetime" in df.columns:
            now = pd.Timestamp.now()
            days_ago = (now - df["crime_datetime"]).dt.days
            df["time_weight"] = np.where(
                days_ago < 180, 2.0,
                np.where(days_ago < 365, 1.5, 1.0)
            )
        else:
            df["time_weight"] = 1.0

        df["weighted_severity"] = df["severity"] * df["time_weight"]

        # Aggregate by cell
        cell_stats = df.groupby("h3_cell").agg(
            crime_count=("h3_cell", "count"),
            total_severity=("severity", "sum"),
            weighted_severity=("weighted_severity", "sum"),
            avg_severity=("severity", "mean"),
        ).reset_index()

        # Normalize to 0-100
        max_weighted = cell_stats["weighted_severity"].max()
        if max_weighted > 0:
            cell_stats["crime_risk"] = (
                cell_stats["weighted_severity"] / max_weighted * 100
            ).round(2)
        else:
            cell_stats["crime_risk"] = 0

        # Spatial smoothing: blend with neighbors
        # Build O(1) lookup dict instead of filtering DataFrame per row
        city_avg = cell_stats["crime_risk"].mean()
        risk_lookup = dict(zip(cell_stats["h3_cell"], cell_stats["crime_risk"]))

        def get_smoothed(row):
            cell = row["h3_cell"]
            own_risk = row["crime_risk"]
            neighbors = h3.grid_ring(cell, 1)
            neighbor_scores = [
                risk_lookup[n] for n in neighbors if n in risk_lookup
            ]
            if neighbor_scores:
                neighbor_avg = sum(neighbor_scores) / len(neighbor_scores)
                return round(own_risk * 0.7 + neighbor_avg * 0.3, 2)
            return round(own_risk * 0.7 + city_avg * 0.3, 2)

        cell_stats["smoothed_crime_risk"] = cell_stats.apply(get_smoothed, axis=1)

        self.grid_data = cell_stats
        return cell_stats

    def calculate_crime_time_patterns(self, gdf, h3_resolution=9):
        """
        Calculate crime risk by H3 cell AND time period.
        Same time periods as crash data for consistency.
        """
        TIME_PERIODS = {
            "night": (0, 6),
            "morning_rush": (6, 9),
            "midday": (9, 16),
            "evening_rush": (16, 19),
            "evening": (19, 24)
        }

        df = gdf.copy()

        if "h3_cell" not in df.columns:
            df["h3_cell"] = df.apply(
                lambda row: h3.latlng_to_cell(
                    row.geometry.y, row.geometry.x, h3_resolution
                ),
                axis=1
            )

        def get_period(hour):
            for period, (start, end) in TIME_PERIODS.items():
                if start <= hour < end:
                    return period
            return "night"

        df["time_period"] = df["hour"].apply(get_period)
        df["day_type"] = df["day_of_week"].apply(
            lambda d: "weekend" if d in [5, 6] else "weekday"
        )

        # Aggregate
        cell_time = df.groupby(["h3_cell", "time_period", "day_type"]).agg(
            crime_count=("h3_cell", "count"),
            total_severity=("severity", "sum"),
        ).reset_index()

        # Normalize within each cell
        cell_time["cell_max_severity"] = cell_time.groupby(
            "h3_cell"
        )["total_severity"].transform("max")

        cell_time["time_risk_score"] = np.where(
            cell_time["cell_max_severity"] > 0,
            (cell_time["total_severity"] / cell_time["cell_max_severity"] * 100).round(2),
            0
        )

        # Global risk score
        global_max = cell_time["total_severity"].quantile(0.99)
        if global_max > 0:
            cell_time["global_risk_score"] = (
                cell_time["total_severity"].clip(upper=global_max) / global_max * 100
            ).round(2)
        else:
            cell_time["global_risk_score"] = 0

        return cell_time

    @staticmethod
    def blend_risks(crash_grid_df, crime_grid_df, crash_time_df, crime_time_df):
        """
        Merge crash and crime risk into a single dataset with both scores.
        The routing engine will blend them based on travel mode.

        Returns:
            combined_grid: DataFrame with base_risk, crime_risk per cell
            combined_time: DataFrame with crash + crime time modifiers
        """
        # --- Grid-level merge ---
        crash_cols = crash_grid_df[["h3_cell", "risk_score", "smoothed_risk",
                                    "pedestrian_risk", "cyclist_risk",
                                    "crash_count", "total_severity"]].copy()
        crash_cols = crash_cols.rename(columns={
            "total_severity": "crash_severity"
        })

        crime_cols = crime_grid_df[["h3_cell", "crime_risk",
                                     "smoothed_crime_risk", "crime_count"]].copy()

        combined_grid = crash_cols.merge(crime_cols, on="h3_cell", how="outer")
        combined_grid = combined_grid.fillna(0)

        # --- Time-level merge ---
        # Crash time data
        ct = crash_time_df[["h3_cell", "time_period", "day_type",
                            "global_risk_score"]].copy()
        ct = ct.rename(columns={"global_risk_score": "crash_time_score"})

        # Crime time data
        crt = crime_time_df[["h3_cell", "time_period", "day_type",
                             "global_risk_score"]].copy()
        crt = crt.rename(columns={"global_risk_score": "crime_time_score"})

        combined_time = ct.merge(
            crt, on=["h3_cell", "time_period", "day_type"], how="outer"
        )
        combined_time = combined_time.fillna(0)

        return combined_grid, combined_time
