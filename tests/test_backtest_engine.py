from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot_xauusd.backtest.engine import BacktestEngine
from bot_xauusd.backtest.risk import RiskConfig
from bot_xauusd.models import PriceBar, SignalDirection
from bot_xauusd.signals.decision_engine import Signal
from bot_xauusd.signals.indicators import TrendDirection
from bot_xauusd.signals.macro_rules import MacroScoreResult
from bot_xauusd.signals.technical_trend import TechnicalScoreResult


class FakeDecisionEngine:
    """Devuelve, en orden, las señales precargadas; NONE cuando se agotan."""

    def __init__(self, señales: list[SignalDirection], atr: float = 2.0) -> None:
        self._señales = list(señales)
        self._atr = atr
        self.llamadas = 0

    def evaluate(self, eventos, h4_bars, h1_bars) -> Signal:
        self.llamadas += 1
        direccion = self._señales.pop(0) if self._señales else SignalDirection.NONE
        tecnico = TechnicalScoreResult(
            score=0.0,
            tendencia_h4=TrendDirection.NEUTRAL,
            tendencia_h1=TrendDirection.NEUTRAL,
            estructura_h1=TrendDirection.NEUTRAL,
            rsi_h1=50.0,
            atr_h1=self._atr,
            razones=[],
        )
        return Signal(
            direccion=direccion,
            score_final=0.0,
            macro=MacroScoreResult(score=0.0, resultados=[]),
            tecnico=tecnico,
            razones=[],
        )


def make_bar(hour: int, open_: float, high: float, low: float, close: float, day: int = 1) -> PriceBar:
    return PriceBar(
        symbol="XAUUSD",
        timeframe="H1",
        time=datetime(2026, 1, day, hour, tzinfo=timezone.utc),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100,
    )


DEFAULT_RISK = RiskConfig(
    capital_inicial=10_000.0,
    riesgo_por_operacion=0.005,
    perdida_maxima_diaria=0.5,
    perdida_maxima_semanal=0.5,
    drawdown_maximo_total=0.5,
    relacion_riesgo_beneficio=2.0,
    atr_multiplo_sl=1.5,
)


def test_long_trade_hits_take_profit_with_correct_sizing() -> None:
    h1_bars = [
        make_bar(0, 2000, 2001, 1999, 2000),  # decision -> LONG
        make_bar(1, 2000, 2001, 1999, 2000),  # entrada al open (2000)
        make_bar(2, 2000, 2007, 1999, 2005),  # TP (2006) tocado
        make_bar(3, 2005, 2006, 2004, 2005),
    ]
    engine = BacktestEngine(
        decision_engine=FakeDecisionEngine([SignalDirection.LONG]), risk_config=DEFAULT_RISK, min_historial=1
    )

    resultado = engine.run(h4_bars=h1_bars, h1_bars=h1_bars)

    assert len(resultado.trades) == 1
    trade = resultado.trades[0]
    assert trade.direccion == SignalDirection.LONG
    assert trade.entrada == 2000.0  # open de la vela SIGUIENTE a la señal, no el close de la señal
    assert trade.resultado == "tp"
    assert trade.pnl == pytest.approx(100.0)  # riesgo 0.5% de 10000=50 -> RR 1:2 -> +100
    assert resultado.capital_final == pytest.approx(10_100.0)


def test_short_trade_hits_stop_loss_with_correct_sizing() -> None:
    h1_bars = [
        make_bar(0, 2000, 2001, 1999, 2000),  # decision -> SHORT
        make_bar(1, 2000, 2001, 1999, 2000),  # entrada al open (2000)
        make_bar(2, 2002, 2004, 2001, 2003),  # SL (2003) tocado
        make_bar(3, 2003, 2004, 2002, 2003),
    ]
    engine = BacktestEngine(
        decision_engine=FakeDecisionEngine([SignalDirection.SHORT]), risk_config=DEFAULT_RISK, min_historial=1
    )

    resultado = engine.run(h4_bars=h1_bars, h1_bars=h1_bars)

    assert len(resultado.trades) == 1
    trade = resultado.trades[0]
    assert trade.direccion == SignalDirection.SHORT
    assert trade.resultado == "sl"
    assert trade.pnl == pytest.approx(-50.0)  # pérdida = exactamente el riesgo definido (0.5% de 10000)
    assert resultado.capital_final == pytest.approx(9_950.0)


def test_never_opens_a_second_position_while_one_is_open() -> None:
    h1_bars = [
        make_bar(0, 2000, 2001, 1999, 2000),  # decision -> LONG #1
        make_bar(1, 2000, 2001, 1999, 2000),  # entrada #1
        make_bar(2, 2000, 2001, 1999, 2000),  # sigue abierta; una 2a señal (LONG) no debe abrir nada
        make_bar(3, 2000, 2007, 1999, 2005),  # TP de la #1
        make_bar(4, 2005, 2006, 2004, 2005),
    ]
    fake = FakeDecisionEngine([SignalDirection.LONG, SignalDirection.LONG])
    engine = BacktestEngine(decision_engine=fake, risk_config=DEFAULT_RISK, min_historial=1)

    resultado = engine.run(h4_bars=h1_bars, h1_bars=h1_bars)

    assert len(resultado.trades) == 1  # la 2a señal se descartó, nunca hubo 2 posiciones a la vez


def test_daily_loss_limit_blocks_further_entries_same_day() -> None:
    risk = RiskConfig(
        capital_inicial=10_000.0,
        riesgo_por_operacion=0.01,
        perdida_maxima_diaria=0.01,
        perdida_maxima_semanal=0.5,
        drawdown_maximo_total=0.5,
        relacion_riesgo_beneficio=2.0,
        atr_multiplo_sl=1.5,
    )
    h1_bars = [
        make_bar(0, 2000, 2001, 1999, 2000),  # decision -> SHORT (pierde)
        make_bar(1, 2000, 2001, 1999, 2000),  # entrada
        make_bar(2, 2002, 2004, 2001, 2003),  # SL tocado -> pérdida diaria llega al límite (1%)
        make_bar(3, 2003, 2004, 2002, 2003),  # LONG en cola nunca se evalúa: día pausado
        make_bar(4, 2003, 2004, 2002, 2003),
        make_bar(5, 2003, 2004, 2002, 2003),
    ]
    fake = FakeDecisionEngine([SignalDirection.SHORT, SignalDirection.LONG])
    engine = BacktestEngine(decision_engine=fake, risk_config=risk, min_historial=1)

    resultado = engine.run(h4_bars=h1_bars, h1_bars=h1_bars)

    assert len(resultado.trades) == 1
    assert resultado.trades[0].resultado == "sl"
    assert resultado.capital_final == pytest.approx(9_900.0)
    assert not resultado.halted_permanently


def test_total_drawdown_triggers_permanent_halt() -> None:
    risk = RiskConfig(
        capital_inicial=10_000.0,
        riesgo_por_operacion=0.01,
        perdida_maxima_diaria=0.5,
        perdida_maxima_semanal=0.5,
        drawdown_maximo_total=0.005,  # una sola pérdida del 1% ya lo dispara
        relacion_riesgo_beneficio=2.0,
        atr_multiplo_sl=1.5,
    )
    h1_bars = [
        make_bar(0, 2000, 2001, 1999, 2000),  # decision -> SHORT (pierde)
        make_bar(1, 2000, 2001, 1999, 2000),
        make_bar(2, 2002, 2004, 2001, 2003),  # SL -> dispara el kill switch permanente
        make_bar(3, 2003, 2004, 2002, 2003, day=2),  # incluso al día siguiente, no se evalúa nada más
        make_bar(4, 2003, 2004, 2002, 2003, day=2),
    ]
    fake = FakeDecisionEngine([SignalDirection.SHORT, SignalDirection.LONG])
    engine = BacktestEngine(decision_engine=fake, risk_config=risk, min_historial=1)

    resultado = engine.run(h4_bars=h1_bars, h1_bars=h1_bars)

    assert resultado.halted_permanently is True
    assert resultado.halted_en == datetime(2026, 1, 1, 2, tzinfo=timezone.utc)
    assert len(resultado.trades) == 1


def test_insufficient_history_never_calls_decision_engine() -> None:
    h1_bars = [make_bar(h, 2000, 2001, 1999, 2000) for h in range(3)]
    fake = FakeDecisionEngine([SignalDirection.LONG])
    engine = BacktestEngine(decision_engine=fake, risk_config=DEFAULT_RISK, min_historial=10)

    resultado = engine.run(h4_bars=h1_bars, h1_bars=h1_bars)

    assert fake.llamadas == 0
    assert resultado.trades == []
