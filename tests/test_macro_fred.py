from __future__ import annotations

import pytest

from bot_xauusd.ingestion.macro_fred import FredApiError, FredMacroClient, WatchedSeries
from bot_xauusd.models import ImpactLevel


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.requested_paths: list[str] = []

    def get(self, url: str, params: dict, timeout: float) -> FakeResponse:
        self.requested_paths.append(url)
        return self._response


def test_get_latest_events_maps_actual_and_previous_values() -> None:
    payload = {
        "observations": [
            {"date": "2026-06-01", "value": "3.1"},
            {"date": "2026-05-01", "value": "3.0"},
        ]
    }
    session = FakeSession(FakeResponse(payload))
    client = FredMacroClient(api_key="fake-key", session=session)
    serie = WatchedSeries("CPIAUCSL", "CPI", "US", ImpactLevel.ALTO)

    eventos = client.get_latest_events(series=[serie])

    assert len(eventos) == 1
    evento = eventos[0]
    assert evento.evento == "CPI"
    assert evento.pais == "US"
    assert evento.impacto == ImpactLevel.ALTO
    assert evento.valor_real == 3.1
    assert evento.valor_previo == 3.0
    assert evento.valor_esperado is None
    assert evento.fecha.year == 2026 and evento.fecha.month == 6


def test_missing_observations_yields_none_values() -> None:
    session = FakeSession(FakeResponse({"observations": []}))
    client = FredMacroClient(api_key="fake-key", session=session)
    serie = WatchedSeries("CPIAUCSL", "CPI", "US", ImpactLevel.ALTO)

    eventos = client.get_latest_events(series=[serie])

    assert eventos[0].valor_real is None
    assert eventos[0].valor_previo is None


def test_missing_api_key_raises() -> None:
    with pytest.raises(ValueError):
        FredMacroClient(api_key="")


def test_non_200_response_raises_fred_api_error() -> None:
    session = FakeSession(FakeResponse({"error_message": "bad request"}, status_code=400))
    client = FredMacroClient(api_key="fake-key", session=session)
    serie = WatchedSeries("CPIAUCSL", "CPI", "US", ImpactLevel.ALTO)

    with pytest.raises(FredApiError):
        client.get_latest_events(series=[serie])
