from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..models import PriceBar
from .indicators import TrendDirection, atr, detect_structure, ema, rsi


@dataclass(frozen=True)
class TechnicalConfig:
    ema_rapida: int = 50
    ema_lenta: int = 200
    rsi_period: int = 14
    atr_period: int = 14
    rsi_sobrecompra: float = 70.0
    rsi_sobreventa: float = 30.0
    peso_confirmacion_estructura: float = 0.2
    factor_amortiguacion_rsi: float = 0.5


@dataclass(frozen=True)
class TechnicalScoreResult:
    score: float
    tendencia_h4: TrendDirection
    tendencia_h1: TrendDirection
    estructura_h1: TrendDirection
    rsi_h1: float | None
    atr_h1: float | None
    razones: list[str]


def _ema_trend(closes: Sequence[float], config: TechnicalConfig) -> TrendDirection:
    rapida = ema(closes, config.ema_rapida)
    lenta = ema(closes, config.ema_lenta)
    if rapida > lenta:
        return TrendDirection.ALCISTA
    if rapida < lenta:
        return TrendDirection.BAJISTA
    return TrendDirection.NEUTRAL


class TechnicalTrendEngine:
    """
    Motor de tendencia técnica (spec_bot_xauusd.md, 4.3), con el filtro de
    contexto multi-temporalidad de la sección 4.8: H4 (EMA50/200) fija la
    dirección permitida y H1 debe confirmarla o no hay señal técnica. Si H4 es
    neutral o H1 no coincide con H4, el score es 0 — "nunca opera contra este
    filtro" es una regla del bot completo, no solo de este motor (ver
    DecisionEngine para el resto de la garantía).
    """

    def __init__(self, config: TechnicalConfig | None = None) -> None:
        self._config = config or TechnicalConfig()

    def evaluate(self, h4_bars: Sequence[PriceBar], h1_bars: Sequence[PriceBar]) -> TechnicalScoreResult:
        config = self._config
        razones: list[str] = []

        closes_h4 = [b.close for b in h4_bars]
        closes_h1 = [b.close for b in h1_bars]

        tendencia_h4 = _ema_trend(closes_h4, config)
        tendencia_h1 = _ema_trend(closes_h1, config)
        razones.append(f"Tendencia H4 (EMA{config.ema_rapida}/EMA{config.ema_lenta}): {tendencia_h4.value}")
        razones.append(f"Tendencia H1 (EMA{config.ema_rapida}/EMA{config.ema_lenta}): {tendencia_h1.value}")

        atr_h1 = atr(h1_bars, config.atr_period) if len(h1_bars) >= config.atr_period + 1 else None
        rsi_h1 = rsi(h1_bars, config.rsi_period) if len(h1_bars) >= config.rsi_period + 1 else None

        if tendencia_h4 == TrendDirection.NEUTRAL or tendencia_h1 != tendencia_h4:
            razones.append("H1 no confirma la dirección de H4 (o H4 es neutral) — filtro bloquea la entrada.")
            return TechnicalScoreResult(
                score=0.0,
                tendencia_h4=tendencia_h4,
                tendencia_h1=tendencia_h1,
                estructura_h1=TrendDirection.NEUTRAL,
                rsi_h1=rsi_h1,
                atr_h1=atr_h1,
                razones=razones,
            )

        score = 1.0 if tendencia_h4 == TrendDirection.ALCISTA else -1.0

        estructura_h1 = detect_structure(h1_bars)
        razones.append(f"Estructura de precio H1: {estructura_h1.value}")
        if estructura_h1 == tendencia_h1:
            score += config.peso_confirmacion_estructura if score > 0 else -config.peso_confirmacion_estructura
            razones.append("La estructura de precio confirma la tendencia — se refuerza el score.")

        if rsi_h1 is not None:
            razones.append(f"RSI H1 ({config.rsi_period}): {rsi_h1:.1f}")
            if tendencia_h1 == TrendDirection.ALCISTA and rsi_h1 >= config.rsi_sobrecompra:
                score *= config.factor_amortiguacion_rsi
                razones.append(
                    f"RSI en sobrecompra (>= {config.rsi_sobrecompra}) — se amortigua el score (filtro, no señal)."
                )
            elif tendencia_h1 == TrendDirection.BAJISTA and rsi_h1 <= config.rsi_sobreventa:
                score *= config.factor_amortiguacion_rsi
                razones.append(
                    f"RSI en sobreventa (<= {config.rsi_sobreventa}) — se amortigua el score (filtro, no señal)."
                )

        return TechnicalScoreResult(
            score=max(-1.0, min(1.0, score)),
            tendencia_h4=tendencia_h4,
            tendencia_h1=tendencia_h1,
            estructura_h1=estructura_h1,
            rsi_h1=rsi_h1,
            atr_h1=atr_h1,
            razones=razones,
        )
