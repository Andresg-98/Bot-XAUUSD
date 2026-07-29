from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

from ..models import PriceBar

BASE_URL = "https://api.twelvedata.com/time_series"

_INTERVALS: dict[str, str] = {"H1": "1h", "H4": "4h", "D1": "1day"}
_MAX_OUTPUTSIZE = 5000


class TwelveDataApiError(RuntimeError):
    """La API de TwelveData respondió con un error."""


class TwelveDataPriceClient:
    """
    Cliente de precio histórico vía TwelveData (spec_bot_xauusd.md, sección 5:
    "Datos de mercado: API del broker o proveedor tipo TwelveData/Polygon").

    Se usa SOLO para el histórico de backtesting (Fase 3): el broker MT5 del
    usuario puede no conservar suficiente historial (ver README.md). La
    ejecución en vivo sigue siendo exclusivamente vía MT5 (spec 4.6) — este
    cliente nunca coloca ni gestiona órdenes.
    """

    def __init__(
        self,
        api_key: str,
        symbol: str = "XAU/USD",
        session: requests.Session | None = None,
        timeout: float = 15.0,
        pausa_entre_paginas: float = 8.0,
    ) -> None:
        if not api_key:
            raise ValueError("Se requiere TWELVEDATA_API_KEY (ver .env.example).")
        self._api_key = api_key
        self._symbol = symbol
        self._session = session or requests.Session()
        self._timeout = timeout
        self._pausa = pausa_entre_paginas

    def _request(self, interval: str, **params: object) -> dict:
        query = {"symbol": self._symbol, "interval": interval, "apikey": self._api_key, "timezone": "UTC", **params}
        response = self._session.get(BASE_URL, params=query, timeout=self._timeout)
        if response.status_code != 200:
            raise TwelveDataApiError(f"TwelveData respondió {response.status_code}: {response.text[:200]}")
        payload = response.json()
        if isinstance(payload, dict) and payload.get("status") == "error":
            raise TwelveDataApiError(f"TwelveData: {payload.get('message', payload)}")
        return payload

    def get_historical_bars(
        self,
        timeframe: str,
        start: datetime,
        end: datetime,
        outputsize: int = _MAX_OUTPUTSIZE,
    ) -> list[PriceBar]:
        """
        Trae velas históricas paginando hacia atrás desde `end` hasta `start`.
        Respeta el límite de peticiones del tier gratuito con una pausa entre
        páginas (por defecto 8s); para 2-3 años de H1 esto puede tardar varios
        minutos — es esperado, correr una sola vez y cachear el resultado.
        """
        interval = _INTERVALS.get(timeframe)
        if interval is None:
            raise ValueError(f"Timeframe no soportado: '{timeframe}' (usa {list(_INTERVALS)})")

        barras: dict[datetime, PriceBar] = {}
        cursor_fin = end
        while cursor_fin > start:
            payload = self._request(
                interval,
                start_date=start.strftime("%Y-%m-%d %H:%M:%S"),
                end_date=cursor_fin.strftime("%Y-%m-%d %H:%M:%S"),
                outputsize=outputsize,
            )
            valores = payload.get("values") or []
            if not valores:
                break

            momentos_pagina: list[datetime] = []
            for v in valores:
                momento = datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                momentos_pagina.append(momento)
                barras[momento] = PriceBar(
                    symbol=self._symbol,
                    timeframe=timeframe,
                    time=momento,
                    open=float(v["open"]),
                    high=float(v["high"]),
                    low=float(v["low"]),
                    close=float(v["close"]),
                    volume=float(v.get("volume") or 0.0),
                )

            nuevo_cursor = min(momentos_pagina)
            if nuevo_cursor >= cursor_fin:
                break  # protección ante una API que no avanza, evita loop infinito
            cursor_fin = nuevo_cursor

            if len(valores) < outputsize:
                break  # última página disponible

            time.sleep(self._pausa)

        return [barras[momento] for momento in sorted(barras)]
