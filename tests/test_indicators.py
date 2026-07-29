from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot_xauusd.models import PriceBar
from bot_xauusd.signals.indicators import TrendDirection, atr, detect_structure, ema, rsi


def make_bar(high: float, low: float, close: float, i: int = 0) -> PriceBar:
    return PriceBar(
        symbol="XAUUSD",
        timeframe="H1",
        time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=close,
        high=high,
        low=low,
        close=close,
        volume=i,
    )


def test_ema_constant_series_equals_the_constant() -> None:
    assert ema([100.0] * 10, period=5) == pytest.approx(100.0)


def test_ema_hand_computed_example() -> None:
    # SMA(1,2,3)=2.0; mult=0.5; (4-2)*0.5+2=3.0; (5-3)*0.5+3=4.0
    assert ema([1, 2, 3, 4, 5], period=3) == pytest.approx(4.0)


def test_ema_raises_with_insufficient_values() -> None:
    with pytest.raises(ValueError):
        ema([1, 2], period=5)


def test_atr_hand_computed_example() -> None:
    bars = [
        make_bar(high=10, low=8, close=9),
        make_bar(high=11, low=9, close=10),
        make_bar(high=12, low=10, close=11),
        make_bar(high=13, low=11, close=12),
    ]
    # TR de cada vela (2,3,4) contra la previa = 2.0 en los tres casos
    assert atr(bars, period=3) == pytest.approx(2.0)


def test_atr_raises_with_insufficient_bars() -> None:
    with pytest.raises(ValueError):
        atr([make_bar(10, 8, 9)], period=14)


def test_rsi_monotonic_increase_is_100() -> None:
    bars = [make_bar(i + 1, i, i, i) for i in range(1, 8)]
    assert rsi(bars, period=5) == pytest.approx(100.0)


def test_rsi_monotonic_decrease_is_0() -> None:
    bars = [make_bar(8 - i + 1, 8 - i, 8 - i, i) for i in range(1, 8)]
    assert rsi(bars, period=5) == pytest.approx(0.0)


def test_detect_structure_ascending_highs_and_lows_is_alcista() -> None:
    bars = [
        make_bar(high=10, low=8, close=9),
        make_bar(high=15, low=12, close=13),  # swing high (15)
        make_bar(high=11, low=7, close=9),  # swing low (7)
        make_bar(high=16, low=13, close=14),  # swing high (16) > 15
        make_bar(high=12, low=9, close=10),  # swing low (9) > 7
        make_bar(high=13, low=10, close=11),
    ]
    assert detect_structure(bars, fractal_window=1) == TrendDirection.ALCISTA


def test_detect_structure_descending_highs_and_lows_is_bajista() -> None:
    bars = [
        make_bar(high=16, low=13, close=14),
        make_bar(high=11, low=7, close=9),  # swing low (7)
        make_bar(high=15, low=12, close=13),  # swing high (15)
        make_bar(high=10, low=6, close=8),  # swing low (6) < 7
        make_bar(high=14, low=11, close=12),  # swing high (14) < 15
        make_bar(high=9, low=5, close=6),
    ]
    assert detect_structure(bars, fractal_window=1) == TrendDirection.BAJISTA


def test_detect_structure_insufficient_swings_is_neutral() -> None:
    bars = [make_bar(high=10 + i, low=8 + i, close=9 + i) for i in range(4)]
    assert detect_structure(bars, fractal_window=2) == TrendDirection.NEUTRAL
