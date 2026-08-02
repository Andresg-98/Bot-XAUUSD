from __future__ import annotations

from datetime import datetime, timezone

import requests

from ..models import ImpactLevel, MacroEvent

BASE_URL = "https://www.alphavantage.co/query"


class NewsSentimentApiError(RuntimeError):
    """La API de Alpha Vantage respondió con un error."""


class AlphaVantageNewsSentimentClient:
    """
    Cliente de sentimiento de noticias vía Alpha Vantage NEWS_SENTIMENT
    (spec_bot_xauusd.md, 4.2 — refuerzo de la regla de "aversión al riesgo",
    junto al VIX en macro_fred.py). Devuelve un `MacroEvent` sintético con el
    sentimiento agregado como `valor_real`, para encajar en el mismo pipeline
    que ForexFactory/FRED sin cambiar el resto del motor de reglas.

    **Tier gratuito muy limitado** (históricamente 25 peticiones/día) — el
    llamador (bot_xauusd/live/loop.py) debe cachear agresivamente (horas, no
    minutos) para no agotar la cuota diaria.
    """

    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        timeout: float = 15.0,
        topics: str = "financial_markets,economy_macro",
    ) -> None:
        if not api_key:
            raise ValueError("Se requiere ALPHAVANTAGE_API_KEY (ver .env.example).")
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timeout = timeout
        self._topics = topics

    def _obtener_sentimiento_agregado(self, limit: int) -> float | None:
        params = {
            "function": "NEWS_SENTIMENT",
            "topics": self._topics,
            "sort": "LATEST",
            "limit": limit,
            "apikey": self._api_key,
        }
        response = self._session.get(BASE_URL, params=params, timeout=self._timeout)
        if response.status_code != 200:
            raise NewsSentimentApiError(f"Alpha Vantage respondió {response.status_code}: {response.text[:200]}")

        payload = response.json()
        # Alpha Vantage devuelve 200 OK incluso cuando se excede la cuota o la
        # api_key es inválida — el detalle viene en estos campos, no en el
        # status HTTP.
        if "Error Message" in payload or "Information" in payload:
            raise NewsSentimentApiError(f"Alpha Vantage: {payload.get('Information') or payload.get('Error Message')}")

        feed = payload.get("feed") or []
        scores = [float(item["overall_sentiment_score"]) for item in feed if "overall_sentiment_score" in item]
        if not scores:
            return None
        return sum(scores) / len(scores)

    def get_latest_events(self, limit: int = 50) -> list[MacroEvent]:
        """Un único `MacroEvent` sintético con el sentimiento agregado más
        reciente, o lista vacía si no hay artículos disponibles."""
        sentimiento = self._obtener_sentimiento_agregado(limit)
        if sentimiento is None:
            return []
        return [
            MacroEvent(
                evento="Sentimiento de noticias (mercados financieros)",
                fecha=datetime.now(timezone.utc),
                pais="US",
                impacto=ImpactLevel.MEDIO,
                valor_esperado=None,
                valor_real=sentimiento,
                valor_previo=None,
                fuente="Alpha Vantage",
            )
        ]
