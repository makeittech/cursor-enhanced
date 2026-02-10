"""
Weather Tool - Get current weather and forecast via Open-Meteo API.

No API key required. Supports any city via geocoding.
Default city: Lviv, Ukraine.
"""

import asyncio
from typing import Dict, Optional, Any
import logging

logger = logging.getLogger("cursor_enhanced.openclaw_weather")

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

# WMO weather interpretation codes -> human-readable descriptions
WMO_CODES: Dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

# Well-known cities with coordinates (lat, lon, timezone)
KNOWN_CITIES: Dict[str, Dict[str, Any]] = {
    "lviv": {"lat": 49.8397, "lon": 24.0297, "timezone": "Europe/Kyiv", "name": "Lviv, Ukraine"},
    "kyiv": {"lat": 50.4501, "lon": 30.5234, "timezone": "Europe/Kyiv", "name": "Kyiv, Ukraine"},
    "london": {"lat": 51.5074, "lon": -0.1278, "timezone": "Europe/London", "name": "London, UK"},
    "new york": {"lat": 40.7128, "lon": -74.0060, "timezone": "America/New_York", "name": "New York, USA"},
    "tokyo": {"lat": 35.6762, "lon": 139.6503, "timezone": "Asia/Tokyo", "name": "Tokyo, Japan"},
    "berlin": {"lat": 52.5200, "lon": 13.4050, "timezone": "Europe/Berlin", "name": "Berlin, Germany"},
    "paris": {"lat": 48.8566, "lon": 2.3522, "timezone": "Europe/Paris", "name": "Paris, France"},
    "warsaw": {"lat": 52.2297, "lon": 21.0122, "timezone": "Europe/Warsaw", "name": "Warsaw, Poland"},
}

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_SECONDS = 15


def _wmo_description(code: int) -> str:
    return WMO_CODES.get(code, f"Unknown ({code})")


class WeatherTool:
    """Weather tool — get current weather and forecast for any city.

    Uses the free Open-Meteo API (no API key needed).
    Default city is Lviv, Ukraine.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        weather_cfg = self.config.get("tools", {}).get("weather", {})
        self.default_city = weather_cfg.get("default_city", "lviv")
        self.enabled = weather_cfg.get("enabled", True)

    # ------------------------------------------------------------------
    # Geocoding: resolve city name to lat/lon
    # ------------------------------------------------------------------
    async def _geocode(self, city: str) -> Dict[str, Any]:
        """Resolve city name to coordinates. Returns dict with lat, lon, timezone, name."""
        key = city.strip().lower()
        if key in KNOWN_CITIES:
            return KNOWN_CITIES[key]

        if not HTTPX_AVAILABLE:
            return {"error": "httpx library required. Install with: pip install httpx"}

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                resp = await client.get(GEOCODE_URL, params={"name": city, "count": 1, "language": "en"})
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results")
                if not results:
                    return {"error": f"City not found: {city}"}
                r = results[0]
                return {
                    "lat": r["latitude"],
                    "lon": r["longitude"],
                    "timezone": r.get("timezone", "UTC"),
                    "name": f"{r.get('name', city)}, {r.get('country', '')}".rstrip(", "),
                }
        except Exception as e:
            logger.error("Geocoding failed for %s: %s", city, e)
            return {"error": f"Geocoding failed: {e}"}

    # ------------------------------------------------------------------
    # Main execute: current + forecast
    # ------------------------------------------------------------------
    async def execute(self, city: Optional[str] = None, forecast_days: int = 7) -> Dict[str, Any]:
        """Get current weather and forecast for *city* (default: Lviv).

        Parameters
        ----------
        city : str, optional
            City name (e.g. "Lviv", "London"). Default from config or "Lviv".
        forecast_days : int
            Number of forecast days (1-16). Default 7.
        """
        if not self.enabled:
            return {"error": "Weather tool is disabled"}
        if not HTTPX_AVAILABLE:
            return {"error": "httpx library required. Install with: pip install httpx"}

        city = (city or self.default_city).strip()
        forecast_days = max(1, min(16, forecast_days))

        # Resolve coordinates
        geo = await self._geocode(city)
        if "error" in geo:
            return geo

        lat, lon, tz, city_name = geo["lat"], geo["lon"], geo["timezone"], geo["name"]

        try:
            params = {
                "latitude": lat,
                "longitude": lon,
                "timezone": tz,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m,pressure_msl",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max",
                "forecast_days": forecast_days,
            }
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                resp = await client.get(WEATHER_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.error("Weather API call failed: %s", e)
            return {"error": f"Weather API error: {e}"}

        # ---- Parse current weather ----
        cur = data.get("current", {})
        current = {
            "temperature_c": cur.get("temperature_2m"),
            "feels_like_c": cur.get("apparent_temperature"),
            "humidity_pct": cur.get("relative_humidity_2m"),
            "wind_speed_kmh": cur.get("wind_speed_10m"),
            "wind_direction_deg": cur.get("wind_direction_10m"),
            "pressure_hpa": cur.get("pressure_msl"),
            "weather": _wmo_description(cur.get("weather_code", -1)),
            "weather_code": cur.get("weather_code"),
        }

        # ---- Parse daily forecast ----
        daily_data = data.get("daily", {})
        dates = daily_data.get("time", [])
        forecast = []
        for i, date in enumerate(dates):
            forecast.append({
                "date": date,
                "weather": _wmo_description((daily_data.get("weather_code") or [])[i] if i < len(daily_data.get("weather_code", [])) else -1),
                "temp_max_c": (daily_data.get("temperature_2m_max") or [])[i] if i < len(daily_data.get("temperature_2m_max", [])) else None,
                "temp_min_c": (daily_data.get("temperature_2m_min") or [])[i] if i < len(daily_data.get("temperature_2m_min", [])) else None,
                "precipitation_mm": (daily_data.get("precipitation_sum") or [])[i] if i < len(daily_data.get("precipitation_sum", [])) else None,
                "wind_max_kmh": (daily_data.get("wind_speed_10m_max") or [])[i] if i < len(daily_data.get("wind_speed_10m_max", [])) else None,
            })

        return {
            "city": city_name,
            "timezone": tz,
            "current": current,
            "forecast": forecast,
        }

    # ------------------------------------------------------------------
    # Convenience: current-only
    # ------------------------------------------------------------------
    async def get_current(self, city: Optional[str] = None) -> Dict[str, Any]:
        """Get current weather only (no forecast)."""
        result = await self.execute(city=city, forecast_days=1)
        if "error" in result:
            return result
        result.pop("forecast", None)
        return result

    # ------------------------------------------------------------------
    # Convenience: forecast-only
    # ------------------------------------------------------------------
    async def get_forecast(self, city: Optional[str] = None, days: int = 7) -> Dict[str, Any]:
        """Get forecast only (no current)."""
        result = await self.execute(city=city, forecast_days=days)
        if "error" in result:
            return result
        result.pop("current", None)
        return result
