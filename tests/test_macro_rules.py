from __future__ import annotations

from datetime import datetime, timezone

from bot_xauusd.models import ImpactLevel, MacroEvent
from bot_xauusd.signals.macro_rules import DEFAULT_RULES, MacroRule, MacroRuleEngine


def make_event(
    evento: str,
    pais: str = "USD",
    valor_real: float | None = None,
    valor_esperado: float | None = None,
    valor_previo: float | None = None,
) -> MacroEvent:
    return MacroEvent(
        evento=evento,
        fecha=datetime(2026, 1, 1, tzinfo=timezone.utc),
        pais=pais,
        impacto=ImpactLevel.ALTO,
        valor_esperado=valor_esperado,
        valor_real=valor_real,
        valor_previo=valor_previo,
    )


def test_engine_returns_neutral_score_when_no_rule_applies() -> None:
    engine = MacroRuleEngine()
    resultado = engine.evaluate([make_event("Evento irrelevante sin datos")])
    assert resultado.score == 0.0
    assert resultado.resultados == []


def test_cpi_above_expected_is_bearish_for_gold() -> None:
    engine = MacroRuleEngine(reglas=[r for r in DEFAULT_RULES if r.nombre == "inflacion_cpi_pce"])
    eventos = [make_event("CPI m/m", valor_real=0.5, valor_esperado=0.3)]
    resultado = engine.evaluate(eventos)
    assert resultado.score < 0


def test_cpi_below_expected_is_bullish_for_gold() -> None:
    engine = MacroRuleEngine(reglas=[r for r in DEFAULT_RULES if r.nombre == "inflacion_cpi_pce"])
    eventos = [make_event("CPI m/m", valor_real=0.1, valor_esperado=0.3)]
    resultado = engine.evaluate(eventos)
    assert resultado.score > 0


def test_strong_nfp_is_bearish_for_gold() -> None:
    engine = MacroRuleEngine(reglas=[r for r in DEFAULT_RULES if r.nombre == "empleo_nfp"])
    eventos = [make_event("Non-Farm Employment Change", valor_real=250_000, valor_esperado=175_000)]
    resultado = engine.evaluate(eventos)
    assert resultado.score < 0


def test_hawkish_fed_surprise_is_bearish_for_gold() -> None:
    engine = MacroRuleEngine(reglas=[r for r in DEFAULT_RULES if r.nombre == "tasa_fed"])
    eventos = [make_event("Federal Funds Rate", valor_real=4.00, valor_esperado=3.75)]
    resultado = engine.evaluate(eventos)
    assert resultado.score < 0


def test_rising_dxy_is_bearish_for_gold() -> None:
    engine = MacroRuleEngine(reglas=[r for r in DEFAULT_RULES if r.nombre == "dxy_confirmacion"])
    eventos = [make_event("Índice del dólar (DXY, ponderado por comercio)", valor_real=105.0, valor_previo=104.0)]
    resultado = engine.evaluate(eventos)
    assert resultado.score < 0


def test_rising_vix_is_bullish_for_gold() -> None:
    engine = MacroRuleEngine(reglas=[r for r in DEFAULT_RULES if r.nombre == "riesgo_vix"])
    eventos = [make_event("Índice de volatilidad VIX (aversión al riesgo)", valor_real=25.0, valor_previo=18.0)]
    resultado = engine.evaluate(eventos)
    assert resultado.score > 0


def test_falling_vix_is_bearish_for_gold() -> None:
    engine = MacroRuleEngine(reglas=[r for r in DEFAULT_RULES if r.nombre == "riesgo_vix"])
    eventos = [make_event("Índice de volatilidad VIX (aversión al riesgo)", valor_real=15.0, valor_previo=20.0)]
    resultado = engine.evaluate(eventos)
    assert resultado.score < 0


def test_negative_news_sentiment_is_bullish_for_gold() -> None:
    engine = MacroRuleEngine(reglas=[r for r in DEFAULT_RULES if r.nombre == "sentimiento_noticias"])
    eventos = [make_event("Sentimiento de noticias (mercados financieros)", valor_real=-0.6)]
    resultado = engine.evaluate(eventos)
    assert resultado.score > 0


def test_positive_news_sentiment_is_bearish_for_gold() -> None:
    engine = MacroRuleEngine(reglas=[r for r in DEFAULT_RULES if r.nombre == "sentimiento_noticias"])
    eventos = [make_event("Sentimiento de noticias (mercados financieros)", valor_real=0.6)]
    resultado = engine.evaluate(eventos)
    assert resultado.score < 0


def test_non_usd_events_are_ignored_by_rules() -> None:
    engine = MacroRuleEngine(reglas=[r for r in DEFAULT_RULES if r.nombre == "inflacion_cpi_pce"])
    eventos = [make_event("CPI y/y", pais="EUR", valor_real=5.0, valor_esperado=1.0)]
    resultado = engine.evaluate(eventos)
    assert resultado.score == 0.0
    assert resultado.resultados == []


def test_combined_score_is_weighted_average_of_applicable_rules_only() -> None:
    reglas = [
        MacroRule("solo_bajista", peso=1.0, evaluar=lambda eventos: (-1.0, "siempre bajista")),
        MacroRule("no_aplica", peso=5.0, evaluar=lambda eventos: None),
    ]
    engine = MacroRuleEngine(reglas=reglas)
    resultado = engine.evaluate([])
    assert resultado.score == -1.0
    assert len(resultado.resultados) == 1


def test_score_is_clamped_to_valid_range() -> None:
    reglas = [MacroRule("extrema", peso=1.0, evaluar=lambda eventos: (-5.0, "fuera de rango"))]
    engine = MacroRuleEngine(reglas=reglas)
    resultado = engine.evaluate([])
    assert resultado.score == -1.0
