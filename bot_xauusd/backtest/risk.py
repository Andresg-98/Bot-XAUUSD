from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskConfig:
    """
    Perfil de riesgo conservador por defecto (spec_bot_xauusd.md, 4.5 y 7).
    `capital_inicial` es un número redondo para el backtest — el backtest
    trabaja en porcentajes y no simula la restricción de tamaño de lote mínimo
    del broker de la sección 4.5 (eso es una validación de la capa de
    ejecución, Fase 5, no de este motor puramente numérico).

    `relacion_riesgo_beneficio` y `atr_multiplo_sl` fueron actualizados tras el
    grid search de la Fase 3 (scripts/optimize_backtest.py) sobre 3 años reales
    de XAU/USD — la mejor combinación entre 36 probadas, comparada con los
    valores mínimos iniciales de la spec. **Advertencia:** ese grid search fue
    SOLO TÉCNICO (score macro=0, ver README) y no alcanzó el profit factor
    objetivo de 1.4 (llegó a 1.31) — siguen siendo un punto de partida, no
    valores calibrados definitivos, y deben re-validarse cuando haya datos
    macro históricos disponibles.
    """

    capital_inicial: float = 10_000.0
    riesgo_por_operacion: float = 0.005  # 0.5% del capital
    perdida_maxima_diaria: float = 0.015  # 1.5% -> pausa el resto del día
    perdida_maxima_semanal: float = 0.03  # 3% -> pausa el resto de la semana
    drawdown_maximo_total: float = 0.08  # 8% -> kill switch permanente
    relacion_riesgo_beneficio: float = 3.0  # TP = distancia_sl * este factor (mínimo 1:2 por spec; grid search: 3.0)
    atr_multiplo_sl: float = 2.5  # distancia del stop-loss = ATR(H1) * este múltiplo (grid search: 2.5)

    # Trailing stop (breakeven + ATR). Solo lo aplica el loop en vivo
    # (bot_xauusd/live/loop.py) — el BacktestEngine todavía NO lo simula, así
    # que los resultados de la Fase 3 no reflejan este comportamiento. Off por
    # defecto para no cambiar nada existente; run_paper_trading.py lo activa.
    trailing_habilitado: bool = False
    trailing_activar_en_r: float = 1.0  # mueve el SL a breakeven al alcanzar esta ganancia (en múltiplos del riesgo inicial)
    trailing_atr_multiplo: float = 1.5  # más allá del breakeven, distancia del SL = ATR(H1) * este múltiplo
