from __future__ import annotations

from bot_xauusd.models import SignalDirection
from bot_xauusd.signals.decision_engine import DecisionConfig, DecisionEngine
from bot_xauusd.signals.indicators import TrendDirection
from bot_xauusd.signals.macro_rules import MacroScoreResult
from bot_xauusd.signals.technical_trend import TechnicalScoreResult


class FakeMacroEngine:
    def __init__(self, score: float) -> None:
        self._resultado = MacroScoreResult(score=score, resultados=[])

    def evaluate(self, eventos):
        return self._resultado


class FakeTechnicalEngine:
    def __init__(self, score: float, tendencia_h4: TrendDirection, tendencia_h1: TrendDirection | None = None) -> None:
        self._resultado = TechnicalScoreResult(
            score=score,
            tendencia_h4=tendencia_h4,
            tendencia_h1=tendencia_h1 or tendencia_h4,
            estructura_h1=TrendDirection.NEUTRAL,
            rsi_h1=50.0,
            atr_h1=1.0,
            razones=[],
        )

    def evaluate(self, h4_bars, h1_bars):
        return self._resultado


DEFAULT_CONFIG = DecisionConfig(peso_macro=0.5, peso_tecnico=0.5, umbral_compra=0.5, umbral_venta=-0.5)


def make_engine(macro_score: float, tecnico_score: float, tendencia_h4: TrendDirection) -> DecisionEngine:
    return DecisionEngine(
        macro_engine=FakeMacroEngine(macro_score),
        technical_engine=FakeTechnicalEngine(tecnico_score, tendencia_h4),
        config=DEFAULT_CONFIG,
    )


def test_h4_alcista_with_strong_combined_score_yields_long() -> None:
    engine = make_engine(macro_score=0.8, tecnico_score=1.0, tendencia_h4=TrendDirection.ALCISTA)
    señal = engine.evaluate([], [], [])
    assert señal.direccion == SignalDirection.LONG


def test_h4_alcista_but_weak_score_does_not_trade() -> None:
    engine = make_engine(macro_score=0.1, tecnico_score=0.2, tendencia_h4=TrendDirection.ALCISTA)
    señal = engine.evaluate([], [], [])
    assert señal.direccion == SignalDirection.NONE


def test_never_shorts_against_a_bullish_h4_filter_even_with_bearish_combined_score() -> None:
    # macro muy bajista + técnico ligeramente bajista => score_final < umbral_venta,
    # lo que en aislamiento "querría" ser SHORT. Pero H4 es alcista, así que la
    # spec (4.8) exige NO operar, nunca invertir la dirección.
    engine = make_engine(macro_score=-1.0, tecnico_score=-0.2, tendencia_h4=TrendDirection.ALCISTA)
    señal = engine.evaluate([], [], [])
    assert señal.score_final < DEFAULT_CONFIG.umbral_venta
    assert señal.direccion == SignalDirection.NONE
    assert señal.direccion != SignalDirection.SHORT


def test_h4_bajista_with_strong_combined_score_yields_short() -> None:
    engine = make_engine(macro_score=-0.8, tecnico_score=-1.0, tendencia_h4=TrendDirection.BAJISTA)
    señal = engine.evaluate([], [], [])
    assert señal.direccion == SignalDirection.SHORT


def test_never_longs_against_a_bearish_h4_filter_even_with_bullish_combined_score() -> None:
    engine = make_engine(macro_score=1.0, tecnico_score=0.2, tendencia_h4=TrendDirection.BAJISTA)
    señal = engine.evaluate([], [], [])
    assert señal.score_final > DEFAULT_CONFIG.umbral_compra
    assert señal.direccion == SignalDirection.NONE
    assert señal.direccion != SignalDirection.LONG


def test_h4_neutral_never_trades_regardless_of_scores() -> None:
    engine = make_engine(macro_score=1.0, tecnico_score=1.0, tendencia_h4=TrendDirection.NEUTRAL)
    señal = engine.evaluate([], [], [])
    assert señal.direccion == SignalDirection.NONE


def test_signal_carries_reasoning_for_logging() -> None:
    engine = make_engine(macro_score=0.8, tecnico_score=1.0, tendencia_h4=TrendDirection.ALCISTA)
    señal = engine.evaluate([], [], [])
    assert len(señal.razones) > 0
    assert any("señal_final" in razon for razon in señal.razones)
