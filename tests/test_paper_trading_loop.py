from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot_xauusd.backtest.risk import RiskConfig
from bot_xauusd.live.decision_log import DecisionLogger
from bot_xauusd.execution.mt5_broker import Mt5ExecutionError
from bot_xauusd.ingestion.price_mt5 import Mt5ConnectionError
from bot_xauusd.live.loop import (
    LoopState,
    _actualizar_trailing_stop,
    _obtener_eventos_fred,
    _obtener_eventos_macro,
    _obtener_eventos_sentimiento,
    _reconectar_si_hace_falta,
    debe_evaluar,
    ejecutar_ciclo,
)
from bot_xauusd.live.state import KillSwitchStateStore
from bot_xauusd.models import ImpactLevel, MacroEvent, PriceBar, SignalDirection
from bot_xauusd.signals.decision_engine import Signal
from bot_xauusd.signals.indicators import TrendDirection
from bot_xauusd.signals.macro_rules import MacroScoreResult
from bot_xauusd.signals.technical_trend import TechnicalScoreResult


def make_event(evento: str, impacto: ImpactLevel, valor_real: float | None, fecha: datetime) -> MacroEvent:
    return MacroEvent(
        evento=evento, fecha=fecha, pais="USD", impacto=impacto, valor_esperado=None, valor_real=valor_real, valor_previo=None
    )


# --- debe_evaluar() -----------------------------------------------------------------


def test_first_call_triggers_h1_close() -> None:
    loop_state = LoopState()
    momento = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)

    disparar, motivo = debe_evaluar(momento, loop_state, eventos=[])

    assert disparar is True
    assert "H1" in motivo


def test_same_hour_does_not_trigger_again() -> None:
    loop_state = LoopState(ultima_hora_evaluada="2026-01-01T10")
    momento = datetime(2026, 1, 1, 10, 30, tzinfo=timezone.utc)

    disparar, _ = debe_evaluar(momento, loop_state, eventos=[])

    assert disparar is False


def test_new_high_impact_event_triggers_immediately_within_same_hour() -> None:
    loop_state = LoopState(ultima_hora_evaluada="2026-01-01T10")
    momento = datetime(2026, 1, 1, 10, 5, tzinfo=timezone.utc)
    evento = make_event("Federal Funds Rate", ImpactLevel.ALTO, valor_real=4.0, fecha=momento)

    disparar, motivo = debe_evaluar(momento, loop_state, eventos=[evento])

    assert disparar is True
    assert "alto impacto" in motivo


def test_high_impact_event_without_published_value_does_not_trigger() -> None:
    loop_state = LoopState(ultima_hora_evaluada="2026-01-01T10")
    momento = datetime(2026, 1, 1, 10, 5, tzinfo=timezone.utc)
    evento = make_event("Federal Funds Rate", ImpactLevel.ALTO, valor_real=None, fecha=momento)  # aún no publicado

    disparar, _ = debe_evaluar(momento, loop_state, eventos=[evento])

    assert disparar is False


def test_same_high_impact_event_does_not_retrigger() -> None:
    loop_state = LoopState(ultima_hora_evaluada="2026-01-01T10")
    momento = datetime(2026, 1, 1, 10, 5, tzinfo=timezone.utc)
    evento = make_event("Federal Funds Rate", ImpactLevel.ALTO, valor_real=4.0, fecha=momento)

    debe_evaluar(momento, loop_state, eventos=[evento])  # 1er disparo, se marca como visto
    disparar_de_nuevo, _ = debe_evaluar(momento, loop_state, eventos=[evento])

    assert disparar_de_nuevo is False


# --- ejecutar_ciclo() -----------------------------------------------------------------


class FakeBroker:
    def __init__(
        self,
        equity: float = 10_000.0,
        posiciones_abiertas: int = 0,
        ultimo_cierre: dict | None = None,
        posicion_abierta: dict | None = None,
    ) -> None:
        self.equity = equity
        self.posiciones_abiertas = posiciones_abiertas
        self.ultimo_cierre = ultimo_cierre
        self.posicion_abierta = posicion_abierta
        self.ordenes_enviadas: list = []
        self.sl_actualizados: list = []

    def get_account_equity(self) -> float:
        return self.equity

    def get_open_positions_count(self, symbol: str) -> int:
        return self.posiciones_abiertas

    def get_last_closed_trade(self, symbol, desde):
        return self.ultimo_cierre

    def get_open_position(self, symbol):
        return self.posicion_abierta

    def update_stop_loss(self, symbol, ticket, nuevo_sl, tp_actual) -> bool:
        self.sl_actualizados.append((ticket, nuevo_sl, tp_actual))
        return True

    def place_market_order(self, symbol, direccion, sl, tp, *, tamano_unidades=None, lotes=None):
        self.ordenes_enviadas.append((symbol, direccion, sl, tp, tamano_unidades, lotes))
        return _FakeOrderResult()


class _FakeOrderResult:
    enviada = True
    dry_run = True


class FakeMacroClient:
    def __init__(self, eventos: list | None = None, fallar: bool = False) -> None:
        self._eventos = eventos or []
        self.fallar = fallar
        self.llamadas = 0

    def get_calendar(self, window: str = "this_week"):
        self.llamadas += 1
        if self.fallar:
            from bot_xauusd.ingestion.macro_forexfactory import ForexFactoryApiError

            raise ForexFactoryApiError("429 simulado")
        return self._eventos


class FakePriceClient:
    def __init__(self, n_bars: int = 250) -> None:
        self.n_bars = n_bars

    def get_bars(self, symbol, timeframe, count):
        return [
            PriceBar(symbol=symbol, timeframe=timeframe, time=datetime(2026, 1, 1, tzinfo=timezone.utc), open=2000, high=2001, low=1999, close=2000, volume=1)
            for _ in range(self.n_bars)
        ]


class FakeDecisionEngine:
    def __init__(self, direccion: SignalDirection, atr: float = 2.0) -> None:
        self._direccion = direccion
        self._atr = atr

    def evaluate(self, eventos, h4_bars, h1_bars) -> Signal:
        tecnico = TechnicalScoreResult(
            score=0.0, tendencia_h4=TrendDirection.NEUTRAL, tendencia_h1=TrendDirection.NEUTRAL,
            estructura_h1=TrendDirection.NEUTRAL, rsi_h1=50.0, atr_h1=self._atr, razones=[],
        )
        return Signal(direccion=self._direccion, score_final=0.0, macro=MacroScoreResult(score=0.0, resultados=[]), tecnico=tecnico, razones=[])


def make_ciclo_kwargs(tmp_path: Path, **overrides):
    base = dict(
        momento=datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
        symbol="XAUUSD!",
        broker=FakeBroker(),
        macro_client=FakeMacroClient(),
        price_client=FakePriceClient(),
        decision_engine=FakeDecisionEngine(SignalDirection.LONG),
        risk=RiskConfig(),
        logger=DecisionLogger(tmp_path / "decisiones.jsonl"),
        state_store=KillSwitchStateStore(tmp_path / "estado.json"),
        loop_state=LoopState(),
    )
    base.update(overrides)
    return base


def read_log(path: Path) -> list[dict]:
    import json

    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_signal_places_an_order_and_logs_it(tmp_path: Path) -> None:
    broker = FakeBroker()
    kwargs = make_ciclo_kwargs(tmp_path, broker=broker)

    ejecutar_ciclo(**kwargs)

    assert len(broker.ordenes_enviadas) == 1
    registros = read_log(tmp_path / "decisiones.jsonl")
    assert any(r["tipo"] == "evaluacion" and r["ejecutada"] is True for r in registros)


def test_open_position_blocks_new_entry(tmp_path: Path) -> None:
    broker = FakeBroker(posiciones_abiertas=1)
    kwargs = make_ciclo_kwargs(tmp_path, broker=broker)

    ejecutar_ciclo(**kwargs)

    assert broker.ordenes_enviadas == []
    registros = read_log(tmp_path / "decisiones.jsonl")
    assert any("posición abierta" in r.get("detalle", "") for r in registros)


def test_permanent_halt_blocks_new_entries(tmp_path: Path) -> None:
    from bot_xauusd.live.state import KillSwitchState

    store = KillSwitchStateStore(tmp_path / "estado.json")
    momento = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    estado_halted = KillSwitchState.nuevo(10_000.0, momento)
    estado_halted.halted_permanently = True
    estado_halted.halted_en = momento.isoformat()
    store.save(estado_halted)

    broker = FakeBroker(equity=9_000.0)
    kwargs = make_ciclo_kwargs(tmp_path, broker=broker, state_store=store, momento=momento)

    ejecutar_ciclo(**kwargs)

    assert broker.ordenes_enviadas == []


def test_no_signal_still_logs_the_evaluation(tmp_path: Path) -> None:
    kwargs = make_ciclo_kwargs(tmp_path, decision_engine=FakeDecisionEngine(SignalDirection.NONE))

    ejecutar_ciclo(**kwargs)

    registros = read_log(tmp_path / "decisiones.jsonl")
    assert any(r["tipo"] == "evaluacion" and r["ejecutada"] is False for r in registros)


def test_when_not_time_to_evaluate_no_order_and_no_evaluation_log(tmp_path: Path) -> None:
    broker = FakeBroker()
    loop_state = LoopState(ultima_hora_evaluada="2026-01-01T10")  # ya evaluado esta hora
    kwargs = make_ciclo_kwargs(tmp_path, broker=broker, loop_state=loop_state)

    ejecutar_ciclo(**kwargs)

    assert broker.ordenes_enviadas == []
    registros = read_log(tmp_path / "decisiones.jsonl")
    assert not any(r["tipo"] == "evaluacion" for r in registros)


# --- throttling del calendario macro ---------------------------------------------------
# Se agregó tras correr el bot en vivo: pedir el feed de ForexFactory en cada tick de
# monitoreo (cada 45s) lo satura y empieza a devolver 429 Rate Limited.


def test_macro_calendar_is_not_refetched_within_the_throttle_window(tmp_path: Path) -> None:
    macro_client = FakeMacroClient()
    logger = DecisionLogger(tmp_path / "decisiones.jsonl")
    loop_state = LoopState()
    momento = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)

    eventos_1 = _obtener_eventos_macro(momento, loop_state, macro_client, logger, intervalo_macro_segundos=180)
    eventos_2 = _obtener_eventos_macro(
        momento + timedelta(seconds=45), loop_state, macro_client, logger, intervalo_macro_segundos=180
    )

    assert macro_client.llamadas == 1
    assert eventos_1 == eventos_2


def test_macro_calendar_is_refetched_after_the_throttle_window_expires(tmp_path: Path) -> None:
    macro_client = FakeMacroClient()
    logger = DecisionLogger(tmp_path / "decisiones.jsonl")
    loop_state = LoopState()
    momento = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)

    _obtener_eventos_macro(momento, loop_state, macro_client, logger, intervalo_macro_segundos=180)
    _obtener_eventos_macro(
        momento + timedelta(seconds=200), loop_state, macro_client, logger, intervalo_macro_segundos=180
    )

    assert macro_client.llamadas == 2


def test_ejecutar_ciclo_reuses_cached_macro_events_across_calls(tmp_path: Path) -> None:
    macro_client = FakeMacroClient()
    loop_state = LoopState()
    momento = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)

    kwargs = make_ciclo_kwargs(tmp_path, macro_client=macro_client, loop_state=loop_state, momento=momento)
    ejecutar_ciclo(**kwargs)

    kwargs2 = make_ciclo_kwargs(
        tmp_path, macro_client=macro_client, loop_state=loop_state, momento=momento + timedelta(seconds=45)
    )
    ejecutar_ciclo(**kwargs2)

    assert macro_client.llamadas == 1


# --- lote fijo (MT5_LOTE_FIJO) -----------------------------------------------------------


def test_signal_uses_fixed_lot_when_configured(tmp_path: Path) -> None:
    broker = FakeBroker()
    kwargs = make_ciclo_kwargs(tmp_path, broker=broker, lote_fijo=0.05)

    ejecutar_ciclo(**kwargs)

    assert len(broker.ordenes_enviadas) == 1
    _symbol, _direccion, _sl, _tp, tamano_unidades, lotes = broker.ordenes_enviadas[0]
    assert lotes == 0.05
    assert tamano_unidades is None


def test_signal_uses_risk_based_sizing_when_no_fixed_lot_configured(tmp_path: Path) -> None:
    broker = FakeBroker()
    kwargs = make_ciclo_kwargs(tmp_path, broker=broker)  # lote_fijo no incluido -> None por defecto

    ejecutar_ciclo(**kwargs)

    _symbol, _direccion, _sl, _tp, tamano_unidades, lotes = broker.ordenes_enviadas[0]
    assert lotes is None
    assert tamano_unidades is not None


# --- resiliencia ante fallas del feed macro (evita tormenta de reintentos) ----------------


def test_failed_macro_fetch_still_respects_the_throttle_window(tmp_path: Path) -> None:
    macro_client = FakeMacroClient(fallar=True)
    logger = DecisionLogger(tmp_path / "decisiones.jsonl")
    loop_state = LoopState()
    momento = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)

    _obtener_eventos_macro(momento, loop_state, macro_client, logger, intervalo_macro_segundos=180)
    _obtener_eventos_macro(
        momento + timedelta(seconds=45), loop_state, macro_client, logger, intervalo_macro_segundos=180
    )

    # Aunque la 1ra falló, la 2da (45s despues) NO debe reintentar todavía —
    # de lo contrario, cada tick de monitoreo martillaría el feed sin parar.
    assert macro_client.llamadas == 1
    registros = read_log(tmp_path / "decisiones.jsonl")
    assert sum(1 for r in registros if r["tipo"] == "error_macro") == 1


# --- detección de cierre de posición (SL/TP los gestiona el broker, no el loop) -----------


def test_position_closure_is_logged_with_the_deal_details(tmp_path: Path) -> None:
    cierre = {"ticket_posicion": 123, "precio_cierre": 1990.0, "profit": -25.5, "volumen": 0.15}
    broker = FakeBroker(posiciones_abiertas=0, ultimo_cierre=cierre)
    loop_state = LoopState(posicion_abierta_anterior=True)  # ciclo anterior: sí había posición
    kwargs = make_ciclo_kwargs(tmp_path, broker=broker, loop_state=loop_state)

    ejecutar_ciclo(**kwargs)

    registros = read_log(tmp_path / "decisiones.jsonl")
    cierres_logueados = [r for r in registros if r["tipo"] == "posicion_cerrada"]
    assert len(cierres_logueados) == 1
    assert "123" in cierres_logueados[0]["detalle"]
    assert "-25.50" in cierres_logueados[0]["detalle"]
    assert loop_state.posicion_abierta_anterior is False


def test_no_closure_logged_when_position_stays_open(tmp_path: Path) -> None:
    broker = FakeBroker(posiciones_abiertas=1)
    loop_state = LoopState(posicion_abierta_anterior=True)
    kwargs = make_ciclo_kwargs(tmp_path, broker=broker, loop_state=loop_state)

    ejecutar_ciclo(**kwargs)

    registros = read_log(tmp_path / "decisiones.jsonl")
    assert not any(r["tipo"] == "posicion_cerrada" for r in registros)
    assert loop_state.posicion_abierta_anterior is True


def test_no_closure_logged_when_there_was_no_previous_position(tmp_path: Path) -> None:
    broker = FakeBroker(posiciones_abiertas=0)
    loop_state = LoopState(posicion_abierta_anterior=False)
    kwargs = make_ciclo_kwargs(tmp_path, broker=broker, loop_state=loop_state)

    ejecutar_ciclo(**kwargs)

    registros = read_log(tmp_path / "decisiones.jsonl")
    assert not any(r["tipo"] == "posicion_cerrada" for r in registros)


# --- trailing stop (breakeven + ATR) -------------------------------------------------

TRAILING_RISK = RiskConfig(trailing_habilitado=True, trailing_activar_en_r=1.0, trailing_atr_multiplo=1.5)


def make_logger(tmp_path: Path) -> DecisionLogger:
    return DecisionLogger(tmp_path / "decisiones.jsonl")


def test_trailing_disabled_does_nothing(tmp_path: Path) -> None:
    broker = FakeBroker(posicion_abierta={"ticket": 1, "entrada": 2000, "sl": 1990, "tp": 2050, "precio_actual": 2050, "direccion": SignalDirection.LONG})
    _actualizar_trailing_stop("XAUUSD!", broker, FakePriceClient(), RiskConfig(trailing_habilitado=False), make_logger(tmp_path), LoopState())

    assert broker.sl_actualizados == []


def test_no_open_position_does_nothing(tmp_path: Path) -> None:
    broker = FakeBroker(posicion_abierta=None)
    _actualizar_trailing_stop("XAUUSD!", broker, FakePriceClient(), TRAILING_RISK, make_logger(tmp_path), LoopState())

    assert broker.sl_actualizados == []


def test_long_below_activation_threshold_does_not_move_sl(tmp_path: Path) -> None:
    # riesgo inicial = 2000-1990 = 10; ganancia = 2005-2000 = 5 < 10 -> no se activa
    broker = FakeBroker(posicion_abierta={"ticket": 1, "entrada": 2000, "sl": 1990, "tp": 2050, "precio_actual": 2005, "direccion": SignalDirection.LONG})
    _actualizar_trailing_stop("XAUUSD!", broker, FakePriceClient(), TRAILING_RISK, make_logger(tmp_path), LoopState())

    assert broker.sl_actualizados == []


def test_long_past_activation_moves_sl_to_atr_trail(tmp_path: Path) -> None:
    # riesgo inicial=10; ganancia=15>=10 -> activado. ATR=2.0 (FakePriceClient) x1.5=3.0
    # candidato ATR = 2015-3=2012; candidato breakeven=2000; max(1990,2000,2012)=2012
    broker = FakeBroker(posicion_abierta={"ticket": 7, "entrada": 2000, "sl": 1990, "tp": 2050, "precio_actual": 2015, "direccion": SignalDirection.LONG})
    _actualizar_trailing_stop("XAUUSD!", broker, FakePriceClient(), TRAILING_RISK, make_logger(tmp_path), LoopState())

    assert broker.sl_actualizados == [(7, 2012.0, 2050)]


def test_short_past_activation_moves_sl_down_to_atr_trail(tmp_path: Path) -> None:
    # riesgo inicial=10; ganancia=2000-1985=15>=10 -> activado. candidato ATR=1985+3=1988
    # candidato breakeven=2000; min(2010,2000,1988)=1988
    broker = FakeBroker(posicion_abierta={"ticket": 9, "entrada": 2000, "sl": 2010, "tp": 1950, "precio_actual": 1985, "direccion": SignalDirection.SHORT})
    _actualizar_trailing_stop("XAUUSD!", broker, FakePriceClient(), TRAILING_RISK, make_logger(tmp_path), LoopState())

    assert broker.sl_actualizados == [(9, 1988.0, 1950)]


def test_trailing_never_moves_sl_backwards(tmp_path: Path) -> None:
    # el SL actual (2012) ya está más protegido que lo que el trailing calcularía
    # de cero (2000 breakeven, o 2012 con ATR) -- no debe empeorarlo.
    broker = FakeBroker(posicion_abierta={"ticket": 3, "entrada": 2000, "sl": 2012, "tp": 2050, "precio_actual": 2015, "direccion": SignalDirection.LONG})
    _actualizar_trailing_stop("XAUUSD!", broker, FakePriceClient(), TRAILING_RISK, make_logger(tmp_path), LoopState())

    assert broker.sl_actualizados == []


def test_original_risk_distance_is_cached_not_recomputed_from_a_moved_sl(tmp_path: Path) -> None:
    loop_state = LoopState()
    broker = FakeBroker(posicion_abierta={"ticket": 5, "entrada": 2000, "sl": 1990, "tp": 2050, "precio_actual": 2005, "direccion": SignalDirection.LONG})
    _actualizar_trailing_stop("XAUUSD!", broker, FakePriceClient(), TRAILING_RISK, make_logger(tmp_path), loop_state)
    assert loop_state.trailing_distancia_riesgo == 10.0

    # el broker ya movió el SL (como si un ciclo anterior lo hubiera hecho) —
    # la distancia de riesgo cacheada debe seguir siendo la original (10), no 5
    broker.posicion_abierta["sl"] = 2000.0
    broker.posicion_abierta["precio_actual"] = 2011.0  # ganancia=11 >= 10 (cacheada) -> se activa
    _actualizar_trailing_stop("XAUUSD!", broker, FakePriceClient(), TRAILING_RISK, make_logger(tmp_path), loop_state)

    assert loop_state.trailing_distancia_riesgo == 10.0


# --- FRED (VIX/DXY) y sentimiento de noticias (refuerzo de aversión al riesgo) -----------


class FakeFredClient:
    def __init__(self, eventos: list | None = None) -> None:
        self._eventos = eventos or []
        self.llamadas = 0

    def get_latest_events(self):
        self.llamadas += 1
        return self._eventos


class FakeSentimentClient:
    def __init__(self, eventos: list | None = None) -> None:
        self._eventos = eventos or []
        self.llamadas = 0

    def get_latest_events(self):
        self.llamadas += 1
        return self._eventos


def test_fred_client_none_returns_empty_list_without_calling_anything(tmp_path: Path) -> None:
    eventos = _obtener_eventos_fred(
        datetime(2026, 1, 1, tzinfo=timezone.utc), LoopState(), None, make_logger(tmp_path), intervalo_segundos=180
    )
    assert eventos == []


def test_fred_events_are_cached_within_the_throttle_window(tmp_path: Path) -> None:
    fred_client = FakeFredClient(eventos=[make_event("VIX", ImpactLevel.MEDIO, valor_real=20.0, fecha=datetime(2026, 1, 1, tzinfo=timezone.utc))])
    logger = make_logger(tmp_path)
    loop_state = LoopState()
    momento = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)

    _obtener_eventos_fred(momento, loop_state, fred_client, logger, intervalo_segundos=180)
    _obtener_eventos_fred(momento + timedelta(seconds=45), loop_state, fred_client, logger, intervalo_segundos=180)

    assert fred_client.llamadas == 1


def test_sentiment_client_none_returns_empty_list_without_calling_anything(tmp_path: Path) -> None:
    eventos = _obtener_eventos_sentimiento(
        datetime(2026, 1, 1, tzinfo=timezone.utc), LoopState(), None, make_logger(tmp_path), intervalo_segundos=7200
    )
    assert eventos == []


def test_sentiment_events_are_cached_within_the_throttle_window(tmp_path: Path) -> None:
    sentiment_client = FakeSentimentClient(eventos=[])
    logger = make_logger(tmp_path)
    loop_state = LoopState()
    momento = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)

    _obtener_eventos_sentimiento(momento, loop_state, sentiment_client, logger, intervalo_segundos=7200)
    _obtener_eventos_sentimiento(momento + timedelta(minutes=10), loop_state, sentiment_client, logger, intervalo_segundos=7200)

    assert sentiment_client.llamadas == 1


def test_ejecutar_ciclo_merges_events_from_all_three_sources(tmp_path: Path) -> None:
    momento = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    ff_event = make_event("ForexFactory event", ImpactLevel.ALTO, valor_real=1.0, fecha=momento)
    fred_event = make_event("VIX", ImpactLevel.MEDIO, valor_real=20.0, fecha=momento)
    sentiment_event = make_event("Sentimiento de noticias", ImpactLevel.MEDIO, valor_real=-0.3, fecha=momento)

    eventos_recibidos = {}

    class CapturingDecisionEngine:
        def evaluate(self, eventos, h4_bars, h1_bars):
            eventos_recibidos["eventos"] = list(eventos)
            tecnico = TechnicalScoreResult(
                score=0.0, tendencia_h4=TrendDirection.NEUTRAL, tendencia_h1=TrendDirection.NEUTRAL,
                estructura_h1=TrendDirection.NEUTRAL, rsi_h1=50.0, atr_h1=2.0, razones=[],
            )
            return Signal(direccion=SignalDirection.NONE, score_final=0.0, macro=MacroScoreResult(score=0.0, resultados=[]), tecnico=tecnico, razones=[])

    kwargs = make_ciclo_kwargs(
        tmp_path,
        momento=momento,
        macro_client=FakeMacroClient(eventos=[ff_event]),
        fred_client=FakeFredClient(eventos=[fred_event]),
        sentiment_client=FakeSentimentClient(eventos=[sentiment_event]),
        decision_engine=CapturingDecisionEngine(),
    )
    ejecutar_ciclo(**kwargs)

    assert eventos_recibidos["eventos"] == [ff_event, fred_event, sentiment_event]


# --- reconexión ante pérdida de la conexión IPC con MT5 ------------------------------------


class FakeReconnectClient:
    def __init__(self, falla_al_reconectar: bool = False) -> None:
        self.conexiones = 0
        self.falla_al_reconectar = falla_al_reconectar

    def connect(self) -> None:
        self.conexiones += 1
        if self.falla_al_reconectar:
            raise Mt5ExecutionError("sigue caído")


def test_reconnects_broker_and_price_client_on_mt5_execution_error(tmp_path: Path) -> None:
    broker = FakeReconnectClient()
    price_client = FakeReconnectClient()
    logger = make_logger(tmp_path)

    _reconectar_si_hace_falta(Mt5ExecutionError("IPC send failed"), broker, price_client, logger)

    assert broker.conexiones == 1
    assert price_client.conexiones == 1
    registros = read_log(tmp_path / "decisiones.jsonl")
    assert any(r["tipo"] == "reconexion_mt5" for r in registros)


def test_reconnects_on_mt5_connection_error_too(tmp_path: Path) -> None:
    broker = FakeReconnectClient()
    price_client = FakeReconnectClient()
    logger = make_logger(tmp_path)

    _reconectar_si_hace_falta(Mt5ConnectionError("desconectado"), broker, price_client, logger)

    assert broker.conexiones == 1


def test_does_not_reconnect_on_unrelated_errors(tmp_path: Path) -> None:
    broker = FakeReconnectClient()
    price_client = FakeReconnectClient()
    logger = make_logger(tmp_path)

    _reconectar_si_hace_falta(ValueError("otra cosa"), broker, price_client, logger)

    assert broker.conexiones == 0
    assert price_client.conexiones == 0


def test_logs_failure_when_reconnection_itself_fails(tmp_path: Path) -> None:
    broker = FakeReconnectClient(falla_al_reconectar=True)
    price_client = FakeReconnectClient()
    logger = make_logger(tmp_path)

    _reconectar_si_hace_falta(Mt5ExecutionError("IPC send failed"), broker, price_client, logger)

    registros = read_log(tmp_path / "decisiones.jsonl")
    assert any(r["tipo"] == "reconexion_mt5_fallo" for r in registros)
