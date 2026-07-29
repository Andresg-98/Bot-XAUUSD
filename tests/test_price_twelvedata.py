from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot_xauusd.ingestion.price_twelvedata import TwelveDataApiError, TwelveDataPriceClient


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class FakeSession:
    """Simula dos páginas de resultados (más recientes -> más antiguos) sin dormir entre ellas."""

    def __init__(self, pages: list[dict]) -> None:
        self._pages = pages
        self.calls: list[dict] = []

    def get(self, url: str, params: dict, timeout: float) -> FakeResponse:
        self.calls.append(params)
        page = self._pages[len(self.calls) - 1]
        return FakeResponse(page)


def value(dt: str, close: float) -> dict:
    return {"datetime": dt, "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": "100"}


def test_get_historical_bars_single_page() -> None:
    payload = {"status": "ok", "values": [value("2026-01-02 01:00:00", 2000.0), value("2026-01-01 00:00:00", 1990.0)]}
    session = FakeSession([payload])
    client = TwelveDataPriceClient(api_key="fake-key", session=session, pausa_entre_paginas=0)

    barras = client.get_historical_bars(
        "H1", start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 3, tzinfo=timezone.utc)
    )

    assert len(barras) == 2
    assert barras[0].time < barras[1].time  # orden ascendente
    assert barras[0].close == 1990.0
    assert barras[1].close == 2000.0


def test_get_historical_bars_paginates_backwards() -> None:
    pagina_1 = {
        "status": "ok",
        "values": [value("2026-01-05 00:00:00", 2010.0), value("2026-01-04 00:00:00", 2005.0)],
    }
    pagina_2 = {
        "status": "ok",
        "values": [value("2026-01-03 00:00:00", 2000.0), value("2026-01-02 00:00:00", 1995.0)],
    }
    session = FakeSession([pagina_1, pagina_2])
    client = TwelveDataPriceClient(api_key="fake-key", session=session, pausa_entre_paginas=0)

    barras = client.get_historical_bars(
        "H1",
        start=datetime(2026, 1, 2, tzinfo=timezone.utc),
        end=datetime(2026, 1, 5, tzinfo=timezone.utc),
        outputsize=2,
    )

    assert [b.close for b in barras] == [1995.0, 2000.0, 2005.0, 2010.0]
    assert len(session.calls) == 2
    # la segunda página pide datos hasta el borde más antiguo devuelto por la primera
    assert session.calls[1]["end_date"] == "2026-01-04 00:00:00"


def test_missing_api_key_raises() -> None:
    with pytest.raises(ValueError):
        TwelveDataPriceClient(api_key="")


def test_error_status_in_payload_raises() -> None:
    session = FakeSession([{"status": "error", "message": "clave inválida"}])
    client = TwelveDataPriceClient(api_key="fake-key", session=session, pausa_entre_paginas=0)
    with pytest.raises(TwelveDataApiError):
        client.get_historical_bars(
            "H1", start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 2, tzinfo=timezone.utc)
        )


def test_non_200_response_raises() -> None:
    class ErrorSession:
        def get(self, url, params, timeout):
            return FakeResponse({"error": "boom"}, status_code=500)

    client = TwelveDataPriceClient(api_key="fake-key", session=ErrorSession(), pausa_entre_paginas=0)
    with pytest.raises(TwelveDataApiError):
        client.get_historical_bars(
            "H1", start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 2, tzinfo=timezone.utc)
        )


def test_invalid_timeframe_raises_value_error() -> None:
    client = TwelveDataPriceClient(api_key="fake-key", session=FakeSession([]), pausa_entre_paginas=0)
    with pytest.raises(ValueError):
        client.get_historical_bars(
            "M1", start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 2, tzinfo=timezone.utc)
        )
