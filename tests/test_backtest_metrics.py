from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from bot_xauusd.backtest.engine import BacktestResult, Trade
from bot_xauusd.backtest.metrics import compute_metrics
from bot_xauusd.models import SignalDirection


def make_trade(pnl: float, capital_al_abrir: float = 10_000.0, dias_desde_inicio: int = 0) -> Trade:
    momento = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=dias_desde_inicio)
    return Trade(
        direccion=SignalDirection.LONG,
        apertura=momento,
        cierre=momento + timedelta(hours=1),
        entrada=2000.0,
        salida=2000.0 + pnl,
        sl=1990.0,
        tp=2020.0,
        tamano=1.0,
        pnl=pnl,
        resultado="tp" if pnl > 0 else "sl",
        capital_al_abrir=capital_al_abrir,
    )


def test_win_rate_and_profit_factor() -> None:
    trades = [make_trade(100.0), make_trade(100.0), make_trade(-50.0)]
    resultado = BacktestResult(
        trades=trades,
        equity_curve=[(t.apertura, 10_000.0) for t in trades],
        capital_inicial=10_000.0,
        capital_final=10_150.0,
        halted_permanently=False,
        halted_en=None,
    )

    metricas = compute_metrics(resultado)

    assert metricas.numero_operaciones == 3
    assert metricas.win_rate == pytest.approx(2 / 3)
    assert metricas.profit_factor == pytest.approx(200.0 / 50.0)


def test_profit_factor_is_infinite_with_no_losses() -> None:
    trades = [make_trade(100.0), make_trade(50.0)]
    resultado = BacktestResult(
        trades=trades,
        equity_curve=[(t.apertura, 10_000.0) for t in trades],
        capital_inicial=10_000.0,
        capital_final=10_150.0,
        halted_permanently=False,
        halted_en=None,
    )

    metricas = compute_metrics(resultado)

    assert metricas.profit_factor == math.inf


def test_no_trades_yields_zero_win_rate_and_no_profit_factor() -> None:
    resultado = BacktestResult(
        trades=[],
        equity_curve=[(datetime(2026, 1, 1, tzinfo=timezone.utc), 10_000.0)],
        capital_inicial=10_000.0,
        capital_final=10_000.0,
        halted_permanently=False,
        halted_en=None,
    )

    metricas = compute_metrics(resultado)

    assert metricas.numero_operaciones == 0
    assert metricas.win_rate == 0.0
    assert metricas.profit_factor is None
    assert metricas.sharpe_ratio is None


def test_max_drawdown_from_equity_curve() -> None:
    curva = [
        (datetime(2026, 1, 1, tzinfo=timezone.utc), 10_000.0),
        (datetime(2026, 1, 2, tzinfo=timezone.utc), 11_000.0),  # nuevo pico
        (datetime(2026, 1, 3, tzinfo=timezone.utc), 9_900.0),  # -10% desde el pico
        (datetime(2026, 1, 4, tzinfo=timezone.utc), 10_500.0),
    ]
    resultado = BacktestResult(
        trades=[],
        equity_curve=curva,
        capital_inicial=10_000.0,
        capital_final=10_500.0,
        halted_permanently=False,
        halted_en=None,
    )

    metricas = compute_metrics(resultado)

    assert metricas.max_drawdown == pytest.approx(0.1)


def test_sharpe_ratio_is_none_with_zero_variance() -> None:
    trades = [make_trade(100.0, dias_desde_inicio=0), make_trade(100.0, dias_desde_inicio=10)]
    resultado = BacktestResult(
        trades=trades,
        equity_curve=[(t.apertura, 10_000.0) for t in trades],
        capital_inicial=10_000.0,
        capital_final=10_200.0,
        halted_permanently=False,
        halted_en=None,
    )

    metricas = compute_metrics(resultado)

    assert metricas.sharpe_ratio is None  # retornos idénticos -> varianza 0


def test_sharpe_ratio_is_computed_with_varying_returns() -> None:
    trades = [
        make_trade(100.0, dias_desde_inicio=0),
        make_trade(-50.0, dias_desde_inicio=10),
        make_trade(80.0, dias_desde_inicio=20),
    ]
    resultado = BacktestResult(
        trades=trades,
        equity_curve=[(t.apertura, 10_000.0) for t in trades],
        capital_inicial=10_000.0,
        capital_final=10_130.0,
        halted_permanently=False,
        halted_en=None,
    )

    metricas = compute_metrics(resultado)

    assert metricas.sharpe_ratio is not None
    assert isinstance(metricas.sharpe_ratio, float)
