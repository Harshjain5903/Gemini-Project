"""
Risk Data Export Module
Exports processed risk data in formats for routing and visualization
"""
import json
import geopandas as gpd
import pandas as pd
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime


class RiskExporter:
    """Export risk data in various formats for downstream consumers"""

    def __init__(self, output_dir: str = "output", h3_resolution: int = 9):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.h3_resolution = h3_resolution

    def export_grid_geojson(
        self,
        grid_gdf: gpd.GeoDataFrame,
        filename: str = "grid_risk.geojson"
    ) -> str:
        """
        Export H3 grid risk as GeoJSON for map visualization

        Args:
            grid_gdf: GeoDataFrame with H3 hexagons
            filename: Output filename

        Returns:
            Path to exported file
        """
        output_path = self.output_dir / filename

        # Select columns for export
        export_cols = [
            "h3_cell", "risk_score", "risk_category", "crash_count",
            "total_severity", "total_injured", "total_killed",
            "center_lat", "center_lng", "geometry"
        ]

        export_gdf = grid_gdf[[c for c in export_cols if c in grid_gdf.columns]].copy()

        # Convert category to string for JSON
        if "risk_category" in export_gdf.columns:
            export_gdf["risk_category"] = export_gdf["risk_category"].astype(str)

        export_gdf.to_file(output_path, driver="GeoJSON")
        print(f"Exported grid GeoJSON: {output_path}")
        return str(output_path)

    def export_segments_geojson(
        self,
        segments_gdf: gpd.GeoDataFrame,
        filename: str = "segment_risk.geojson"
    ) -> str:
        """
        Export road segments as GeoJSON

        Args:
            segments_gdf: GeoDataFrame with street segments
            filename: Output filename

        Returns:
            Path to exported file
        """
        output_path = self.output_dir / filename
        segments_gdf.to_file(output_path, driver="GeoJSON")
        print(f"Exported segments GeoJSON: {output_path}")
        return str(output_path)

    def export_grid_json(
        self,
        grid_df: pd.DataFrame,
        filename: str = "grid_risk.json"
    ) -> str:
        """
        Export grid risk as plain JSON (no geometry) for routing API

        Args:
            grid_df: DataFrame with grid risk data
            filename: Output filename

        Returns:
            Path to exported file
        """
        output_path = self.output_dir / filename

        # Remove geometry, keep data
        export_df = grid_df.drop(columns=["geometry"], errors="ignore").copy()

        # Convert to records format
        records = export_df.to_dict("records")

        # Create lookup dict by h3_cell for fast access
        lookup = {r["h3_cell"]: r for r in records}

        output_data = {
            "metadata": {
                "type": "h3_grid_risk",
                "generated_at": datetime.now().isoformat(),
                "total_cells": len(records),
                "resolution": self.h3_resolution
            },
            "cells": lookup
        }

        with open(output_path, "w") as f:
            json.dump(output_data, f, indent=2, default=str)

        print(f"Exported grid JSON: {output_path}")
        return str(output_path)

    def export_time_patterns_json(
        self,
        hourly_df: pd.DataFrame,
        period_df: pd.DataFrame,
        cell_time_df: pd.DataFrame,
        filename: str = "time_patterns.json"
    ) -> str:
        """
        Export temporal risk patterns

        Args:
            hourly_df: Hourly risk data
            period_df: Period risk data
            cell_time_df: Cell-time combination data
            filename: Output filename

        Returns:
            Path to exported file
        """
        output_path = self.output_dir / filename

        # Build time patterns structure
        output_data = {
            "metadata": {
                "type": "time_patterns",
                "generated_at": datetime.now().isoformat()
            },
            "hourly": hourly_df.to_dict("records") if hourly_df is not None else [],
            "periods": period_df.to_dict("records") if period_df is not None else [],
            "cell_time_lookup": {}
        }

        # Create fast lookup for cell-time combinations
        if cell_time_df is not None:
            lookup = output_data["cell_time_lookup"]
            for cell, group in cell_time_df.groupby("h3_cell"):
                lookup[cell] = {
                    f"{r['time_period']}_{r['day_type']}": {
                        "crash_count": int(r["crash_count"]),
                        "time_risk_score": float(r["time_risk_score"]),
                        "global_risk_score": float(r["global_risk_score"])
                    }
                    for r in group.to_dict("records")
                }

        with open(output_path, "w") as f:
            json.dump(output_data, f, indent=2, default=str)

        print(f"Exported time patterns JSON: {output_path}")
        return str(output_path)

    def export_routing_api_format(
        self,
        grid_df: pd.DataFrame,
        time_df: pd.DataFrame,
        filename: str = "routing_risk_api.json",
        combined_time_df: pd.DataFrame = None
    ) -> str:
        """
        Export combined data optimized for routing engine consumption

        This is the PRIMARY output for Person 2 (routing engineer)

        Format:
        {
            "cells": {
                "h3_cell_id": {
                    "base_risk": 45.2,
                    "crime_risk": 30.1,
                    "crash_count": 12,
                    "time_modifiers": { ... },
                    "crime_time_modifiers": { ... }
                }
            }
        }
        """
        output_path = self.output_dir / filename

        # Build cells dict from grid in batch (avoid iterrows)
        grid_records = grid_df.to_dict("records")
        cells = {}
        for r in grid_records:
            severity = r.get("crash_severity", r.get("total_severity", 0))
            cells[r["h3_cell"]] = {
                "base_risk": float(r.get("risk_score", 0)),
                "smoothed_risk": float(r.get("smoothed_risk", r.get("risk_score", 0))),
                "pedestrian_risk": float(r.get("pedestrian_risk", 0)),
                "cyclist_risk": float(r.get("cyclist_risk", 0)),
                "crime_risk": float(r.get("crime_risk", 0)),
                "smoothed_crime_risk": float(r.get("smoothed_crime_risk", 0)),
                "crash_count": int(r.get("crash_count", 0)),
                "crime_count": int(r.get("crime_count", 0)),
                "total_severity": float(severity),
                "time_modifiers": {},
                "crime_time_modifiers": {}
            }

        # Add crash time modifiers in batch
        if time_df is not None:
            for cell_id, group in time_df.groupby("h3_cell"):
                if cell_id not in cells:
                    continue
                base = cells[cell_id]["base_risk"]
                for r in group.to_dict("records"):
                    key = f"{r['time_period']}_{r['day_type']}"
                    modifier = (r["global_risk_score"] / base) if base > 0 else 1.0
                    cells[cell_id]["time_modifiers"][key] = round(modifier, 3)

        # Add crime time modifiers in batch
        if combined_time_df is not None:
            for cell_id, group in combined_time_df.groupby("h3_cell"):
                if cell_id not in cells:
                    continue
                crime_base = cells[cell_id]["crime_risk"]
                for r in group.to_dict("records"):
                    key = f"{r['time_period']}_{r['day_type']}"
                    score = r.get("crime_time_score", 0)
                    modifier = (score / crime_base) if crime_base > 0 else 1.0
                    cells[cell_id]["crime_time_modifiers"][key] = round(modifier, 3)

        output_data = {
            "metadata": {
                "type": "routing_risk_api",
                "generated_at": datetime.now().isoformat(),
                "total_cells": len(cells),
                "h3_resolution": self.h3_resolution,
                "usage": "Walking: risk = crime_risk*0.7 + base_risk*0.3. Driving: risk = base_risk*0.9 + crime_risk*0.1",
                "has_crime_data": True
            },
            "cells": cells
        }

        with open(output_path, "w") as f:
            json.dump(output_data, f, indent=2)

        print(f"Exported routing API JSON: {output_path}")
        return str(output_path)

    def export_intersections_geojson(
        self,
        intersections_gdf: gpd.GeoDataFrame,
        filename: str = "intersection_risk.geojson"
    ) -> str:
        """Export intersection risk points as GeoJSON"""
        output_path = self.output_dir / filename
        intersections_gdf.to_file(output_path, driver="GeoJSON")
        print(f"Exported intersections GeoJSON: {output_path}")
        return str(output_path)

    def export_all(
        self,
        grid_gdf: gpd.GeoDataFrame,
        segments_gdf: gpd.GeoDataFrame,
        intersections_gdf: gpd.GeoDataFrame,
        hourly_df: pd.DataFrame,
        period_df: pd.DataFrame,
        cell_time_df: pd.DataFrame,
        combined_grid_df: pd.DataFrame = None,
        combined_time_df: pd.DataFrame = None
    ) -> Dict[str, str]:
        """
        Export all data formats at once

        Returns:
            Dict mapping format name to file path
        """
        exports = {}

        exports["grid_geojson"] = self.export_grid_geojson(grid_gdf)
        exports["segments_geojson"] = self.export_segments_geojson(segments_gdf)
        exports["intersections_geojson"] = self.export_intersections_geojson(intersections_gdf)

        # JSON exports need DataFrame without geometry
        grid_df = grid_gdf.drop(columns=["geometry"], errors="ignore")
        exports["grid_json"] = self.export_grid_json(grid_df)
        exports["time_patterns"] = self.export_time_patterns_json(
            hourly_df, period_df, cell_time_df
        )

        # Use combined grid (crash+crime) if available, else plain crash grid
        routing_grid = combined_grid_df if combined_grid_df is not None else grid_df
        exports["routing_api"] = self.export_routing_api_format(
            routing_grid, cell_time_df, combined_time_df=combined_time_df
        )

        print(f"\nAll exports complete. Files in: {self.output_dir}")
        return exports
