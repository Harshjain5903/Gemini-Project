"""
Weather service using Open-Meteo API (free, no API key needed).
Provides current conditions, hourly forecast, and risk multipliers.
"""

import requests
import time


class WeatherService:
    # WMO weather codes → (description, category, risk_multiplier)
    WEATHER_CODES = {
        0:  ("Clear sky", "clear", 1.0),
        1:  ("Mainly clear", "clear", 1.0),
        2:  ("Partly cloudy", "cloudy", 1.0),
        3:  ("Overcast", "cloudy", 1.0),
        45: ("Fog", "fog", 1.15),
        48: ("Freezing fog", "fog", 1.25),
        51: ("Light drizzle", "drizzle", 1.1),
        53: ("Moderate drizzle", "drizzle", 1.15),
        55: ("Dense drizzle", "drizzle", 1.2),
        56: ("Freezing drizzle", "freezing", 1.4),
        57: ("Heavy freezing drizzle", "freezing", 1.5),
        61: ("Light rain", "rain", 1.15),
        63: ("Moderate rain", "rain", 1.25),
        65: ("Heavy rain", "rain", 1.35),
        66: ("Light freezing rain", "freezing", 1.5),
        67: ("Heavy freezing rain", "freezing", 1.6),
        71: ("Light snow", "snow", 1.25),
        73: ("Moderate snow", "snow", 1.35),
        75: ("Heavy snow", "snow", 1.5),
        77: ("Snow grains", "snow", 1.25),
        80: ("Light rain showers", "rain", 1.15),
        81: ("Moderate rain showers", "rain", 1.25),
        82: ("Heavy rain showers", "rain", 1.4),
        85: ("Light snow showers", "snow", 1.25),
        86: ("Heavy snow showers", "snow", 1.5),
        95: ("Thunderstorm", "storm", 1.4),
        96: ("Thunderstorm with hail", "storm", 1.6),
        99: ("Thunderstorm with heavy hail", "storm", 1.7),
    }

    CATEGORY_ICONS = {
        "clear": "☀️", "cloudy": "☁️", "fog": "🌫️",
        "drizzle": "🌦️", "rain": "🌧️", "snow": "🌨️",
        "freezing": "🧊", "storm": "⛈️", "unknown": "🌡️",
    }

    def __init__(self, lat=41.8781, lng=-87.6298):
        self.lat = lat
        self.lng = lng
        self._cache = None
        self._cache_time = 0
        self.cache_ttl = 600  # 10 minutes

    def get_weather(self):
        """Get current weather + hourly forecast. Cached for 10 minutes."""
        now = time.time()
        if self._cache and (now - self._cache_time) < self.cache_ttl:
            return self._cache

        try:
            url = (
                f"https://api.open-meteo.com/v1/forecast?"
                f"latitude={self.lat}&longitude={self.lng}"
                f"&current=temperature_2m,relative_humidity_2m,precipitation,"
                f"weather_code,wind_speed_10m,apparent_temperature"
                f"&hourly=temperature_2m,precipitation_probability,weather_code,wind_speed_10m"
                f"&forecast_days=1"
                f"&temperature_unit=fahrenheit"
                f"&wind_speed_unit=mph"
                f"&precipitation_unit=inch"
                f"&timezone=America/Chicago"
            )
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            current = data.get("current", {})
            code = current.get("weather_code", 0)
            desc, category, risk_mult = self.WEATHER_CODES.get(code, ("Unknown", "unknown", 1.0))

            # Wind boost for high winds
            wind = current.get("wind_speed_10m", 0)
            if wind > 25:
                risk_mult += 0.1

            result = {
                "current": {
                    "temperature": current.get("temperature_2m"),
                    "feels_like": current.get("apparent_temperature"),
                    "humidity": current.get("relative_humidity_2m"),
                    "precipitation": current.get("precipitation", 0),
                    "wind_speed": round(wind, 1),
                    "weather_code": code,
                    "description": desc,
                    "category": category,
                    "icon": self.CATEGORY_ICONS.get(category, "🌡️"),
                    "risk_multiplier": round(risk_mult, 2),
                },
                "hourly": self._process_hourly(data.get("hourly", {})),
            }

            self._cache = result
            self._cache_time = now
            print(f"Weather updated: {desc}, {current.get('temperature_2m')}°F, risk x{risk_mult}")
            return result
        except Exception as e:
            print(f"Weather API error: {e}")
            return self._fallback()

    def _process_hourly(self, hourly):
        times = hourly.get("time", [])
        codes = hourly.get("weather_code", [])
        temps = hourly.get("temperature_2m", [])
        precip = hourly.get("precipitation_probability", [])
        winds = hourly.get("wind_speed_10m", [])

        result = []
        for i, t in enumerate(times):
            if i >= 24:
                break
            hour = int(t.split("T")[1].split(":")[0])
            code = codes[i] if i < len(codes) else 0
            desc, cat, mult = self.WEATHER_CODES.get(code, ("Unknown", "unknown", 1.0))
            w = winds[i] if i < len(winds) else 0
            if w > 25:
                mult += 0.1
            result.append({
                "hour": hour,
                "temp": temps[i] if i < len(temps) else None,
                "precip_chance": precip[i] if i < len(precip) else 0,
                "wind": round(w, 1) if i < len(winds) else 0,
                "description": desc,
                "category": cat,
                "icon": self.CATEGORY_ICONS.get(cat, "🌡️"),
                "risk_multiplier": round(mult, 2),
            })
        return result

    def _fallback(self):
        return {
            "current": {
                "temperature": None, "feels_like": None, "humidity": None,
                "precipitation": 0, "wind_speed": 0, "weather_code": 0,
                "description": "Weather unavailable", "category": "unknown",
                "icon": "🌡️", "risk_multiplier": 1.0,
            },
            "hourly": [],
        }

    def get_risk_multiplier(self, hour=None):
        """Get weather risk multiplier for current conditions or a specific hour."""
        data = self.get_weather()
        if hour is not None and data.get("hourly"):
            for h in data["hourly"]:
                if h["hour"] == hour:
                    return h["risk_multiplier"]
        return data["current"]["risk_multiplier"]

    def get_context_string(self, hour=None):
        """Human-readable weather context for Gemini prompts."""
        data = self.get_weather()
        current = data["current"]

        parts = []
        if current["temperature"] is not None:
            parts.append(f"{current['description']}, {current['temperature']}°F "
                         f"(feels like {current['feels_like']}°F)")
        else:
            parts.append(current["description"])

        if current["wind_speed"] and current["wind_speed"] > 10:
            parts.append(f"Wind: {current['wind_speed']} mph")

        if current["precipitation"] and current["precipitation"] > 0:
            parts.append(f"Precipitation: {current['precipitation']} inches")

        # Forecast for requested hour
        if hour is not None and data.get("hourly"):
            for h in data["hourly"]:
                if h["hour"] == hour:
                    if h["description"] != current["description"]:
                        parts.append(f"Forecast at {hour}:00: {h['description']}, {h['temp']}°F")
                    if h["precip_chance"] > 30:
                        parts.append(f"{h['precip_chance']}% chance of precipitation at that time")
                    break

        risk = current["risk_multiplier"]
        if risk >= 1.4:
            parts.append("Weather conditions significantly increase safety risk — extra caution needed")
        elif risk >= 1.2:
            parts.append("Weather conditions moderately increase safety risk")
        elif risk >= 1.1:
            parts.append("Weather conditions slightly increase safety risk")

        return ". ".join(parts)
