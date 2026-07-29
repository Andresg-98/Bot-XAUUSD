from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Sequence

from ..backtest.risk import RiskConfig
from ..execution.mt5_broker import Mt5Broker
from ..ingestion.macro_forexfactory import ForexFactoryApiError, ForexFactoryClient
from ..ingestion.price_mt5 import Mt5ConnectionError, Mt5PriceClient
from ..models import ImpactLevel, MacroEvent, SignalDirection
from ..signals.decision_engine import DecisionEngine
from ..signals.indicators import atr
from .decision_log import DecisionLogger
from .state import KillSwitchStateStore, evaluar_kill_switches

HISTORIAL_MINIMO = 250
INTERVALO_MACRO_SEGUNDOS_DEFECTO = 180


@dataclass
class LoopState:
    """Estado en memoria del ciclo (spec 4.8): cuándo fue la última evaluación
    por cierre de H1, qué eventos de alto impacto ya dispararon una
    re-evaluación (para no repetir la misma reacción varias veces), y el
    calendario macro cacheado para no re-consultar el feed en cada tick de
    monitoreo (ver `ejecutar_ciclo`: el feed de ForexFactory es un servicio
    gratuito no oficial que empieza a devolver 429 si se le pide demasiado
    seguido — se descubrió corriendo el bot en vivo)."""

    ultima_hora_evaluada: str | None = None
    eventos_alto_impacto_vistos: set[tuple[str, str, str]] = field(default_factory=set)
    eventos_cache: list[MacroEvent] = field(default_factory=list)
    ultima_consulta_macro: datetime | None = None
    posicion_abierta_anterior: bool = False
    trailing_distancia_riesgo: float | None = None


def _hora_bucket(momento: datetime) -> str:
    return momento.strftime("%Y-%m-%dT%H")


def _detectar_eventos_alto_impacto_nuevos(
    eventos: Sequence[MacroEvent], vistos: set[tuple[str, str, str]]
) -> list[MacroEvent]:
    nuevos = []
    for e in eventos:
        if e.impacto == ImpactLevel.ALTO and e.valor_real is not None:
            clave = (e.evento, e.pais, e.fecha.isoformat())
            if clave not in vistos:
                nuevos.append(e)
                vistos.add(clave)
    return nuevos


def _obtener_eventos_macro(
    momento: datetime,
    loop_state: LoopState,
    macro_client: ForexFactoryClient,
    logger: DecisionLogger,
    intervalo_macro_segundos: int,
) -> list[MacroEvent]:
    """Reutiliza el calendario cacheado salvo que ya haya pasado
    `intervalo_macro_segundos` desde el último INTENTO (haya tenido éxito o
    no). Marcar `ultima_consulta_macro` solo en el caso exitoso causaba una
    tormenta de reintentos: tras un 429, cada tick de 45s volvía a intentar
    de inmediato en vez de esperar la ventana completa — descubierto
    corriendo el bot en vivo."""
    vencido = (
        loop_state.ultima_consulta_macro is None
        or (momento - loop_state.ultima_consulta_macro).total_seconds() >= intervalo_macro_segundos
    )
    if not vencido:
        return loop_state.eventos_cache

    loop_state.ultima_consulta_macro = momento
    try:
        loop_state.eventos_cache = macro_client.get_calendar("this_week")
    except ForexFactoryApiError as exc:
        logger.log_evento("error_macro", str(exc))
    return loop_state.eventos_cache


def _revisar_cierre_de_posicion(
    momento: datetime, symbol: str, broker: Mt5Broker, logger: DecisionLogger, loop_state: LoopState
) -> None:
    """El SL/TP lo gestiona el broker directamente, no nuestro loop — así que
    detectamos el cierre comparando el conteo de posiciones abiertas entre
    ciclos, y consultamos el historial de MT5 para el resultado (spec 4.7:
    registrar cada evento, no solo las decisiones de entrada)."""
    hay_posicion_ahora = broker.get_open_positions_count(symbol) > 0
    if loop_state.posicion_abierta_anterior and not hay_posicion_ahora:
        cierre = broker.get_last_closed_trade(symbol, desde=momento - timedelta(days=7))
        if cierre is not None:
            logger.log_evento(
                "posicion_cerrada",
                f"ticket={cierre['ticket_posicion']} precio_cierre={cierre['precio_cierre']} "
                f"profit={cierre['profit']:+.2f} volumen={cierre['volumen']}",
            )
        else:
            logger.log_evento("posicion_cerrada", "la posición ya no está abierta, pero no se encontró el deal de cierre en el historial")
    if not hay_posicion_ahora:
        loop_state.trailing_distancia_riesgo = None
    loop_state.posicion_abierta_anterior = hay_posicion_ahora


def _actualizar_trailing_stop(
    symbol: str,
    broker: Mt5Broker,
    price_client: Mt5PriceClient,
    risk: RiskConfig,
    logger: DecisionLogger,
    loop_state: LoopState,
) -> None:
    """Breakeven + ATR trailing (a pedido explícito del usuario, no es parte
    de la spec original): una vez que la operación ganó `trailing_activar_en_r`
    veces su riesgo inicial, el SL se mueve a breakeven y luego sigue al
    precio a `trailing_atr_multiplo` × ATR(H1) de distancia. Nunca se mueve en
    contra (nunca aumenta el riesgo)."""
    if not risk.trailing_habilitado:
        return

    posicion = broker.get_open_position(symbol)
    if posicion is None:
        return

    if loop_state.trailing_distancia_riesgo is None:
        # Primera vez que vemos esta posición en este proceso (recién abierta,
        # o el bot se reinició con una ya abierta): usamos el SL actual como
        # referencia del riesgo inicial. Si el bot se reinició DESPUÉS de que
        # el trailing ya hubiera movido el SL, esta referencia queda más
        # conservadora que la original — no hay persistencia en disco para
        # esto (a diferencia de los kill switches), limitación aceptada.
        distancia = abs(posicion["entrada"] - posicion["sl"])
        if distancia <= 0:
            return
        loop_state.trailing_distancia_riesgo = distancia

    distancia_riesgo = loop_state.trailing_distancia_riesgo
    entrada = posicion["entrada"]
    sl_actual = posicion["sl"]
    precio_actual = posicion["precio_actual"]

    try:
        h1_bars = price_client.get_bars(symbol, "H1", count=15)
    except Mt5ConnectionError:
        return
    if len(h1_bars) < 15:
        return
    atr_h1 = atr(h1_bars, period=14)

    if posicion["direccion"] == SignalDirection.LONG:
        ganancia = precio_actual - entrada
        if ganancia < distancia_riesgo * risk.trailing_activar_en_r:
            return
        nuevo_sl = max(sl_actual, entrada, precio_actual - atr_h1 * risk.trailing_atr_multiplo)
        mejora = nuevo_sl > sl_actual
    else:
        ganancia = entrada - precio_actual
        if ganancia < distancia_riesgo * risk.trailing_activar_en_r:
            return
        nuevo_sl = min(sl_actual, entrada, precio_actual + atr_h1 * risk.trailing_atr_multiplo)
        mejora = nuevo_sl < sl_actual

    if mejora and abs(nuevo_sl - sl_actual) > 1e-6:
        exito = broker.update_stop_loss(symbol, posicion["ticket"], nuevo_sl, posicion["tp"])
        if exito:
            logger.log_evento(
                "trailing_stop", f"ticket={posicion['ticket']} SL {sl_actual:.2f} -> {nuevo_sl:.2f}"
            )
        else:
            logger.log_evento("trailing_stop_fallo", f"ticket={posicion['ticket']} no se pudo mover el SL a {nuevo_sl:.2f}")


def debe_evaluar(momento: datetime, loop_state: LoopState, eventos: Sequence[MacroEvent]) -> tuple[bool, str]:
    """
    Dos disparadores posibles de evaluación (spec 4.8), independientes del
    monitoreo continuo: cierre de vela H1, o publicación de un evento macro de
    alto impacto. Nunca se evalúa en cada tick — eso es exactamente lo que
    prohíbe la spec ("sobre-operación y ruido").
    """
    nuevos = _detectar_eventos_alto_impacto_nuevos(eventos, loop_state.eventos_alto_impacto_vistos)
    if nuevos:
        return True, f"evento macro de alto impacto publicado: {', '.join(e.evento for e in nuevos)}"

    hora_actual = _hora_bucket(momento)
    if loop_state.ultima_hora_evaluada != hora_actual:
        return True, "cierre de vela H1"

    return False, ""


def ejecutar_ciclo(
    *,
    momento: datetime,
    symbol: str,
    broker: Mt5Broker,
    macro_client: ForexFactoryClient,
    price_client: Mt5PriceClient,
    decision_engine: DecisionEngine,
    risk: RiskConfig,
    logger: DecisionLogger,
    state_store: KillSwitchStateStore,
    loop_state: LoopState,
    intervalo_macro_segundos: int = INTERVALO_MACRO_SEGUNDOS_DEFECTO,
    lote_fijo: float | None = None,
) -> None:
    """Un ciclo de monitoreo (spec 4.8): siempre revisa kill switches; solo
    evalúa una nueva señal si corresponde (ver `debe_evaluar`).

    `lote_fijo`: si se define, reemplaza el sizing automático por riesgo % +
    ATR (spec 4.5) con este lote fijo para todas las entradas. El SL sigue
    calculándose por ATR, así que el riesgo en $ deja de ser constante entre
    operaciones — es una decisión explícita del usuario (MT5_LOTE_FIJO)."""
    equity = broker.get_account_equity()
    estado, motivo_halt = evaluar_kill_switches(state_store, equity, momento, risk)
    if motivo_halt:
        logger.log_evento("kill_switch_permanente", motivo_halt)

    _revisar_cierre_de_posicion(momento, symbol, broker, logger, loop_state)
    _actualizar_trailing_stop(symbol, broker, price_client, risk, logger, loop_state)

    eventos = _obtener_eventos_macro(momento, loop_state, macro_client, logger, intervalo_macro_segundos)

    evaluar, motivo = debe_evaluar(momento, loop_state, eventos)
    if not evaluar:
        return
    loop_state.ultima_hora_evaluada = _hora_bucket(momento)

    if estado.halted_permanently:
        logger.log_evento("evaluacion_omitida", "kill switch de drawdown total permanente ya activo")
        return
    if estado.perdida_diaria(equity) >= risk.perdida_maxima_diaria:
        logger.log_evento("evaluacion_omitida", "pausa por pérdida diaria máxima (spec 4.5)")
        return
    if estado.perdida_semanal(equity) >= risk.perdida_maxima_semanal:
        logger.log_evento("evaluacion_omitida", "pausa por pérdida semanal máxima (spec 4.5)")
        return
    if broker.get_open_positions_count(symbol) > 0:
        logger.log_evento("evaluacion_omitida", "ya hay una posición abierta (máximo 1, spec 4.5)")
        return

    try:
        h4_bars = price_client.get_bars(symbol, "H4", count=HISTORIAL_MINIMO)
        h1_bars = price_client.get_bars(symbol, "H1", count=HISTORIAL_MINIMO)
    except Mt5ConnectionError as exc:
        logger.log_evento("error_precio", str(exc))
        return

    if len(h4_bars) < HISTORIAL_MINIMO or len(h1_bars) < HISTORIAL_MINIMO:
        logger.log_evento("evaluacion_omitida", "historial insuficiente para EMA200")
        return

    señal = decision_engine.evaluate(eventos, h4_bars, h1_bars)

    if señal.direccion == SignalDirection.NONE:
        logger.log_evaluacion(señal, ejecutada=False, detalle=motivo)
        return

    atr_h1 = señal.tecnico.atr_h1
    if not atr_h1:
        logger.log_evaluacion(señal, ejecutada=False, detalle="sin ATR disponible")
        return

    distancia_sl = atr_h1 * risk.atr_multiplo_sl

    precio_referencia = h1_bars[-1].close
    if señal.direccion == SignalDirection.LONG:
        sl = precio_referencia - distancia_sl
        tp = precio_referencia + distancia_sl * risk.relacion_riesgo_beneficio
    else:
        sl = precio_referencia + distancia_sl
        tp = precio_referencia - distancia_sl * risk.relacion_riesgo_beneficio

    if lote_fijo is not None:
        resultado_orden = broker.place_market_order(symbol, señal.direccion, sl=sl, tp=tp, lotes=lote_fijo)
    else:
        riesgo_dinero = equity * risk.riesgo_por_operacion
        tamano_unidades = riesgo_dinero / distancia_sl
        resultado_orden = broker.place_market_order(
            symbol, señal.direccion, sl=sl, tp=tp, tamano_unidades=tamano_unidades
        )
    logger.log_evaluacion(
        señal,
        ejecutada=resultado_orden.enviada,
        detalle=f"disparador={motivo} | orden={resultado_orden}",
    )


def run_forever(
    *,
    symbol: str,
    broker: Mt5Broker,
    macro_client: ForexFactoryClient,
    price_client: Mt5PriceClient,
    decision_engine: DecisionEngine,
    risk: RiskConfig,
    logger: DecisionLogger,
    state_store: KillSwitchStateStore,
    intervalo_monitoreo_segundos: int = 45,
    intervalo_macro_segundos: int = INTERVALO_MACRO_SEGUNDOS_DEFECTO,
    lote_fijo: float | None = None,
) -> None:  # pragma: no cover - loop infinito, probado vía ejecutar_ciclo()
    """Loop principal de paper trading. Solo se detiene con Ctrl+C."""
    loop_state = LoopState()
    logger.log_evento("inicio", f"paper trading iniciado para {symbol}")
    while True:
        try:
            ejecutar_ciclo(
                momento=datetime.now(timezone.utc),
                symbol=symbol,
                broker=broker,
                macro_client=macro_client,
                price_client=price_client,
                decision_engine=decision_engine,
                risk=risk,
                logger=logger,
                state_store=state_store,
                loop_state=loop_state,
                intervalo_macro_segundos=intervalo_macro_segundos,
                lote_fijo=lote_fijo,
            )
        except Exception as exc:  # noqa: BLE001 - un ciclo fallido no debe tumbar 4-8 semanas de ejecución
            logger.log_evento("error_ciclo", f"{type(exc).__name__}: {exc}")
        time.sleep(intervalo_monitoreo_segundos)
