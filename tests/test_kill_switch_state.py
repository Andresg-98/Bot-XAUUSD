from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bot_xauusd.backtest.risk import RiskConfig
from bot_xauusd.live.state import KillSwitchStateStore, evaluar_kill_switches, reactivar_pausa_semanal

RISK = RiskConfig(drawdown_maximo_total=0.08, perdida_maxima_semanal=0.03)


def test_first_call_creates_state_with_current_equity(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    momento = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)

    estado, motivo_perm, motivo_sem = evaluar_kill_switches(store, equity=10_000.0, momento=momento, risk=RISK)

    assert estado.equity_inicio_dia == 10_000.0
    assert estado.equity_inicio_semana == 10_000.0
    assert estado.pico_equity == 10_000.0
    assert estado.halted_permanently is False
    assert estado.halted_semanalmente is False
    assert motivo_perm is None
    assert motivo_sem is None


def test_state_persists_across_reloads(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    momento = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    evaluar_kill_switches(store, equity=10_000.0, momento=momento, risk=RISK)

    # "reinicio" del script: nueva instancia del store, mismo archivo
    store2 = KillSwitchStateStore(tmp_path / "estado.json")
    estado, _, _ = evaluar_kill_switches(store2, equity=9_800.0, momento=momento + timedelta(hours=1), risk=RISK)

    assert estado.equity_inicio_dia == 10_000.0  # se mantiene, no se resetea en la misma fecha


def test_day_rollover_resets_daily_but_keeps_weekly(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    lunes = datetime(2026, 1, 5, 10, tzinfo=timezone.utc)  # lunes
    evaluar_kill_switches(store, equity=10_000.0, momento=lunes, risk=RISK)

    martes = lunes + timedelta(days=1)
    estado, _, _ = evaluar_kill_switches(store, equity=9_900.0, momento=martes, risk=RISK)

    assert estado.equity_inicio_dia == 9_900.0  # nuevo día -> nueva base diaria
    assert estado.equity_inicio_semana == 10_000.0  # misma semana -> base semanal intacta


def test_week_rollover_resets_weekly_baseline_when_not_halted(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    lunes = datetime(2026, 1, 5, 10, tzinfo=timezone.utc)
    evaluar_kill_switches(store, equity=10_000.0, momento=lunes, risk=RISK)

    siguiente_lunes = lunes + timedelta(days=8)
    estado, _, _ = evaluar_kill_switches(store, equity=9_900.0, momento=siguiente_lunes, risk=RISK)

    assert estado.equity_inicio_semana == 9_900.0


def test_drawdown_triggers_permanent_halt_once(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    momento = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    evaluar_kill_switches(store, equity=10_000.0, momento=momento, risk=RISK)

    estado, motivo_perm, _ = evaluar_kill_switches(store, equity=9_150.0, momento=momento, risk=RISK)  # -8.5%
    assert estado.halted_permanently is True
    assert motivo_perm is not None

    # ciclos posteriores: sigue halted, pero no se repite el mensaje de "recién activado"
    estado2, motivo_perm2, _ = evaluar_kill_switches(store, equity=9_200.0, momento=momento + timedelta(hours=1), risk=RISK)
    assert estado2.halted_permanently is True
    assert motivo_perm2 is None


def test_halt_persists_across_reload_even_if_equity_recovers(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    momento = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    evaluar_kill_switches(store, equity=10_000.0, momento=momento, risk=RISK)
    evaluar_kill_switches(store, equity=9_100.0, momento=momento, risk=RISK)  # dispara el halt

    store2 = KillSwitchStateStore(tmp_path / "estado.json")
    estado, _, _ = evaluar_kill_switches(store2, equity=9_900.0, momento=momento + timedelta(days=1), risk=RISK)

    assert estado.halted_permanently is True  # el halt es permanente, no se revierte al recuperar equity


# --- pausa semanal: requiere aprobación manual, no se reinicia sola (spec 4.5) -----------


def test_weekly_loss_triggers_halt_once(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    momento = datetime(2026, 1, 5, 10, tzinfo=timezone.utc)  # lunes
    evaluar_kill_switches(store, equity=10_000.0, momento=momento, risk=RISK)

    estado, _, motivo_sem = evaluar_kill_switches(store, equity=9_650.0, momento=momento, risk=RISK)  # -3.5%
    assert estado.halted_semanalmente is True
    assert motivo_sem is not None

    estado2, _, motivo_sem2 = evaluar_kill_switches(store, equity=9_700.0, momento=momento + timedelta(hours=1), risk=RISK)
    assert estado2.halted_semanalmente is True
    assert motivo_sem2 is None  # no se repite el mensaje


def test_weekly_halt_does_not_reset_on_week_rollover(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    lunes = datetime(2026, 1, 5, 10, tzinfo=timezone.utc)
    evaluar_kill_switches(store, equity=10_000.0, momento=lunes, risk=RISK)
    evaluar_kill_switches(store, equity=9_650.0, momento=lunes, risk=RISK)  # dispara la pausa

    siguiente_lunes = lunes + timedelta(days=8)
    estado, _, motivo_sem = evaluar_kill_switches(store, equity=9_700.0, momento=siguiente_lunes, risk=RISK)

    # a diferencia del drawdown/día, la pausa semanal NO se reinicia sola con la nueva semana
    assert estado.halted_semanalmente is True
    assert motivo_sem is None  # no se re-dispara el mensaje, simplemente sigue activa
    assert estado.equity_inicio_semana == 10_000.0  # la base NO se movió, sigue "congelada"


def test_weekly_halt_blocks_even_if_equity_recovers(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    momento = datetime(2026, 1, 5, 10, tzinfo=timezone.utc)
    evaluar_kill_switches(store, equity=10_000.0, momento=momento, risk=RISK)
    evaluar_kill_switches(store, equity=9_650.0, momento=momento, risk=RISK)  # dispara

    estado, _, _ = evaluar_kill_switches(store, equity=10_500.0, momento=momento + timedelta(hours=1), risk=RISK)

    assert estado.halted_semanalmente is True  # sigue pausado aunque el equity ya se recuperó


def test_reactivar_pausa_semanal_clears_the_flag_and_resets_baseline(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    momento = datetime(2026, 1, 5, 10, tzinfo=timezone.utc)
    evaluar_kill_switches(store, equity=10_000.0, momento=momento, risk=RISK)
    evaluar_kill_switches(store, equity=9_650.0, momento=momento, risk=RISK)  # dispara

    nuevo_estado = reactivar_pausa_semanal(store, momento + timedelta(hours=1), equity_actual=9_700.0)

    assert nuevo_estado.halted_semanalmente is False
    assert nuevo_estado.halted_semanalmente_en is None
    assert nuevo_estado.equity_inicio_semana == 9_700.0  # nueva base, no la vieja de 10_000

    # y el bot ya no queda bloqueado en el siguiente ciclo
    store2 = KillSwitchStateStore(tmp_path / "estado.json")
    estado, _, motivo_sem = evaluar_kill_switches(store2, equity=9_700.0, momento=momento + timedelta(hours=2), risk=RISK)
    assert estado.halted_semanalmente is False
    assert motivo_sem is None


def test_reactivar_pausa_semanal_raises_without_saved_state(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    with pytest.raises(ValueError):
        reactivar_pausa_semanal(store, datetime(2026, 1, 1, tzinfo=timezone.utc), equity_actual=10_000.0)


def test_reactivar_pausa_semanal_is_a_noop_risk_when_not_halted(tmp_path: Path) -> None:
    store = KillSwitchStateStore(tmp_path / "estado.json")
    momento = datetime(2026, 1, 5, 10, tzinfo=timezone.utc)
    evaluar_kill_switches(store, equity=10_000.0, momento=momento, risk=RISK)  # nunca se dispara

    nuevo_estado = reactivar_pausa_semanal(store, momento, equity_actual=10_000.0)

    assert nuevo_estado.halted_semanalmente is False
