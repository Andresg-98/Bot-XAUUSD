from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot_xauusd.backtest.risk import RiskConfig
from bot_xauusd.live.decision_log import DecisionLogger
from bot_xauusd.live.loop import LoopState, _obtener_eventos_macro, debe_evaluar, ejecutar_ciclo
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
    def __init__(self, equity: float = 10_000.0, posiciones_abiertas: int = 0) -> None:
        self.equity = equity
        self.posiciones_abiertas = posiciones_abiertas
        self.ordenes_enviadas: list = []

    def get_account_equity(self) -> float:
        return self.equity

    def get_open_positions_count(self, symbol: str) -> int:
        return self.posiciones_abiertas

    def place_market_order(self, symbol, direccion, tamano_unidades, sl, tp):
        self.ordenes_enviadas.append((symbol, direccion, tamano_unidades, sl, tp))
        return _FakeOrderResult()


class _FakeOrderResult:
    enviada = True
    dry_run = True


class FakeMacroClient:
    def __init__(self, eventos: list | None = None) -> None:
        self._eventos = eventos or []
        self.llamadas = 0

    def get_calendar(self, window: str = "this_week"):
        self.llamadas += 1
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
