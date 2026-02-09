"""
H3 Grid-Based Risk Calculation Module
Uses Uber's H3 hexagonal grid for spatial aggregation
"""
import h3
import pandas as pd
import geopandas as gpd
import numpy as np
from shapely.geometry import Polygon
from typing import Optional


class GridRiskCalculator:
    """Calculate risk scores on H3 hexagonal grid"""

    # H3 Resolution guide:
    # 8 = ~0.74 km² (neighborhood blocks)
    # 9 = ~0.10 km² (street level) - RECOMMENDED
    # 10 = ~0.015 km² (intersection level)

    def __init__(self, resolution: int = 9):
        self.resolution = resolution
        self.grid_data: Optional[pd.DataFrame] = None
        self.grid_geo: Optional[gpd.GeoDataFrame] = None

    def assign_h3_cells(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """
        Assign H3 cell ID to each crash point

        Args:
            gdf: GeoDataFrame with crash points

        Returns:
            GeoDataFrame with h3_cell column added
        """
        gdf = gdf.copy()
        gdf["h3_cell"] = gdf.apply(
            lambda row: h3.latlng_to_cell(
                row.geometry.y,
                row.geometry.x,
                self.resolution
            ),
            axis=1
        )
        return gdf

    def calculate_cell_risk(
        self,
        gdf: gpd.GeoDataFrame,
        time_weighted: bool = True
    ) -> pd.DataFrame:
        """
        Aggregate crashes by H3 cell and calculate risk scores

        Args:
            gdf: GeoDataFrame with h3_cell assigned
            time_weighted: Apply time decay (recent crashes weighted more)

        Returns:
            DataFrame with risk scores per cell
        """
        if "h3_cell" not in gdf.columns:
            gdf = self.assign_h3_cells(gdf)

        df = gdf.copy()

        # Time decay: crashes in last 6 months weighted 2x, last year 1.5x
        if time_weighted and "crash_datetime" in df.columns:
            now = pd.Timestamp.now()
            days_ago = (now - df["crash_datetime"]).dt.days
            df["time_weight"] = np.where(
                days_ago < 180, 2.0,
                np.where(days_ago < 365, 1.5, 1.0)
            )
        else:
            df["time_weight"] = 1.0

        df["weighted_severity"] = df["severity"] * df["time_weight"]

        # Aggregate by cell
        cell_stats = df.groupby("h3_cell").agg(
            crash_count=("h3_cell", "count"),
            total_severity=("severity", "sum"),
            weighted_severity=("weighted_severity", "sum"),
            avg_severity=("severity", "mean"),
            total_injured=("number_of_persons_injured", "sum"),
            total_killed=("number_of_persons_killed", "sum"),
            pedestrian_injured=("number_of_pedestrians_injured", "sum"),
            pedestrian_killed=("number_of_pedestrians_killed", "sum"),
            cyclist_injured=("number_of_cyclist_injured", "sum"),
            cyclist_killed=("number_of_cyclist_killed", "sum"),
            pedestrian_crashes=("number_of_pedestrians_injured", lambda x: (x > 0).sum()),
            cyclist_crashes=("number_of_cyclist_injured", lambda x: (x > 0).sum())
        ).reset_index()

        # Normalize risk score (0-100 scale)
        max_weighted = cell_stats["weighted_severity"].max()
        if max_weighted > 0:
            cell_stats["risk_score"] = (
                cell_stats["weighted_severity"] / max_weighted * 100
            ).round(2)
        else:
            cell_stats["risk_score"] = 0

        # Pedestrian-specific risk score (for walking routes)
        cell_stats["pedestrian_severity"] = (
            cell_stats["pedestrian_injured"] * 2 +
            cell_stats["pedestrian_killed"] * 10
        )
        max_ped = cell_stats["pedestrian_severity"].max()
        cell_stats["pedestrian_risk"] = (
            (cell_stats["pedestrian_severity"] / max_ped * 100).round(2)
            if max_ped > 0 else 0
        )

        # Cyclist-specific risk score (for bike routes)
        cell_stats["cyclist_severity"] = (
            cell_stats["cyclist_injured"] * 2 +
            cell_stats["cyclist_killed"] * 10
        )
        max_cyc = cell_stats["cyclist_severity"].max()
        cell_stats["cyclist_risk"] = (
            (cell_stats["cyclist_severity"] / max_cyc * 100).round(2)
            if max_cyc > 0 else 0
        )

        # Risk category
        cell_stats["risk_category"] = pd.cut(
            cell_stats["risk_score"],
            bins=[0, 20, 40, 60, 80, 100],
            labels=["very_low", "low", "medium", "high", "critical"],
            include_lowest=True
        )

        self.grid_data = cell_stats
        return cell_stats

    def create_grid_geodataframe(self) -> gpd.GeoDataFrame:
        """
        Create GeoDataFrame with H3 cell polygons for visualization

        Returns:
            GeoDataFrame with hexagon geometries
        """
        if self.grid_data is None:
            raise ValueError("No grid data. Call calculate_cell_risk() first.")

        def h3_to_polygon(h3_cell):
            """Convert H3 cell to Shapely polygon"""
            boundary = h3.cell_to_boundary(h3_cell)
            # h3 returns (lat, lng), shapely needs (lng, lat)
            coords = [(lng, lat) for lat, lng in boundary]
            return Polygon(coords)

        grid_with_geo = self.grid_data.copy()
        grid_with_geo["geometry"] = grid_with_geo["h3_cell"].apply(h3_to_polygon)
        self.grid_geo = gpd.GeoDataFrame(
            grid_with_geo,
            geometry="geometry",
            crs="EPSG:4326"
        )

        # Add cell center for labeling
        self.grid_geo["center_lat"] = self.grid_geo["h3_cell"].apply(
            lambda x: h3.cell_to_latlng(x)[0]
        )
        self.grid_geo["center_lng"] = self.grid_geo["h3_cell"].apply(
            lambda x: h3.cell_to_latlng(x)[1]
        )

        return self.grid_geo

    def get_neighbors_risk(self, h3_cell: str) -> dict:
        """
        Get risk context including neighboring cells

        Args:
            h3_cell: H3 cell ID

        Returns:
            Dict with cell risk and neighbor average
        """
        if self.grid_data is None:
            return {}

        neighbors = h3.grid_ring(h3_cell, 1)
        neighbor_data = self.grid_data[
            self.grid_data["h3_cell"].isin(neighbors)
        ]

        cell_data = self.grid_data[self.grid_data["h3_cell"] == h3_cell]

        return {
            "cell_risk": float(cell_data["risk_score"].iloc[0]) if len(cell_data) > 0 else 0,
            "neighbor_avg_risk": float(neighbor_data["risk_score"].mean()) if len(neighbor_data) > 0 else 0,
            "neighbor_count": len(neighbor_data)
        }

    def get_high_risk_cells(self, threshold: float = 60) -> gpd.GeoDataFrame:
        """Get cells with risk score above threshold"""
        if self.grid_geo is None:
            self.create_grid_geodataframe()
        return self.grid_geo[self.grid_geo["risk_score"] >= threshold]

    def apply_spatial_smoothing(self, fallback_pct: float = 0.3) -> pd.DataFrame:
        """
        Apply spatial smoothing: unknown cells inherit risk from neighbors.
        This prevents "no data = safe" assumption.

        Args:
            fallback_pct: For cells with no neighbors, use this % of city avg

        Returns:
            Updated grid_data with smoothed_risk column
        """
        if self.grid_data is None:
            raise ValueError("No grid data. Call calculate_cell_risk() first.")

        city_avg = self.grid_data["risk_score"].mean()

        # Build O(1) lookup dict instead of filtering DataFrame per row
        risk_lookup = dict(zip(
            self.grid_data["h3_cell"],
            self.grid_data["risk_score"]
        ))

        def get_smoothed_risk(row):
            cell = row["h3_cell"]
            own_risk = row["risk_score"]

            neighbors = h3.grid_ring(cell, 1)
            neighbor_scores = [
                risk_lookup[n] for n in neighbors if n in risk_lookup
            ]

            if neighbor_scores:
                neighbor_avg = sum(neighbor_scores) / len(neighbor_scores)
                return round(own_risk * 0.7 + neighbor_avg * 0.3, 2)
            else:
                return round(own_risk * 0.7 + city_avg * fallback_pct, 2)

        self.grid_data["smoothed_risk"] = self.grid_data.apply(
            get_smoothed_risk, axis=1
        )

        return self.grid_data
