import osmnx as ox
import networkx as nx
import json
import os
import h3

class RoutingEngine:
    # Travel mode risk blending weights
    # Walking: crime matters much more than crashes
    # Driving: crashes matter much more than crime
    MODE_WEIGHTS = {
        "walking":  {"crash": 0.3, "crime": 0.7},
        "driving":  {"crash": 0.9, "crime": 0.1},
        "cycling":  {"crash": 0.5, "crime": 0.5},
    }

    def __init__(self, place_name="Chicago, Illinois, USA", cache_path=None):
        if cache_path and os.path.exists(cache_path):
            print(f"Loading cached graph from {cache_path}...")
            self.G = ox.load_graphml(cache_path)
            print("Graph loaded from cache.")
        else:
            print(f"Downloading graph for {place_name} (this may take a few minutes)...")
            self.G = ox.graph_from_place(place_name, network_type='drive')
            self.G = ox.add_edge_speeds(self.G)
            self.G = ox.add_edge_travel_times(self.G)
            if cache_path:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                ox.save_graphml(self.G, cache_path)
                print(f"Graph cached to {cache_path}")

        # Store bounds for validation
        nodes = self.G.nodes(data=True)
        lats = [d['y'] for _, d in nodes]
        lngs = [d['x'] for _, d in nodes]
        self.bounds = {
            'min_lat': min(lats), 'max_lat': max(lats),
            'min_lng': min(lngs), 'max_lng': max(lngs)
        }
        print(f"Coverage: lat [{self.bounds['min_lat']:.4f}, {self.bounds['max_lat']:.4f}]")
        print(f"Coverage: lng [{self.bounds['min_lng']:.4f}, {self.bounds['max_lng']:.4f}]")
        self.risk_data = {}
        self.has_crime_data = False

    def load_risk_api(self, json_path):
        """Loads the provided routing_risk_api.json"""
        with open(json_path, 'r') as f:
            data = json.load(f)
            self.risk_data = data.get("cells", {})
            self.has_crime_data = data.get("metadata", {}).get("has_crime_data", False)
        print(f"Loaded {len(self.risk_data)} risk-mapped hexagons.")
        if self.has_crime_data:
            print("Crime risk data detected - travel-mode-aware routing enabled.")

    def is_in_bounds(self, lat, lng):
        """Check if coordinates are within road network coverage"""
        return (self.bounds['min_lat'] <= lat <= self.bounds['max_lat'] and
                self.bounds['min_lng'] <= lng <= self.bounds['max_lng'])

    def _get_blended_risk(self, cell_info, time_key, travel_mode="walking"):
        """
        Calculate blended risk based on travel mode.
        Walking: 70% crime + 30% crash
        Driving: 90% crash + 10% crime
        """
        weights = self.MODE_WEIGHTS.get(travel_mode, self.MODE_WEIGHTS["walking"])

        # Crash risk with time modifier
        crash_base = cell_info.get("base_risk", 0)
        crash_mod = cell_info.get("time_modifiers", {}).get(time_key, 1.0)
        crash_risk = crash_base * crash_mod

        # Crime risk with time modifier (falls back gracefully if no crime data)
        crime_base = cell_info.get("crime_risk", 0)
        crime_mod = cell_info.get("crime_time_modifiers", {}).get(time_key, 1.0)
        crime_risk = crime_base * crime_mod

        # If no crime data exists, fall back to crash-only
        if crime_base == 0 and not self.has_crime_data:
            return crash_risk

        return (weights["crash"] * crash_risk) + (weights["crime"] * crime_risk)

    def get_comparison(self, start_coords, end_coords, beta=5.0, hour=12, is_weekend=False, travel_mode="walking"):
        """
        Returns two routes: Fastest (Beta=0) and Risk-Aware (Beta=User Value)
        travel_mode: 'walking', 'driving', or 'cycling' — affects crash vs crime risk weighting
        """
        # Validate bounds
        if not self.is_in_bounds(start_coords[0], start_coords[1]):
            raise ValueError(f"Start point outside Chicago coverage area")
        if not self.is_in_bounds(end_coords[0], end_coords[1]):
            raise ValueError(f"End point outside Chicago coverage area")

        # 1. Get the Fastest Route (Beta = 0)
        fastest_path_coords = self.get_route(start_coords, end_coords, beta=0, hour=hour, is_weekend=is_weekend, travel_mode=travel_mode)

        # 2. Get the Risk-Aware Route
        safest_path_coords = self.get_route(start_coords, end_coords, beta=beta, hour=hour, is_weekend=is_weekend, travel_mode=travel_mode)

        # 3. Calculate Stats for both
        stats = {
            "fastest": self._calculate_route_stats(fastest_path_coords, hour, is_weekend, travel_mode),
            "safest": self._calculate_route_stats(safest_path_coords, hour, is_weekend, travel_mode)
        }

        # Calculate improvements
        stats["reduction_in_risk_pct"] = round(
            (1 - (stats["safest"]["total_risk"] / max(stats["fastest"]["total_risk"], 1))) * 100, 1
        )
        stats["extra_time_seconds"] = round(stats["safest"]["total_time"] - stats["fastest"]["total_time"], 0)
        stats["travel_mode"] = travel_mode

        return {
            "fastest_route": fastest_path_coords,
            "safest_route": safest_path_coords,
            "metrics": stats
        }

    def _calculate_route_stats(self, coords, hour, is_weekend, travel_mode="walking"):
        """Helper to sum up time and risk for any list of coordinates"""
        total_time = 0
        total_risk = 0
        time_key = self._get_time_key(hour, is_weekend)

        for i in range(len(coords) - 1):
            u = ox.nearest_nodes(self.G, coords[i][1], coords[i][0])
            v = ox.nearest_nodes(self.G, coords[i+1][1], coords[i+1][0])

            edge_data = self.G.get_edge_data(u, v)
            if edge_data:
                data = list(edge_data.values())[0]
                total_time += data.get('travel_time', 0)

                cell = h3.latlng_to_cell(coords[i][0], coords[i][1], 9)
                if cell in self.risk_data:
                    total_risk += self._get_blended_risk(
                        self.risk_data[cell], time_key, travel_mode
                    )

        return {"total_time": total_time, "total_risk": total_risk}

    def _get_time_key(self, hour, is_weekend):
        """Maps hour to the keys in your JSON"""
        day_type = "weekend" if is_weekend else "weekday"

        if 0 <= hour < 6: period = "night"
        elif 6 <= hour < 9: period = "morning_rush"
        elif 9 <= hour < 16: period = "midday"
        elif 16 <= hour < 19: period = "evening_rush"
        elif 19 <= hour < 22: period = "evening"
        else: period = "night"

        return f"{period}_{day_type}"

    def get_route(self, start_coords, end_coords, beta=0.5, hour=12, is_weekend=False, travel_mode="walking"):
        """
        start_coords: [lat, lng]
        beta: Risk sensitivity (0 = fastest, 1.0+ = much safer)
        travel_mode: 'walking', 'driving', or 'cycling'
        """
        orig_node = ox.nearest_nodes(self.G, start_coords[1], start_coords[0])
        dest_node = ox.nearest_nodes(self.G, end_coords[1], end_coords[0])

        time_key = self._get_time_key(hour, is_weekend)

        def risk_cost_func(u, v, data):
            travel_time = data.get('travel_time', 1.0)
            node_data = self.G.nodes[u]
            lat, lng = node_data['y'], node_data['x']
            cell = h3.latlng_to_cell(lat, lng, 9)

            risk_val = 2.0  # Default unknown risk

            if cell in self.risk_data:
                risk_val = self._get_blended_risk(
                    self.risk_data[cell], time_key, travel_mode
                )

            return travel_time + (beta * risk_val)

        path = nx.shortest_path(self.G, orig_node, dest_node, weight=risk_cost_func)

        return [[self.G.nodes[n]['y'], self.G.nodes[n]['x']] for n in path]
