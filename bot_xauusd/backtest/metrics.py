from __future__ import annotations

import math
from dataclasses import dataclass

from .engine import BacktestResult


@dataclass(frozen=True)
class BacktestMetrics:
    numero_operaciones: int
    win_rate: float
    profit_factor: float | None  # None si no hubo pérdidas ni ganancias
    max_drawdown: float  # fracción del capital, ej. 0.12 = 12%
    sharpe_ratio: float | None  # None si hay menos de 2 operaciones o varianza 0


def _max_drawdown(equity_curve: list[tuple[object, float]]) -> float:
    if not equity_curve:
        return 0.0
    pico = equity_curve[0][1]
    peor = 0.0
    for _momento, equity in equity_curve:
        pico = max(pico, equity)
        if pico > 0:
            peor = max(peor, (pico - equity) / pico)
    return peor


def _sharpe_ratio(resultado: BacktestResult) -> float | None:
    """
    Sharpe simplificado por operación (no por retorno diario): usa el retorno
    porcentual de cada trade sobre el capital al momento de abrirlo y anualiza
    por la frecuencia real de operaciones observada. Razonable para una
    estrategia de baja frecuencia (spec 4.8: 0-3 operaciones/semana), pero no
    es el Sharpe clásico de series de retornos diarios.
    """
    trades = resultado.trades
    if len(trades) < 2:
        return None

    retornos = [t.pnl / t.capital_al_abrir for t in trades if t.capital_al_abrir > 0]
    if len(retornos) < 2:
        return None

    media = sum(retornos) / len(retornos)
    varianza = sum((r - media) ** 2 for r in retornos) / (len(retornos) - 1)
    desviacion = math.sqrt(varianza)
    if desviacion == 0:
        return None

    primero = resultado.equity_curve[0][0]
    ultimo = resultado.equity_curve[-1][0]
    dias = max((ultimo - primero).days, 1)
    años = dias / 365.25
    operaciones_por_año = len(trades) / años if años > 0 else 0.0

    return (media / desviacion) * math.sqrt(operaciones_por_año) if operaciones_por_año > 0 else None


def compute_metrics(resultado: BacktestResult) -> BacktestMetrics:
    trades = resultado.trades
    n = len(trades)
    ganadoras = [t for t in trades if t.pnl > 0]
    perdedoras = [t for t in trades if t.pnl <= 0]

    win_rate = len(ganadoras) / n if n else 0.0

    suma_ganancias = sum(t.pnl for t in ganadoras)
    suma_perdidas = abs(sum(t.pnl for t in perdedoras))
    if suma_perdidas > 0:
        profit_factor: float | None = suma_ganancias / suma_perdidas
    elif suma_ganancias > 0:
        profit_factor = math.inf
    else:
        profit_factor = None

    return BacktestMetrics(
        numero_operaciones=n,
        win_rate=win_rate,
        profit_factor=profit_factor,
        max_drawdown=_max_drawdown(resultado.equity_curve),
        sharpe_ratio=_sharpe_ratio(resultado),
    )
