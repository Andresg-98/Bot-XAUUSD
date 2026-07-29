from __future__ import annotations

import pytest

from bot_xauusd.ingestion.macro_forexfactory import ForexFactoryApiError, ForexFactoryClient, _parse_value
from bot_xauusd.models import ImpactLevel


class FakeResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> object:
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.requested_urls: list[str] = []
        self.requested_headers: list[dict] = []

    def get(self, url: str, timeout: float, headers: dict) -> FakeResponse:
        self.requested_urls.append(url)
        self.requested_headers.append(headers)
        return self._response


RAW_EVENT = {
    "title": "Non-Farm Payrolls",
    "country": "USD",
    "date": "2026-08-07T08:30:00-04:00",
    "impact": "High",
    "forecast": "175K",
    "previous": "150K",
    "actual": "",
}


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("175K", 175000.0),
        ("1.2M", 1200000.0),
        ("2.1%", 2.1),
        ("-0.3%", -0.3),
        ("85.6", 85.6),
        ("", None),
        ("N/A", None),
        (None, None),
    ],
)
def test_parse_value(raw: object, expected: float | None) -> None:
    assert _parse_value(raw) == expected


def test_get_calendar_maps_fields() -> None:
    session = FakeSession(FakeResponse([RAW_EVENT]))
    client = ForexFactoryClient(session=session)

    eventos = client.get_calendar("this_week")

    assert len(eventos) == 1
    evento = eventos[0]
    assert evento.evento == "Non-Farm Payrolls"
    assert evento.pais == "USD"
    assert evento.impacto == ImpactLevel.ALTO
    assert evento.valor_esperado == 175000.0
    assert evento.valor_previo == 150000.0
    assert evento.valor_real is None
    assert evento.fuente == "ForexFactory"
    assert evento.fecha.year == 2026 and evento.fecha.month == 8
    assert "bot-xauusd" in session.requested_headers[0]["User-Agent"]


def test_unknown_impact_defaults_to_bajo() -> None:
    raw = {**RAW_EVENT, "impact": "Holiday"}
    session = FakeSession(FakeResponse([raw]))
    client = ForexFactoryClient(session=session)

    evento = client.get_calendar()[0]

    assert evento.impacto == ImpactLevel.BAJO


def test_invalid_window_raises_value_error() -> None:
    client = ForexFactoryClient(session=FakeSession(FakeResponse([])))
    with pytest.raises(ValueError):
        client.get_calendar("tomorrow")


def test_non_200_response_raises() -> None:
    session = FakeSession(FakeResponse({"error": "blocked"}, status_code=403))
    client = ForexFactoryClient(session=session)
    with pytest.raises(ForexFactoryApiError):
        client.get_calendar()


def test_non_list_payload_raises() -> None:
    session = FakeSession(FakeResponse({"not": "a list"}))
    client = ForexFactoryClient(session=session)
    with pytest.raises(ForexFactoryApiError):
        client.get_calendar()
