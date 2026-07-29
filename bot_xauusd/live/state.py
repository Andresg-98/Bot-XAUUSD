from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from ..backtest.risk import RiskConfig


def _clave_semana(momento: datetime) -> str:
    año, semana, _ = momento.isocalendar()
    return f"{año}-W{semana:02d}"


@dataclass
class KillSwitchState:
    """Estado persistido en disco para que los kill switches de la spec (4.5)
    sobrevivan a un reinicio del script durante las 4-8 semanas de paper trading."""

    fecha: str
    equity_inicio_dia: float
    semana: str
    equity_inicio_semana: float
    pico_equity: float
    halted_permanently: bool
    halted_en: str | None

    @classmethod
    def nuevo(cls, equity: float, momento: datetime) -> "KillSwitchState":
        return cls(
            fecha=momento.date().isoformat(),
            equity_inicio_dia=equity,
            semana=_clave_semana(momento),
            equity_inicio_semana=equity,
            pico_equity=equity,
            halted_permanently=False,
            halted_en=None,
        )

    def perdida_diaria(self, equity: float) -> float:
        return (self.equity_inicio_dia - equity) / self.equity_inicio_dia if self.equity_inicio_dia > 0 else 0.0

    def perdida_semanal(self, equity: float) -> float:
        return (
            (self.equity_inicio_semana - equity) / self.equity_inicio_semana if self.equity_inicio_semana > 0 else 0.0
        )

    def drawdown_total(self, equity: float) -> float:
        return (self.pico_equity - equity) / self.pico_equity if self.pico_equity > 0 else 0.0


class KillSwitchStateStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> KillSwitchState | None:
        if not self._path.exists():
            return None
        datos = json.loads(self._path.read_text(encoding="utf-8"))
        return KillSwitchState(**datos)

    def save(self, state: KillSwitchState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(asdict(state), indent=2, ensure_ascii=False), encoding="utf-8")


def evaluar_kill_switches(
    store: KillSwitchStateStore, equity: float, momento: datetime, risk: RiskConfig
) -> tuple[KillSwitchState, str | None]:
    """
    Carga el estado (o lo crea), hace roll-over de día/semana si corresponde,
    actualiza el pico de equity y evalúa el kill switch de drawdown total.
    Devuelve el estado actualizado y, SOLO en el ciclo en que se activa por
    primera vez, un motivo no-None (para loggear el evento una única vez).
    """
    state = store.load() or KillSwitchState.nuevo(equity, momento)

    fecha_actual = momento.date().isoformat()
    if state.fecha != fecha_actual:
        state.fecha = fecha_actual
        state.equity_inicio_dia = equity

    semana_actual = _clave_semana(momento)
    if state.semana != semana_actual:
        state.semana = semana_actual
        state.equity_inicio_semana = equity

    state.pico_equity = max(state.pico_equity, equity)

    motivo: str | None = None
    if not state.halted_permanently and state.drawdown_total(equity) >= risk.drawdown_maximo_total:
        state.halted_permanently = True
        state.halted_en = momento.isoformat()
        motivo = (
            f"Drawdown total ({state.drawdown_total(equity):.1%}) alcanzó el límite "
            f"({risk.drawdown_maximo_total:.0%}) — kill switch permanente activado (spec 4.5)."
        )

    store.save(state)
    return state, motivo
