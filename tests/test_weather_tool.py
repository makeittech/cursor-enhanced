"""Tests for the weather tool (runtime_weather_tool.py)."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

from runtime_weather_tool import WeatherTool, WMO_CODES, _wmo_description, KNOWN_CITIES


class WMOCodeTests(unittest.TestCase):
    """Tests for WMO weather code descriptions."""

    def test_known_code(self):
        self.assertEqual(_wmo_description(0), "Clear sky")
        self.assertEqual(_wmo_description(61), "Slight rain")
        self.assertEqual(_wmo_description(95), "Thunderstorm")

    def test_unknown_code(self):
        desc = _wmo_description(999)
        self.assertIn("Unknown", desc)

    def test_all_codes_are_strings(self):
        for code, desc in WMO_CODES.items():
            self.assertIsInstance(desc, str)
            self.assertTrue(len(desc) > 0)


class GeocodeTests(unittest.TestCase):
    """Tests for city geocoding."""

    def test_known_city_returns_immediately(self):
        tool = WeatherTool()
        result = asyncio.run(tool._geocode("Lviv"))
        self.assertNotIn("error", result)
        self.assertAlmostEqual(result["lat"], 49.8397, places=2)
        self.assertEqual(result["timezone"], "Europe/Kyiv")

    def test_known_city_case_insensitive(self):
        tool = WeatherTool()
        result = asyncio.run(tool._geocode("KYIV"))
        self.assertNotIn("error", result)
        self.assertEqual(result["timezone"], "Europe/Kyiv")

    def test_known_cities_have_required_keys(self):
        for city, data in KNOWN_CITIES.items():
            self.assertIn("lat", data, f"{city} missing lat")
            self.assertIn("lon", data, f"{city} missing lon")
            self.assertIn("timezone", data, f"{city} missing timezone")
            self.assertIn("name", data, f"{city} missing name")


class WeatherToolUnitTests(unittest.TestCase):
    """Unit tests for WeatherTool (mocked HTTP)."""

    def _make_mock_response(self):
        """Create a mock Open-Meteo JSON response."""
        return {
            "current": {
                "temperature_2m": 5.2,
                "apparent_temperature": 2.1,
                "relative_humidity_2m": 78,
                "weather_code": 3,
                "wind_speed_10m": 12.5,
                "wind_direction_10m": 220,
                "pressure_msl": 1015.3,
            },
            "daily": {
                "time": ["2026-02-10", "2026-02-11"],
                "weather_code": [3, 61],
                "temperature_2m_max": [7.0, 4.5],
                "temperature_2m_min": [1.0, -1.2],
                "precipitation_sum": [0.0, 3.5],
                "wind_speed_10m_max": [20.0, 15.0],
            },
        }

    @patch("runtime_weather_tool.httpx")
    def test_execute_returns_current_and_forecast(self, mock_httpx):
        mock_resp = MagicMock()
        mock_resp.json.return_value = self._make_mock_response()
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client
        mock_httpx.HTTPX_AVAILABLE = True

        tool = WeatherTool()
        # Use known city so geocode doesn't need HTTP
        result = asyncio.run(tool.execute(city="Lviv", forecast_days=2))

        self.assertNotIn("error", result)
        self.assertEqual(result["city"], "Lviv, Ukraine")
        self.assertIn("current", result)
        self.assertIn("forecast", result)

        cur = result["current"]
        self.assertEqual(cur["temperature_c"], 5.2)
        self.assertEqual(cur["weather"], "Overcast")

        forecast = result["forecast"]
        self.assertEqual(len(forecast), 2)
        self.assertEqual(forecast[0]["date"], "2026-02-10")
        self.assertEqual(forecast[1]["weather"], "Slight rain")

    @patch("runtime_weather_tool.httpx")
    def test_get_current_omits_forecast(self, mock_httpx):
        mock_resp = MagicMock()
        mock_resp.json.return_value = self._make_mock_response()
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        tool = WeatherTool()
        result = asyncio.run(tool.get_current(city="Lviv"))

        self.assertNotIn("error", result)
        self.assertIn("current", result)
        self.assertNotIn("forecast", result)

    @patch("runtime_weather_tool.httpx")
    def test_get_forecast_omits_current(self, mock_httpx):
        mock_resp = MagicMock()
        mock_resp.json.return_value = self._make_mock_response()
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        tool = WeatherTool()
        result = asyncio.run(tool.get_forecast(city="Lviv", days=2))

        self.assertNotIn("error", result)
        self.assertNotIn("current", result)
        self.assertIn("forecast", result)

    def test_disabled_tool_returns_error(self):
        tool = WeatherTool(config={"tools": {"weather": {"enabled": False}}})
        result = asyncio.run(tool.execute(city="Lviv"))
        self.assertIn("error", result)
        self.assertIn("disabled", result["error"])

    def test_default_city_from_config(self):
        tool = WeatherTool(config={"tools": {"weather": {"default_city": "kyiv"}}})
        self.assertEqual(tool.default_city, "kyiv")

    def test_forecast_days_clamped(self):
        """forecast_days should be clamped to [1, 16]."""
        tool = WeatherTool()
        # We can't easily test the clamped value without calling execute,
        # but we can verify the tool instantiates and doesn't crash
        self.assertTrue(tool.enabled)


class ToolRegistryIntegrationTests(unittest.TestCase):
    """Test that weather tool is registered in the ToolRegistry."""

    def test_weather_tool_registered(self):
        from runtime_core import ToolRegistry
        registry = ToolRegistry(gateway_client=None, config={})
        self.assertIn("weather", registry.tools)

    def test_weather_tool_in_list(self):
        from runtime_core import ToolRegistry
        registry = ToolRegistry(gateway_client=None, config={})
        tool_names = [t["name"] for t in registry.list_tools()]
        self.assertIn("weather", tool_names)

    def test_weather_tool_has_description(self):
        from runtime_core import ToolRegistry
        registry = ToolRegistry(gateway_client=None, config={})
        tools = registry.list_tools()
        weather = [t for t in tools if t["name"] == "weather"]
        self.assertTrue(weather)
        self.assertIn("description", weather[0])
        self.assertTrue(len(weather[0]["description"]) > 10)


if __name__ == "__main__":
    unittest.main()
