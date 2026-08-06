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
    halted_semanalmente: bool = False
    halted_semanalmente_en: str | None = None

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
            halted_semanalmente=False,
            halted_semanalmente_en=None,
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
) -> tuple[KillSwitchState, str | None, str | None]:
    """
    Carga el estado (o lo crea), hace roll-over del día siempre (spec 4.5: la
    pausa diaria SÍ se reinicia sola "por el resto del día"), actualiza el
    pico de equity, y evalúa los kill switches de drawdown total y pérdida
    semanal. Devuelve (estado, motivo_permanente, motivo_semanal) — cada
    motivo es no-None SOLO en el ciclo en que ese kill switch se activa por
    primera vez (para loggear el evento una única vez).

    La pausa semanal, a diferencia de la diaria, NO se reinicia sola al
    empezar la siguiente semana calendario (spec 4.5: "requiere revisar la
    lógica antes de reactivar, no reinicio automático") — se queda activa
    hasta una reactivación manual explícita (`reactivar_pausa_semanal`).
    """
    state = store.load() or KillSwitchState.nuevo(equity, momento)

    fecha_actual = momento.date().isoformat()
    if state.fecha != fecha_actual:
        state.fecha = fecha_actual
        state.equity_inicio_dia = equity

    semana_actual = _clave_semana(momento)
    if state.semana != semana_actual and not state.halted_semanalmente:
        state.semana = semana_actual
        state.equity_inicio_semana = equity

    state.pico_equity = max(state.pico_equity, equity)

    motivo_permanente: str | None = None
    if not state.halted_permanently and state.drawdown_total(equity) >= risk.drawdown_maximo_total:
        state.halted_permanently = True
        state.halted_en = momento.isoformat()
        motivo_permanente = (
            f"Drawdown total ({state.drawdown_total(equity):.1%}) alcanzó el límite "
            f"({risk.drawdown_maximo_total:.0%}) — kill switch permanente activado (spec 4.5)."
        )

    motivo_semanal: str | None = None
    if not state.halted_semanalmente and state.perdida_semanal(equity) >= risk.perdida_maxima_semanal:
        state.halted_semanalmente = True
        state.halted_semanalmente_en = momento.isoformat()
        motivo_semanal = (
            f"Pérdida semanal ({state.perdida_semanal(equity):.1%}) alcanzó el límite "
            f"({risk.perdida_maxima_semanal:.0%}) — pausa activada, requiere aprobación manual para "
            "reactivar (spec 4.5), no se reinicia sola la próxima semana."
        )

    store.save(state)
    return state, motivo_permanente, motivo_semanal


def reactivar_pausa_semanal(store: KillSwitchStateStore, momento: datetime, equity_actual: float) -> KillSwitchState:
    """
    Reactivación manual explícita de la pausa semanal (spec 4.5). Resetea
    también la base de equity semanal al equity actual, para que el bot no
    vuelva a dispararse de inmediato contra la base de la semana en que se
    activó — es una decisión deliberada del humano que revisa, no un reinicio
    automático.
    """
    state = store.load()
    if state is None:
        raise ValueError("No hay estado de kill switch guardado — nada que reactivar.")
    state.halted_semanalmente = False
    state.halted_semanalmente_en = None
    state.equity_inicio_semana = equity_actual
    state.semana = _clave_semana(momento)
    store.save(state)
    return state
