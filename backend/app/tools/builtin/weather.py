"""get_weather via Open-Meteo (free, no API key): geocode the place, then fetch the forecast."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.conversation.ports import UserContext
from app.tools.registry import Tool, ToolError

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_REQUEST_TIMEOUT_S = 5.0

# WMO weather interpretation codes used by Open-Meteo.
_WEATHER_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    80: "rain showers",
    81: "rain showers",
    82: "violent rain showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with hail",
}


class WeatherArgs(BaseModel):
    location: str = Field(min_length=1, max_length=100, description="City or place, e.g. 'Pune'")


class WeatherTool(Tool[WeatherArgs]):
    name = "get_weather"
    description = "Get the current weather and today's forecast for a city or place."
    args_model = WeatherArgs
    timeout_s = 12.0

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def run(self, args: WeatherArgs, user: UserContext) -> str:
        try:
            place = await self._geocode(args.location.strip())
            forecast = await self._forecast(place["latitude"], place["longitude"])
        except (httpx.HTTPError, KeyError, ValueError):
            raise ToolError("the weather service is unavailable right now") from None
        return _describe(place, forecast)

    async def _geocode(self, location: str) -> dict[str, Any]:
        response = await self._client.get(
            GEOCODING_URL,
            params={"name": location, "count": 1, "language": "en", "format": "json"},
            timeout=_REQUEST_TIMEOUT_S,
        )
        response.raise_for_status()
        results = response.json().get("results")
        if not results:
            raise ToolError(f"could not find a place called '{location}'")
        place: dict[str, Any] = results[0]
        return place

    async def _forecast(self, latitude: float, longitude: float) -> dict[str, Any]:
        response = await self._client.get(
            FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                "weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "forecast_days": 1,
                "timezone": "auto",
            },
            timeout=_REQUEST_TIMEOUT_S,
        )
        response.raise_for_status()
        forecast: dict[str, Any] = response.json()
        return forecast


def _describe(place: dict[str, Any], forecast: dict[str, Any]) -> str:
    now, today = forecast["current"], forecast["daily"]
    sky = _WEATHER_CODES.get(now["weather_code"], "unsettled")
    name = ", ".join(str(p) for p in (place.get("name"), place.get("country")) if p)
    rain = today["precipitation_probability_max"][0]
    rain_note = f", chance of rain {rain}%" if rain is not None else ""
    return (
        f"Weather in {name}: {now['temperature_2m']:.0f}°C (feels like "
        f"{now['apparent_temperature']:.0f}°C), {sky}, "
        f"humidity {now['relative_humidity_2m']:.0f}%, "
        f"wind {now['wind_speed_10m']:.0f} km/h. Today: {today['temperature_2m_min'][0]:.0f}-"
        f"{today['temperature_2m_max'][0]:.0f}°C{rain_note}."
    )
