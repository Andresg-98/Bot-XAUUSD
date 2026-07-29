from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bot_xauusd.models import PriceBar
from bot_xauusd.signals.indicators import TrendDirection
from bot_xauusd.signals.technical_trend import TechnicalTrendEngine


def make_trending_bars(count: int, start: float, step: float) -> list[PriceBar]:
    """Serie monótona (up si step>0, down si step<0) para forzar un cruce claro de EMA50/200."""
    bars = []
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(count):
        close = start + step * i
        bars.append(
            PriceBar(
                symbol="XAUUSD",
                timeframe="H1",
                time=base_time + timedelta(hours=i),
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=100,
            )
        )
    return bars


def test_bullish_h4_and_h1_agreement_yields_positive_score() -> None:
    h4_bars = make_trending_bars(250, start=1900, step=0.5)
    h1_bars = make_trending_bars(250, start=1900, step=0.5)

    resultado = TechnicalTrendEngine().evaluate(h4_bars, h1_bars)

    assert resultado.tendencia_h4 == TrendDirection.ALCISTA
    assert resultado.tendencia_h1 == TrendDirection.ALCISTA
    assert resultado.score > 0


def test_bearish_h4_and_h1_agreement_yields_negative_score() -> None:
    h4_bars = make_trending_bars(250, start=2200, step=-0.5)
    h1_bars = make_trending_bars(250, start=2200, step=-0.5)

    resultado = TechnicalTrendEngine().evaluate(h4_bars, h1_bars)

    assert resultado.tendencia_h4 == TrendDirection.BAJISTA
    assert resultado.tendencia_h1 == TrendDirection.BAJISTA
    assert resultado.score < 0


def test_h1_disagreement_with_h4_blocks_the_signal() -> None:
    h4_bars = make_trending_bars(250, start=1900, step=0.5)  # H4 alcista
    h1_bars = make_trending_bars(250, start=2200, step=-0.5)  # H1 bajista

    resultado = TechnicalTrendEngine().evaluate(h4_bars, h1_bars)

    assert resultado.tendencia_h4 == TrendDirection.ALCISTA
    assert resultado.tendencia_h1 == TrendDirection.BAJISTA
    assert resultado.score == 0.0


def test_flat_series_is_neutral_and_blocks_the_signal() -> None:
    h4_bars = make_trending_bars(250, start=1900, step=0.0)
    h1_bars = make_trending_bars(250, start=1900, step=0.0)

    resultado = TechnicalTrendEngine().evaluate(h4_bars, h1_bars)

    assert resultado.tendencia_h4 == TrendDirection.NEUTRAL
    assert resultado.score == 0.0


def test_overbought_rsi_dampens_a_bullish_score() -> None:
    # Tendencia alcista sostenida (EMA50>EMA200) pero con una subida final fuerte
    # y reciente para forzar RSI en sobrecompra.
    h4_bars = make_trending_bars(250, start=1900, step=0.5)
    h1_bars = make_trending_bars(240, start=1900, step=0.5)
    h1_bars += make_trending_bars(10, start=h1_bars[-1].close, step=5.0)

    resultado = TechnicalTrendEngine().evaluate(h4_bars, h1_bars)

    assert resultado.rsi_h1 is not None
    assert resultado.rsi_h1 >= 70.0
    # Sin amortiguación el score clampearía a 1.0 (base 1.0, o 1.2 con estructura); con
    # el factor de amortiguación de sobrecompra (0.5x) debe quedar estrictamente por debajo.
    assert 0 < resultado.score < 1.0
