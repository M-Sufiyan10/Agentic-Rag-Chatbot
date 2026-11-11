"""Current weather from Open-Meteo (keyless)."""

import httpx

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
UNAVAILABLE = "Error: the weather service is unavailable right now."

WMO_DESCRIPTIONS: dict[int, str] = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "slight snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


async def get_weather(location: str) -> str:
    try:
        async with make_client() as client:
            geo = await client.get(GEOCODING_URL, params={"name": location, "count": 1})
            geo.raise_for_status()
            results = geo.json().get("results") or []
            if not results:
                return f"Error: couldn't find a place called '{location}'."
            place = results[0]
            forecast = await client.get(
                FORECAST_URL,
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": (
                        "temperature_2m,apparent_temperature,relative_humidity_2m,"
                        "wind_speed_10m,weather_code"
                    ),
                    "timezone": "auto",
                },
            )
            forecast.raise_for_status()
            current = forecast.json()["current"]
        description = WMO_DESCRIPTIONS.get(int(current["weather_code"]), "unknown conditions")
        name = place["name"]
        label = f"{name}, {place['country']}" if place.get("country") else name
        return (
            f"{label}: {description}, {current['temperature_2m']}°C "
            f"(feels like {current['apparent_temperature']}°C), "
            f"humidity {current['relative_humidity_2m']}%, "
            f"wind {current['wind_speed_10m']} km/h (as of {current['time']})."
        )
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return UNAVAILABLE
