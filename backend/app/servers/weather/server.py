import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("weather", streamable_http_path="/", instructions=(
    "When asked about current weather or conditions somewhere, call get_weather with "
    "the place name. Never guess a location or invent conditions."
))

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = 8.0

_CONDITIONS = {
    0: "clear skies", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy with frost",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light rain showers", 81: "rain showers", 82: "heavy rain showers",
    85: "light snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "severe thunderstorms with hail",
}

async def _geocode(location: str) -> dict | None:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            _GEOCODE_URL, params={"name": location, "count": 1, "language": "en", "format": "json"}
        )
        resp.raise_for_status()
        results = resp.json().get("results")
    return results[0] if results else None

async def _current_conditions(lat: float, lon: float) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(_FORECAST_URL, params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "timezone": "auto",
        })
        resp.raise_for_status()
        return resp.json()["current"]

@mcp.tool(description=(
    "Get the current weather for a place.\n\n"
    "location: a city or place name, e.g. \"Austin\" or \"Paris, France\". Ask the "
    "user which place they mean if they haven't said one — never guess."
))
async def get_weather(location: str) -> str:
    try:
        place = await _geocode(location)
        if not place and "," in location:
            place = await _geocode(location.split(",", 1)[0].strip())
    except httpx.HTTPError:
        return "I couldn't check the weather right now — the weather service isn't responding."
    if not place:
        return f"I couldn't find a place called {location}."

    try:
        current = await _current_conditions(place["latitude"], place["longitude"])
    except httpx.HTTPError:
        return "I couldn't check the weather right now — the weather service isn't responding."

    condition = _CONDITIONS.get(current["weather_code"], "unusual conditions")
    temp = round(current["temperature_2m"])
    feels_like = round(current["apparent_temperature"])
    wind = round(current["wind_speed_10m"])
    name = place.get("name", location)
    region = place.get("country", "")
    feel_note = f", feels like {feels_like}" if abs(feels_like - temp) >= 3 else ""

    return (
        f"It's {temp}°F{feel_note} and {condition} in {name}"
        f"{', ' + region if region else ''}, with wind around {wind} mph."
    )
