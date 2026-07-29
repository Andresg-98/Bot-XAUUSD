from __future__ import annotations

from enum import Enum
from typing import Sequence

from ..models import PriceBar


class TrendDirection(str, Enum):
    ALCISTA = "alcista"
    BAJISTA = "bajista"
    NEUTRAL = "neutral"


def ema(values: Sequence[float], period: int) -> float:
    """Media móvil exponencial (solo el último valor). Requiere len(values) >= period."""
    if len(values) < period:
        raise ValueError(f"Se requieren al menos {period} valores, se recibieron {len(values)}.")
    multiplicador = 2 / (period + 1)
    ema_actual = sum(values[:period]) / period
    for valor in values[period:]:
        ema_actual = (valor - ema_actual) * multiplicador + ema_actual
    return ema_actual


def atr(bars: Sequence[PriceBar], period: int = 14) -> float:
    """Average True Range con suavizado de Wilder. Requiere len(bars) >= period + 1."""
    if len(bars) < period + 1:
        raise ValueError(f"Se requieren al menos {period + 1} velas, se recibieron {len(bars)}.")
    true_ranges: list[float] = []
    for previa, actual in zip(bars, bars[1:]):
        true_ranges.append(
            max(
                actual.high - actual.low,
                abs(actual.high - previa.close),
                abs(actual.low - previa.close),
            )
        )
    atr_actual = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr_actual = (atr_actual * (period - 1) + tr) / period
    return atr_actual


def rsi(bars: Sequence[PriceBar], period: int = 14) -> float:
    """RSI de Wilder (0-100) sobre los cierres. Requiere len(bars) >= period + 1."""
    if len(bars) < period + 1:
        raise ValueError(f"Se requieren al menos {period + 1} velas, se recibieron {len(bars)}.")
    ganancias: list[float] = []
    perdidas: list[float] = []
    for previa, actual in zip(bars, bars[1:]):
        cambio = actual.close - previa.close
        ganancias.append(max(cambio, 0.0))
        perdidas.append(max(-cambio, 0.0))

    avg_ganancia = sum(ganancias[:period]) / period
    avg_perdida = sum(perdidas[:period]) / period
    for ganancia, perdida in zip(ganancias[period:], perdidas[period:]):
        avg_ganancia = (avg_ganancia * (period - 1) + ganancia) / period
        avg_perdida = (avg_perdida * (period - 1) + perdida) / period

    if avg_perdida == 0:
        return 100.0
    rs = avg_ganancia / avg_perdida
    return 100 - (100 / (1 + rs))


def detect_structure(bars: Sequence[PriceBar], fractal_window: int = 2) -> TrendDirection:
    """
    Detecta estructura de precio (spec_bot_xauusd.md, 4.3) usando fractales simples:
    una vela es swing high si su high es el máximo estricto entre `fractal_window`
    velas antes y después (swing low, análogo con mínimos). ALCISTA si los últimos
    dos swing highs y swing lows son ambos crecientes; BAJISTA si ambos decrecientes;
    NEUTRAL en cualquier otro caso (incluyendo datos insuficientes).
    """
    n = len(bars)
    swing_highs: list[float] = []
    swing_lows: list[float] = []
    for i in range(fractal_window, n - fractal_window):
        ventana_highs = [bars[j].high for j in range(i - fractal_window, i + fractal_window + 1)]
        ventana_lows = [bars[j].low for j in range(i - fractal_window, i + fractal_window + 1)]
        if bars[i].high == max(ventana_highs) and ventana_highs.count(bars[i].high) == 1:
            swing_highs.append(bars[i].high)
        if bars[i].low == min(ventana_lows) and ventana_lows.count(bars[i].low) == 1:
            swing_lows.append(bars[i].low)

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return TrendDirection.NEUTRAL

    if swing_highs[-1] > swing_highs[-2] and swing_lows[-1] > swing_lows[-2]:
        return TrendDirection.ALCISTA
    if swing_highs[-1] < swing_highs[-2] and swing_lows[-1] < swing_lows[-2]:
        return TrendDirection.BAJISTA
    return TrendDirection.NEUTRAL
