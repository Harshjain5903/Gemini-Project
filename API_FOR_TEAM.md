# Geospatial Risk API Documentation

## For Person 2 (Routing Engineer)

### Primary File: `routing_risk_api.json`

This is your main integration point. Structure:

```json
{
  "metadata": {
    "type": "routing_risk_api",
    "h3_resolution": 9,
    "usage": "risk = base_risk * time_modifiers[period_daytype]"
  },
  "cells": {
    "892a100d2c3ffff": {
      "base_risk": 45.2,
      "smoothed_risk": 42.8,
      "pedestrian_risk": 67.3,
      "cyclist_risk": 23.1,
      "crash_count": 12,
      "total_severity": 156,
      "time_modifiers": {
        "morning_rush_weekday": 1.5,
        "evening_rush_weekday": 1.8,
        "midday_weekday": 0.9,
        "night_weekday": 0.4,
        "morning_rush_weekend": 0.8,
        ...
      }
    }
  }
}
```

### Risk Score Types

| Field | Use Case | Description |
|-------|----------|-------------|
| `base_risk` | Default driving | Raw risk from crash data |
| `smoothed_risk` | Recommended | Neighbor-aware (no data ≠ safe) |
| `pedestrian_risk` | Walking routes | Weighted by pedestrian injuries |
| `cyclist_risk` | Bike routes | Weighted by cyclist injuries |

### How to Use for Routing Cost Function

```python
import h3
import json

# Load risk data
with open("output/routing_risk_api.json") as f:
    risk_data = json.load(f)["cells"]

def get_edge_risk(lat, lng, hour, is_weekend=False):
    """Get risk score for a point at a specific time"""
    # Convert point to H3 cell
    cell = h3.latlng_to_cell(lat, lng, 9)

    if cell not in risk_data:
        return 0  # No crash data = assume safe

    cell_data = risk_data[cell]
    base_risk = cell_data["base_risk"]

    # Determine time period
    period = get_time_period(hour)  # Your logic
    day_type = "weekend" if is_weekend else "weekday"
    key = f"{period}_{day_type}"

    modifier = cell_data["time_modifiers"].get(key, 1.0)
    return base_risk * modifier

def routing_cost(distance, risk_score, beta=0.3):
    """
    Combined cost function for routing
    beta: weight for safety (0=pure distance, 1=pure safety)
    """
    # Normalize risk to 0-1 scale
    normalized_risk = risk_score / 100

    # Cost increases with distance and risk
    return distance * (1 + beta * normalized_risk)
```

### Time Periods

| Period | Hours | Typical Multiplier |
|--------|-------|-------------------|
| night | 0-6 | 0.3-0.5 (low traffic) |
| morning_rush | 6-9 | 1.3-1.8 (high danger) |
| midday | 9-16 | 0.8-1.0 (baseline) |
| evening_rush | 16-19 | 1.5-2.0 (highest danger) |
| evening | 19-24 | 0.6-0.9 |

---

## For Person 3 (Frontend Engineer)

### Visualization Files

#### 1. `grid_risk.geojson` - Heatmap Layer
H3 hexagons with risk scores for heatmap visualization.

```javascript
// Mapbox GL JS example
map.addSource('risk-grid', {
  type: 'geojson',
  data: 'output/grid_risk.geojson'
});

map.addLayer({
  id: 'risk-heatmap',
  type: 'fill',
  source: 'risk-grid',
  paint: {
    'fill-color': [
      'interpolate',
      ['linear'],
      ['get', 'risk_score'],
      0, '#00ff00',   // green = safe
      50, '#ffff00',  // yellow = medium
      100, '#ff0000'  // red = dangerous
    ],
    'fill-opacity': 0.6
  }
});
```

Properties per hexagon:
- `h3_cell`: Unique cell ID
- `risk_score`: 0-100 (use for color)
- `risk_category`: "very_low", "low", "medium", "high", "critical"
- `crash_count`: Number of crashes
- `total_injured`, `total_killed`: For tooltips
- `center_lat`, `center_lng`: Cell center point

#### 2. `segment_risk.geojson` - Street Lines
Road segments with risk data.

```javascript
map.addLayer({
  id: 'risk-segments',
  type: 'line',
  source: 'risk-segments',
  paint: {
    'line-color': [
      'interpolate',
      ['linear'],
      ['get', 'risk_score'],
      0, '#22c55e',
      100, '#ef4444'
    ],
    'line-width': 3
  }
});
```

Properties:
- `street_name`: Street name
- `risk_score`: 0-100
- `crashes_per_km`: Density metric

#### 3. `intersection_risk.geojson` - Danger Points
High-risk intersections as points.

#### 4. `time_patterns.json` - Time Slider Data

```json
{
  "hourly": [
    {"hour": 0, "risk_score": 23.5, "risk_multiplier": 0.45},
    {"hour": 1, "risk_score": 18.2, "risk_multiplier": 0.35},
    ...
  ],
  "periods": [
    {
      "time_period": "morning_rush",
      "day_type": "weekday",
      "risk_multiplier": 1.65
    },
    ...
  ]
}
```

Use `hourly` data for a 24-hour slider showing citywide risk.

### Slider Implementation

```javascript
// When user changes time slider
function onTimeChange(hour, isWeekend) {
  const period = getTimePeriod(hour);
  const dayType = isWeekend ? 'weekend' : 'weekday';

  // Filter/update heatmap based on time
  // Use cell_time_lookup from time_patterns.json
  const timeKey = `${period}_${dayType}`;

  // Update layer filter or reload with time-specific data
}
```

---

## File Summary

| File | Consumer | Purpose |
|------|----------|---------|
| `routing_risk_api.json` | Person 2 | Routing cost function |
| `grid_risk.json` | Person 2 | Simple cell lookup |
| `grid_risk.geojson` | Person 3 | Heatmap hexagons |
| `segment_risk.geojson` | Person 3 | Street line layer |
| `intersection_risk.geojson` | Person 3 | Danger point markers |
| `time_patterns.json` | Person 3 | Time slider data |
| `validation_report.json` | Everyone | Quality metrics |

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run full pipeline (takes ~2-3 min first time)
python main.py

# Quick test with small dataset
python quick_test.py

# Custom options
python main.py --limit 100000 --year 2022
```

Output will be in `./output/` directory.
