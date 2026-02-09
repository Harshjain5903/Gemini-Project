"""
Road Segment Risk Mapping Module
Maps crashes to street segments for routing integration
"""
import pandas as pd
import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Point, box
from shapely.ops import linemerge
from typing import Optional, Tuple
from collections import defaultdict


class SegmentRiskMapper:
    """Map crash risk to road segments for routing engine integration"""

    def __init__(self):
        self.street_data: Optional[pd.DataFrame] = None
        self.segment_geo: Optional[gpd.GeoDataFrame] = None

    def aggregate_by_street(self, gdf: gpd.GeoDataFrame) -> pd.DataFrame:
        """
        Aggregate crash data by street name

        Args:
            gdf: GeoDataFrame with crash points

        Returns:
            DataFrame with per-street statistics
        """
        df = gdf.copy()

        # Clean street names
        df["street_clean"] = df["on_street_name"].fillna("UNKNOWN").str.upper().str.strip()
        df = df[df["street_clean"] != "UNKNOWN"]

        # Aggregate by street
        street_stats = df.groupby("street_clean").agg(
            crash_count=("street_clean", "count"),
            total_severity=("severity", "sum"),
            avg_severity=("severity", "mean"),
            total_injured=("number_of_persons_injured", "sum"),
            total_killed=("number_of_persons_killed", "sum"),
            min_lat=("geometry", lambda x: x.apply(lambda g: g.y).min()),
            max_lat=("geometry", lambda x: x.apply(lambda g: g.y).max()),
            min_lng=("geometry", lambda x: x.apply(lambda g: g.x).min()),
            max_lng=("geometry", lambda x: x.apply(lambda g: g.x).max()),
            # Get list of crash points for segment creation
            crash_points=("geometry", list)
        ).reset_index()

        # Calculate segment length (approximate in meters)
        street_stats["approx_length_m"] = street_stats.apply(
            lambda row: self._haversine_distance(
                row["min_lat"], row["min_lng"],
                row["max_lat"], row["max_lng"]
            ),
            axis=1
        )

        # Risk per km (normalized)
        street_stats["crashes_per_km"] = np.where(
            street_stats["approx_length_m"] > 100,
            street_stats["crash_count"] / (street_stats["approx_length_m"] / 1000),
            street_stats["crash_count"]  # For very short segments
        )

        # Normalize risk score (0-100)
        max_rate = street_stats["crashes_per_km"].quantile(0.99)
        street_stats["risk_score"] = (
            street_stats["crashes_per_km"].clip(upper=max_rate) / max_rate * 100
        ).round(2)

        self.street_data = street_stats
        return street_stats

    def _haversine_distance(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        """Calculate distance between two points in meters"""
        R = 6371000  # Earth radius in meters

        phi1 = np.radians(lat1)
        phi2 = np.radians(lat2)
        delta_phi = np.radians(lat2 - lat1)
        delta_lambda = np.radians(lon2 - lon1)

        a = (np.sin(delta_phi / 2) ** 2 +
             np.cos(phi1) * np.cos(phi2) * np.sin(delta_lambda / 2) ** 2)
        c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))

        return R * c

    def create_segment_geometries(self) -> gpd.GeoDataFrame:
        """
        Create line geometries for street segments based on crash clusters

        Returns:
            GeoDataFrame with segment LineStrings
        """
        if self.street_data is None:
            raise ValueError("No street data. Call aggregate_by_street() first.")

        segments = []

        for _, row in self.street_data.iterrows():
            crash_points = row["crash_points"]

            if len(crash_points) >= 2:
                # Sort points by longitude to create a rough line
                sorted_points = sorted(crash_points, key=lambda p: (p.x, p.y))
                coords = [(p.x, p.y) for p in sorted_points]

                # Simplify to start and end for clean segment
                if len(coords) > 2:
                    # Use convex hull endpoints for better representation
                    line = LineString(coords)
                    simplified = line.simplify(0.0001)
                    geometry = simplified
                else:
                    geometry = LineString(coords)
            else:
                # Single point - create small buffer line
                p = crash_points[0]
                geometry = LineString([(p.x - 0.0001, p.y), (p.x + 0.0001, p.y)])

            segments.append({
                "street_name": row["street_clean"],
                "crash_count": row["crash_count"],
                "risk_score": row["risk_score"],
                "total_severity": row["total_severity"],
                "total_injured": row["total_injured"],
                "total_killed": row["total_killed"],
                "crashes_per_km": round(row["crashes_per_km"], 2),
                "geometry": geometry
            })

        self.segment_geo = gpd.GeoDataFrame(segments, crs="EPSG:4326")
        return self.segment_geo

    def create_intersection_risk(
        self,
        gdf: gpd.GeoDataFrame,
        buffer_meters: float = 30
    ) -> gpd.GeoDataFrame:
        """
        Calculate risk at intersections (cross street combinations)

        Args:
            gdf: GeoDataFrame with crash points
            buffer_meters: Radius to cluster intersection crashes

        Returns:
            GeoDataFrame with intersection points and risk scores
        """
        df = gdf.copy()

        # Filter to crashes with cross street info
        df = df[df["cross_street_name"].notna()].copy()
        df["on_street"] = df["on_street_name"].fillna("").str.upper().str.strip()
        df["cross_street"] = df["cross_street_name"].str.upper().str.strip()

        # Create canonical intersection ID (sorted street names)
        df["intersection_id"] = df.apply(
            lambda row: tuple(sorted([row["on_street"], row["cross_street"]])),
            axis=1
        )

        # Aggregate by intersection
        intersection_stats = df.groupby("intersection_id").agg(
            crash_count=("intersection_id", "count"),
            total_severity=("severity", "sum"),
            total_injured=("number_of_persons_injured", "sum"),
            total_killed=("number_of_persons_killed", "sum"),
            center_lat=("geometry", lambda x: x.apply(lambda g: g.y).mean()),
            center_lng=("geometry", lambda x: x.apply(lambda g: g.x).mean())
        ).reset_index()

        # Unpack intersection tuple to strings
        intersection_stats["street_1"] = intersection_stats["intersection_id"].apply(lambda x: x[0])
        intersection_stats["street_2"] = intersection_stats["intersection_id"].apply(lambda x: x[1])

        # Normalize risk
        max_severity = intersection_stats["total_severity"].quantile(0.99)
        intersection_stats["risk_score"] = (
            intersection_stats["total_severity"].clip(upper=max_severity) / max_severity * 100
        ).round(2)

        # Create point geometries
        geometry = [
            Point(lng, lat)
            for lng, lat in zip(
                intersection_stats["center_lng"],
                intersection_stats["center_lat"]
            )
        ]

        intersection_gdf = gpd.GeoDataFrame(
            intersection_stats.drop(columns=["intersection_id"]),
            geometry=geometry,
            crs="EPSG:4326"
        )

        return intersection_gdf

    def get_segment_for_routing(
        self,
        street_name: str
    ) -> dict:
        """
        Get segment risk data formatted for routing engine

        Args:
            street_name: Street name to look up

        Returns:
            Dict with risk data for routing cost function
        """
        if self.street_data is None:
            return {}

        street = street_name.upper().strip()
        match = self.street_data[self.street_data["street_clean"] == street]

        if len(match) == 0:
            return {"risk_score": 0, "found": False}

        row = match.iloc[0]
        return {
            "found": True,
            "risk_score": float(row["risk_score"]),
            "crash_count": int(row["crash_count"]),
            "crashes_per_km": float(row["crashes_per_km"]),
            "severity_total": float(row["total_severity"])
        }

    def get_high_risk_segments(self, threshold: float = 60) -> gpd.GeoDataFrame:
        """Get segments with risk above threshold"""
        if self.segment_geo is None:
            self.create_segment_geometries()
        return self.segment_geo[self.segment_geo["risk_score"] >= threshold]
