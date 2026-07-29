from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Sequence

from ..models import MacroEvent, PriceBar, SignalDirection
from ..signals.decision_engine import DecisionEngine
from .risk import RiskConfig

MacroEventsProvider = Callable[[datetime], Sequence[MacroEvent]]


def _sin_eventos_macro(_momento: datetime) -> Sequence[MacroEvent]:
    """Proveedor por defecto: sin histórico de calendario macro disponible (ver README)."""
    return ()


@dataclass(frozen=True)
class Trade:
    direccion: SignalDirection
    apertura: datetime
    cierre: datetime
    entrada: float
    salida: float
    sl: float
    tp: float
    tamano: float
    pnl: float
    resultado: str  # "tp" | "sl"
    capital_al_abrir: float


@dataclass(frozen=True)
class BacktestResult:
    trades: list[Trade]
    equity_curve: list[tuple[datetime, float]]
    capital_inicial: float
    capital_final: float
    halted_permanently: bool
    halted_en: datetime | None


@dataclass
class _PosicionAbierta:
    direccion: SignalDirection
    entrada: float
    sl: float
    tp: float
    tamano: float
    apertura: datetime
    capital_al_abrir: float


@dataclass
class _EntradaPendiente:
    direccion: SignalDirection
    atr_h1: float


def _resolver_salida(pos: _PosicionAbierta, bar: PriceBar) -> tuple[float, str] | None:
    """Determina si esta vela toca el SL o el TP. Convención conservadora: si
    ambos caben en el rango de la vela, se asume que el SL se tocó primero."""
    if pos.direccion == SignalDirection.LONG:
        if bar.low <= pos.sl:
            return pos.sl, "sl"
        if bar.high >= pos.tp:
            return pos.tp, "tp"
    else:
        if bar.high >= pos.sl:
            return pos.sl, "sl"
        if bar.low <= pos.tp:
            return pos.tp, "tp"
    return None


class BacktestEngine:
    """
    Recorre velas H1 (con contexto H4) y simula las operaciones que el
    `DecisionEngine` habría tomado — mismo código que correría en vivo, para
    no divergir entre backtest y ejecución real (spec 6, Fase 3).

    Diseño anti-lookahead: una señal decidida al cierre de la vela H1 `i`
    (usando solo datos hasta e incluyendo esa vela) se ejecuta al *open* de la
    vela `i+1`, nunca al close de la misma vela que generó la señal.

    Respeta las reglas no negociables de la spec: máximo 1 operación
    simultánea (4.5), kill switches de pérdida diaria/semanal/drawdown total
    (4.5), y tamaño de posición calculado por riesgo % + distancia de SL (ATR).
    """

    def __init__(
        self,
        decision_engine: DecisionEngine | None = None,
        risk_config: RiskConfig | None = None,
        macro_events_provider: MacroEventsProvider = _sin_eventos_macro,
        min_historial: int = 250,
    ) -> None:
        self._decision_engine = decision_engine or DecisionEngine()
        self._risk = risk_config or RiskConfig()
        self._macro_events_provider = macro_events_provider
        self._min_historial = min_historial

    def run(self, h4_bars: Sequence[PriceBar], h1_bars: Sequence[PriceBar]) -> BacktestResult:
        risk = self._risk
        trades: list[Trade] = []
        equity = risk.capital_inicial
        peak_equity = equity
        equity_curve: list[tuple[datetime, float]] = []

        posicion: _PosicionAbierta | None = None
        pendiente: _EntradaPendiente | None = None
        halted_permanently = False
        halted_en: datetime | None = None

        dia_actual = None
        equity_inicio_dia = equity
        semana_actual = None
        equity_inicio_semana = equity

        for i, bar in enumerate(h1_bars):
            dia = bar.time.date()
            semana = bar.time.isocalendar()[:2]
            if dia != dia_actual:
                dia_actual = dia
                equity_inicio_dia = equity
            if semana != semana_actual:
                semana_actual = semana
                equity_inicio_semana = equity

            if pendiente is not None and posicion is None:
                distancia_sl = pendiente.atr_h1 * risk.atr_multiplo_sl
                riesgo_dinero = equity * risk.riesgo_por_operacion
                tamano = riesgo_dinero / distancia_sl if distancia_sl > 0 else 0.0
                entrada = bar.open
                if pendiente.direccion == SignalDirection.LONG:
                    sl = entrada - distancia_sl
                    tp = entrada + distancia_sl * risk.relacion_riesgo_beneficio
                else:
                    sl = entrada + distancia_sl
                    tp = entrada - distancia_sl * risk.relacion_riesgo_beneficio
                if tamano > 0:
                    posicion = _PosicionAbierta(
                        direccion=pendiente.direccion,
                        entrada=entrada,
                        sl=sl,
                        tp=tp,
                        tamano=tamano,
                        apertura=bar.time,
                        capital_al_abrir=equity,
                    )
                pendiente = None

            if posicion is not None:
                salida = _resolver_salida(posicion, bar)
                if salida is not None:
                    precio_salida, resultado = salida
                    if posicion.direccion == SignalDirection.LONG:
                        pnl = posicion.tamano * (precio_salida - posicion.entrada)
                    else:
                        pnl = posicion.tamano * (posicion.entrada - precio_salida)
                    equity += pnl
                    trades.append(
                        Trade(
                            direccion=posicion.direccion,
                            apertura=posicion.apertura,
                            cierre=bar.time,
                            entrada=posicion.entrada,
                            salida=precio_salida,
                            sl=posicion.sl,
                            tp=posicion.tp,
                            tamano=posicion.tamano,
                            pnl=pnl,
                            resultado=resultado,
                            capital_al_abrir=posicion.capital_al_abrir,
                        )
                    )
                    peak_equity = max(peak_equity, equity)
                    posicion = None

            equity_curve.append((bar.time, equity))

            if halted_permanently:
                continue

            drawdown_actual = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
            if drawdown_actual >= risk.drawdown_maximo_total:
                halted_permanently = True
                halted_en = bar.time
                continue

            perdida_diaria = (equity_inicio_dia - equity) / equity_inicio_dia if equity_inicio_dia > 0 else 0.0
            if perdida_diaria >= risk.perdida_maxima_diaria:
                continue

            perdida_semanal = (
                (equity_inicio_semana - equity) / equity_inicio_semana if equity_inicio_semana > 0 else 0.0
            )
            if perdida_semanal >= risk.perdida_maxima_semanal:
                continue

            if posicion is not None:
                continue

            h4_hasta_ahora = [b for b in h4_bars if b.time <= bar.time]
            h1_hasta_ahora = h1_bars[: i + 1]
            if len(h4_hasta_ahora) < self._min_historial or len(h1_hasta_ahora) < self._min_historial:
                continue

            eventos = self._macro_events_provider(bar.time)
            señal = self._decision_engine.evaluate(
                eventos, h4_hasta_ahora[-self._min_historial :], h1_hasta_ahora[-self._min_historial :]
            )
            if señal.direccion != SignalDirection.NONE and señal.tecnico.atr_h1:
                pendiente = _EntradaPendiente(direccion=señal.direccion, atr_h1=señal.tecnico.atr_h1)

        return BacktestResult(
            trades=trades,
            equity_curve=equity_curve,
            capital_inicial=risk.capital_inicial,
            capital_final=equity,
            halted_permanently=halted_permanently,
            halted_en=halted_en,
        )
