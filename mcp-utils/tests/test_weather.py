import httpx
import pytest

from mcp_utils import weather


def _install(monkeypatch: pytest.MonkeyPatch, handler: httpx.MockTransport) -> None:
    monkeypatch.setattr(weather, "make_client", lambda: httpx.AsyncClient(transport=handler))


async def test_weather_for_london(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "geocoding" in request.url.host:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "London",
                            "country": "United Kingdom",
                            "latitude": 51.5,
                            "longitude": -0.12,
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "current": {
                    "time": "2026-11-11T10:00",
                    "temperature_2m": 11.2,
                    "apparent_temperature": 9.8,
                    "relative_humidity_2m": 80,
                    "wind_speed_10m": 14.5,
                    "weather_code": 61,
                }
            },
        )

    _install(monkeypatch, httpx.MockTransport(handler))
    out = await weather.get_weather("London")
    assert "London, United Kingdom" in out
    assert "°C" in out
    assert "slight rain" in out
    assert seen[0].url.params["count"] == "1"
    assert seen[0].url.params["name"] == "London"
    assert seen[1].url.params["timezone"] == "auto"


async def test_unknown_place(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    out = await weather.get_weather("Atlantis")
    assert out == "Error: couldn't find a place called 'Atlantis'."


async def test_service_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, httpx.MockTransport(lambda r: httpx.Response(503)))
    out = await weather.get_weather("London")
    assert out == "Error: the weather service is unavailable right now."


async def test_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    _install(monkeypatch, httpx.MockTransport(handler))
    out = await weather.get_weather("London")
    assert out == "Error: the weather service is unavailable right now."
