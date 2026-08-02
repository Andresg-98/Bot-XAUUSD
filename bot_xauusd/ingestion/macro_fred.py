from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import requests

from ..models import ImpactLevel, MacroEvent

FRED_BASE_URL = "https://api.stlouisfed.org/fred"


@dataclass(frozen=True)
class WatchedSeries:
    series_id: str
    nombre: str
    pais: str
    impacto: ImpactLevel


# Series vigiladas porque el motor de reglas macro (spec_bot_xauusd.md, 4.2) las usa
# como disparadores de sesgo: CPI/PCE, NFP, tasa de la Fed, el índice del dólar y
# el VIX (proxy cuantitativo de aversión al riesgo — ver macro_rules.py).
WATCHED_SERIES: tuple[WatchedSeries, ...] = (
    WatchedSeries("CPIAUCSL", "CPI (Índice de Precios al Consumidor)", "US", ImpactLevel.ALTO),
    WatchedSeries("PCEPILFE", "Core PCE (gasto de consumo subyacente)", "US", ImpactLevel.ALTO),
    WatchedSeries("PAYEMS", "Nóminas no agrícolas (NFP)", "US", ImpactLevel.ALTO),
    WatchedSeries("UNRATE", "Tasa de desempleo", "US", ImpactLevel.MEDIO),
    WatchedSeries("DFF", "Tasa de fondos federales (Fed Funds Rate)", "US", ImpactLevel.ALTO),
    WatchedSeries("DTWEXBGS", "Índice del dólar (DXY, ponderado por comercio)", "US", ImpactLevel.MEDIO),
    WatchedSeries("VIXCLS", "Índice de volatilidad VIX (aversión al riesgo)", "US", ImpactLevel.MEDIO),
)


class FredApiError(RuntimeError):
    """La API de FRED respondió con un error o un payload inesperado."""


class FredMacroClient:
    """
    Cliente de ingesta para la API de FRED (Federal Reserve Economic Data).

    Limitación conocida frente al "output esperado del módulo" de la sección 4.1
    de spec_bot_xauusd.md: FRED no publica valores de consenso ("esperado") ni un
    calendario de eventos futuros con hora exacta — solo series históricas ya
    publicadas. Por eso `valor_esperado` queda siempre en None y `fecha` es la
    fecha de la observación publicada, no la hora de un evento futuro. El motor
    de reglas macro (fase 2) debe operar solo con real vs. previo.
    """

    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not api_key:
            raise ValueError("Se requiere FRED_API_KEY (ver .env.example).")
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timeout = timeout

    def _get(self, path: str, **params: object) -> dict:
        params = {**params, "api_key": self._api_key, "file_type": "json"}
        response = self._session.get(f"{FRED_BASE_URL}/{path}", params=params, timeout=self._timeout)
        if response.status_code != 200:
            raise FredApiError(f"FRED respondió {response.status_code} en '{path}': {response.text[:200]}")
        return response.json()

    def get_latest_events(self, series: Iterable[WatchedSeries] = WATCHED_SERIES) -> list[MacroEvent]:
        """Trae el último valor publicado y el previo para cada serie vigilada."""
        eventos: list[MacroEvent] = []
        for serie in series:
            data = self._get(
                "series/observations",
                series_id=serie.series_id,
                sort_order="desc",
                limit=2,
            )
            observaciones = [o for o in data.get("observations", []) if o.get("value") not in (None, ".")]

            valor_real = float(observaciones[0]["value"]) if len(observaciones) > 0 else None
            valor_previo = float(observaciones[1]["value"]) if len(observaciones) > 1 else None
            fecha_str = observaciones[0]["date"] if observaciones else None
            fecha = (
                datetime.strptime(fecha_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if fecha_str
                else datetime.now(timezone.utc)
            )

            eventos.append(
                MacroEvent(
                    evento=serie.nombre,
                    fecha=fecha,
                    pais=serie.pais,
                    impacto=serie.impacto,
                    valor_esperado=None,
                    valor_real=valor_real,
                    valor_previo=valor_previo,
                )
            )
        return eventos
