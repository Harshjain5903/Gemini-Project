"""
Time-of-Day Risk Pattern Analysis
Analyzes how crash risk varies by hour, day, and season
"""
import pandas as pd
import geopandas as gpd
import numpy as np
from typing import Optional, Dict, List
import h3


class TimePatternAnalyzer:
    """Analyze temporal patterns in crash data"""

    # Time period definitions
    TIME_PERIODS = {
        "night": (0, 6),        # 12am - 6am
        "morning_rush": (6, 9), # 6am - 9am
        "midday": (9, 16),      # 9am - 4pm
        "evening_rush": (16, 19),# 4pm - 7pm
        "evening": (19, 24)     # 7pm - 12am
    }

    DAY_TYPES = {
        "weekday": [0, 1, 2, 3, 4],  # Mon-Fri
        "weekend": [5, 6]            # Sat-Sun
    }

    def __init__(self):
        self.hourly_risk: Optional[pd.DataFrame] = None
        self.period_risk: Optional[pd.DataFrame] = None
        self.cell_time_risk: Optional[pd.DataFrame] = None

    def calculate_hourly_risk(self, gdf: gpd.GeoDataFrame) -> pd.DataFrame:
        """
        Calculate risk multipliers by hour of day

        Args:
            gdf: GeoDataFrame with hour column

        Returns:
            DataFrame with hourly risk multipliers
        """
        df = gdf.copy()

        # Aggregate by hour
        hourly = df.groupby("hour").agg(
            crash_count=("hour", "count"),
            total_severity=("severity", "sum"),
            avg_severity=("severity", "mean")
        ).reset_index()

        # Calculate baseline (average hourly crashes)
        baseline = hourly["crash_count"].mean()

        # Risk multiplier relative to baseline
        hourly["risk_multiplier"] = (hourly["crash_count"] / baseline).round(3)

        # Normalized 0-100 score
        hourly["risk_score"] = (
            hourly["risk_multiplier"] /
            hourly["risk_multiplier"].max() * 100
        ).round(2)

        self.hourly_risk = hourly
        return hourly

    def calculate_period_risk(self, gdf: gpd.GeoDataFrame) -> pd.DataFrame:
        """
        Calculate risk by time period and day type

        Args:
            gdf: GeoDataFrame with hour and day_of_week columns

        Returns:
            DataFrame with period/day risk combinations
        """
        df = gdf.copy()

        # Assign time period
        def get_period(hour):
            for period, (start, end) in self.TIME_PERIODS.items():
                if start <= hour < end:
                    return period
            return "night"

        df["time_period"] = df["hour"].apply(get_period)

        # Assign day type
        df["day_type"] = df["day_of_week"].apply(
            lambda d: "weekend" if d in self.DAY_TYPES["weekend"] else "weekday"
        )

        # Aggregate by period + day type
        period_stats = df.groupby(["time_period", "day_type"]).agg(
            crash_count=("time_period", "count"),
            total_severity=("severity", "sum"),
            avg_severity=("severity", "mean"),
            pedestrian_involved=("number_of_pedestrians_injured", lambda x: (x > 0).sum()),
            cyclist_involved=("number_of_cyclist_injured", lambda x: (x > 0).sum())
        ).reset_index()

        # Risk multiplier
        baseline = period_stats["crash_count"].mean()
        period_stats["risk_multiplier"] = (period_stats["crash_count"] / baseline).round(3)

        self.period_risk = period_stats
        return period_stats

    def calculate_cell_time_risk(
        self,
        gdf: gpd.GeoDataFrame,
        h3_resolution: int = 9
    ) -> pd.DataFrame:
        """
        Calculate risk by H3 cell AND time period
        This is the key output for dynamic risk routing

        Args:
            gdf: GeoDataFrame with crashes
            h3_resolution: H3 grid resolution

        Returns:
            DataFrame with cell-time risk combinations
        """
        df = gdf.copy()

        # Assign H3 cells
        if "h3_cell" not in df.columns:
            df["h3_cell"] = df.apply(
                lambda row: h3.latlng_to_cell(
                    row.geometry.y,
                    row.geometry.x,
                    h3_resolution
                ),
                axis=1
            )

        # Assign time period
        def get_period(hour):
            for period, (start, end) in self.TIME_PERIODS.items():
                if start <= hour < end:
                    return period
            return "night"

        df["time_period"] = df["hour"].apply(get_period)
        df["day_type"] = df["day_of_week"].apply(
            lambda d: "weekend" if d in self.DAY_TYPES["weekend"] else "weekday"
        )

        # Create compound key
        df["cell_time_key"] = (
            df["h3_cell"] + "_" +
            df["time_period"] + "_" +
            df["day_type"]
        )

        # Aggregate
        cell_time = df.groupby(["h3_cell", "time_period", "day_type"]).agg(
            crash_count=("h3_cell", "count"),
            total_severity=("severity", "sum"),
            avg_severity=("severity", "mean")
        ).reset_index()

        # Normalize risk within each cell (shows relative danger by time)
        cell_time["cell_max_severity"] = cell_time.groupby("h3_cell")["total_severity"].transform("max")
        cell_time["time_risk_score"] = np.where(
            cell_time["cell_max_severity"] > 0,
            (cell_time["total_severity"] / cell_time["cell_max_severity"] * 100).round(2),
            0
        )

        # Global risk score (comparable across cells)
        global_max = cell_time["total_severity"].quantile(0.99)
        cell_time["global_risk_score"] = (
            cell_time["total_severity"].clip(upper=global_max) / global_max * 100
        ).round(2)

        self.cell_time_risk = cell_time
        return cell_time

    def get_risk_for_time(
        self,
        hour: int,
        is_weekend: bool = False
    ) -> Dict[str, float]:
        """
        Get risk multiplier for a specific time

        Args:
            hour: Hour of day (0-23)
            is_weekend: Whether it's a weekend

        Returns:
            Dict with risk multipliers
        """
        result = {"hour": hour, "is_weekend": is_weekend}

        # Hourly multiplier
        if self.hourly_risk is not None:
            hourly = self.hourly_risk[self.hourly_risk["hour"] == hour]
            if len(hourly) > 0:
                result["hourly_multiplier"] = float(hourly["risk_multiplier"].iloc[0])

        # Period multiplier
        if self.period_risk is not None:
            period = "night"
            for p, (start, end) in self.TIME_PERIODS.items():
                if start <= hour < end:
                    period = p
                    break

            day_type = "weekend" if is_weekend else "weekday"
            period_data = self.period_risk[
                (self.period_risk["time_period"] == period) &
                (self.period_risk["day_type"] == day_type)
            ]

            if len(period_data) > 0:
                result["period_multiplier"] = float(period_data["risk_multiplier"].iloc[0])
                result["time_period"] = period
                result["day_type"] = day_type

        # Combined multiplier
        if "hourly_multiplier" in result and "period_multiplier" in result:
            result["combined_multiplier"] = round(
                (result["hourly_multiplier"] + result["period_multiplier"]) / 2,
                3
            )

        return result

    def get_cell_risk_at_time(
        self,
        h3_cell: str,
        time_period: str,
        day_type: str = "weekday"
    ) -> Dict[str, float]:
        """
        Get specific cell's risk for a time period

        Args:
            h3_cell: H3 cell ID
            time_period: One of TIME_PERIODS keys
            day_type: "weekday" or "weekend"

        Returns:
            Dict with cell-time risk data
        """
        if self.cell_time_risk is None:
            return {"found": False}

        match = self.cell_time_risk[
            (self.cell_time_risk["h3_cell"] == h3_cell) &
            (self.cell_time_risk["time_period"] == time_period) &
            (self.cell_time_risk["day_type"] == day_type)
        ]

        if len(match) == 0:
            return {
                "found": False,
                "h3_cell": h3_cell,
                "time_period": time_period,
                "day_type": day_type
            }

        row = match.iloc[0]
        return {
            "found": True,
            "h3_cell": h3_cell,
            "time_period": time_period,
            "day_type": day_type,
            "crash_count": int(row["crash_count"]),
            "time_risk_score": float(row["time_risk_score"]),
            "global_risk_score": float(row["global_risk_score"])
        }

    def get_safest_times(self, top_n: int = 3) -> List[Dict]:
        """Get the safest time periods overall"""
        if self.period_risk is None:
            return []

        safest = self.period_risk.nsmallest(top_n, "risk_multiplier")
        return safest.to_dict("records")

    def get_peak_danger_times(self, top_n: int = 3) -> List[Dict]:
        """Get the most dangerous time periods"""
        if self.period_risk is None:
            return []

        dangerous = self.period_risk.nlargest(top_n, "risk_multiplier")
        return dangerous.to_dict("records")
