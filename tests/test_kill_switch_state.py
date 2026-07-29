from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot_xauusd.backtest.risk import RiskConfig
from bot_xauusd.live.state import KillSwitchStateStore, evaluar_kill_switches

RISK = RiskConfig(drawdown_maximo_total=0.08)


def test_first_call_creates_state_with_current_equity(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    momento = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)

    estado, motivo = evaluar_kill_switches(store, equity=10_000.0, momento=momento, risk=RISK)

    assert estado.equity_inicio_dia == 10_000.0
    assert estado.equity_inicio_semana == 10_000.0
    assert estado.pico_equity == 10_000.0
    assert estado.halted_permanently is False
    assert motivo is None


def test_state_persists_across_reloads(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    momento = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    evaluar_kill_switches(store, equity=10_000.0, momento=momento, risk=RISK)

    # "reinicio" del script: nueva instancia del store, mismo archivo
    store2 = KillSwitchStateStore(tmp_path / "estado.json")
    estado, _ = evaluar_kill_switches(store2, equity=9_800.0, momento=momento + timedelta(hours=1), risk=RISK)

    assert estado.equity_inicio_dia == 10_000.0  # se mantiene, no se resetea en la misma fecha


def test_day_rollover_resets_daily_but_keeps_weekly(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    lunes = datetime(2026, 1, 5, 10, tzinfo=timezone.utc)  # lunes
    evaluar_kill_switches(store, equity=10_000.0, momento=lunes, risk=RISK)

    martes = lunes + timedelta(days=1)
    estado, _ = evaluar_kill_switches(store, equity=9_900.0, momento=martes, risk=RISK)

    assert estado.equity_inicio_dia == 9_900.0  # nuevo día -> nueva base diaria
    assert estado.equity_inicio_semana == 10_000.0  # misma semana -> base semanal intacta


def test_week_rollover_resets_weekly_baseline(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    lunes = datetime(2026, 1, 5, 10, tzinfo=timezone.utc)
    evaluar_kill_switches(store, equity=10_000.0, momento=lunes, risk=RISK)

    siguiente_lunes = lunes + timedelta(days=8)
    estado, _ = evaluar_kill_switches(store, equity=9_900.0, momento=siguiente_lunes, risk=RISK)

    assert estado.equity_inicio_semana == 9_900.0


def test_drawdown_triggers_permanent_halt_once(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    momento = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    evaluar_kill_switches(store, equity=10_000.0, momento=momento, risk=RISK)

    estado, motivo = evaluar_kill_switches(store, equity=9_150.0, momento=momento, risk=RISK)  # -8.5%
    assert estado.halted_permanently is True
    assert motivo is not None

    # ciclos posteriores: sigue halted, pero no se repite el mensaje de "recién activado"
    estado2, motivo2 = evaluar_kill_switches(store, equity=9_200.0, momento=momento + timedelta(hours=1), risk=RISK)
    assert estado2.halted_permanently is True
    assert motivo2 is None


def test_halt_persists_across_reload_even_if_equity_recovers(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    momento = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    evaluar_kill_switches(store, equity=10_000.0, momento=momento, risk=RISK)
    evaluar_kill_switches(store, equity=9_100.0, momento=momento, risk=RISK)  # dispara el halt

    store2 = KillSwitchStateStore(tmp_path / "estado.json")
    estado, _ = evaluar_kill_switches(store2, equity=9_900.0, momento=momento + timedelta(days=1), risk=RISK)

    assert estado.halted_permanently is True  # el halt es permanente, no se revierte al recuperar equity
