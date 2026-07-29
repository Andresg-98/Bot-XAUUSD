from __future__ import annotations

from datetime import datetime, timezone

import requests

from ..models import ImpactLevel, MacroEvent

# Feed JSON que alimenta el propio widget embebible de ForexFactory, operado por
# Fair Economy. ForexFactory no tiene API pública oficial (spec_bot_xauusd.md, 4.1)
# y bloquea activamente el scraping de su sitio; este feed evita tocar el HTML de
# forexfactory.com, pero tampoco es una API documentada/licenciada para terceros.
# Revisa el aviso en README.md antes de usarlo con una cuenta real.
FEED_URLS: dict[str, str] = {
    "last_week": "https://nfs.faireconomy.media/ff_calendar_lastweek.json",
    "this_week": "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "next_week": "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
}

_IMPACT_MAP: dict[str, ImpactLevel] = {
    "high": ImpactLevel.ALTO,
    "medium": ImpactLevel.MEDIO,
    "low": ImpactLevel.BAJO,
}

_SUFFIX_MULTIPLIERS: dict[str, float] = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def _parse_value(raw: object) -> float | None:
    """Convierte valores tipo '175K', '2.1%', '-0.3%' o '85.6' a float."""
    if raw in (None, "", "N/A"):
        return None
    text = str(raw).strip().replace(",", "")
    if not text:
        return None
    multiplier = 1.0
    suffix = text[-1].upper()
    if suffix in _SUFFIX_MULTIPLIERS:
        multiplier = _SUFFIX_MULTIPLIERS[suffix]
        text = text[:-1]
    text = text.rstrip("%")
    try:
        return float(text) * multiplier
    except ValueError:
        return None


class ForexFactoryApiError(RuntimeError):
    """El feed de calendario de ForexFactory/Fair Economy respondió con un error."""


class ForexFactoryClient:
    """
    Cliente de ingesta para el calendario económico de ForexFactory en tiempo real.

    A diferencia de FRED, este feed sí trae valor de consenso ('forecast'), así
    que `valor_esperado` viene poblado — coincide con el "output esperado del
    módulo" ideal de la sección 4.1 de spec_bot_xauusd.md. `valor_real` queda en
    None hasta que el dato se publica (el feed lo actualiza automáticamente).
    """

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0) -> None:
        self._session = session or requests.Session()
        self._timeout = timeout

    def _get_feed(self, window: str) -> list[dict]:
        url = FEED_URLS.get(window)
        if url is None:
            raise ValueError(f"Ventana no soportada: '{window}' (usa {list(FEED_URLS)})")
        response = self._session.get(
            url, timeout=self._timeout, headers={"User-Agent": "bot-xauusd/0.1 (+ingesta macro)"}
        )
        if response.status_code != 200:
            raise ForexFactoryApiError(f"El feed de FF respondió {response.status_code}: {response.text[:200]}")
        payload = response.json()
        if not isinstance(payload, list):
            raise ForexFactoryApiError("Formato de feed inesperado (se esperaba una lista de eventos).")
        return payload

    def get_calendar(self, window: str = "this_week") -> list[MacroEvent]:
        eventos: list[MacroEvent] = []
        for raw in self._get_feed(window):
            impacto = _IMPACT_MAP.get(str(raw.get("impact", "")).strip().lower(), ImpactLevel.BAJO)
            fecha_str = raw.get("date")
            try:
                fecha = datetime.fromisoformat(fecha_str) if fecha_str else datetime.now(timezone.utc)
            except ValueError:
                fecha = datetime.now(timezone.utc)

            eventos.append(
                MacroEvent(
                    evento=str(raw.get("title", "")).strip(),
                    fecha=fecha,
                    pais=str(raw.get("country", "")).strip(),
                    impacto=impacto,
                    valor_esperado=_parse_value(raw.get("forecast")),
                    valor_real=_parse_value(raw.get("actual")),
                    valor_previo=_parse_value(raw.get("previous")),
                    fuente="ForexFactory",
                )
            )
        return eventos
