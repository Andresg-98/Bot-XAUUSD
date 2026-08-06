from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import bot_xauusd.execution.mt5_broker as mt5_broker
from bot_xauusd.config import Settings
from bot_xauusd.execution.mt5_broker import Mt5Broker, Mt5ExecutionError
from bot_xauusd.models import SignalDirection


class FakeMt5:
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_FOK = 2
    ORDER_FILLING_RETURN = 3
    DEAL_ENTRY_IN = 0
    DEAL_ENTRY_OUT = 1
    TRADE_RETCODE_DONE = 10009

    def __init__(self) -> None:
        self.symbol_info_obj = SimpleNamespace(
            trade_contract_size=100.0,
            volume_step=0.01,
            volume_min=0.01,
            volume_max=100.0,
            filling_mode=2,  # solo IOC soportado, por defecto en los tests
        )
        self.tick = SimpleNamespace(ask=2001.0, bid=2000.0)
        self.account = SimpleNamespace(equity=100_000.0)
        self.positiciones: list = []
        self.deals_historicos: list = []
        self.orden_a_devolver = SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=555, price=2001.0)
        self.ultima_request: dict | None = None

    def initialize(self, **kwargs: object) -> bool:
        return True

    def shutdown(self) -> None:
        pass

    def last_error(self) -> tuple[int, str]:
        return (1, "fake error")

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return True

    def symbol_info(self, symbol: str):
        return self.symbol_info_obj

    def symbol_info_tick(self, symbol: str):
        return self.tick

    def account_info(self):
        return self.account

    def positions_get(self, symbol: str = None, ticket: int = None):
        if ticket is not None:
            return [p for p in self.positiciones if p.ticket == ticket]
        return [p for p in self.positiciones if p.symbol == symbol]

    def order_send(self, request: dict):
        self.ultima_request = request
        return self.orden_a_devolver

    def history_deals_get(self, date_from, date_to, group: str = "*"):
        return list(self.deals_historicos)


def make_settings(**overrides: object) -> Settings:
    base = dict(
        fred_api_key=None,
        twelvedata_api_key=None,
        alphavantage_api_key=None,
        mt5_login=None,
        mt5_password=None,
        mt5_server=None,
        mt5_terminal_path=None,
        mt5_symbol="XAUUSD!",
        mt5_lote_fijo=None,
    )
    base.update(overrides)
    return Settings(**base)


def connected_broker(monkeypatch: pytest.MonkeyPatch, fake: FakeMt5, dry_run: bool) -> Mt5Broker:
    monkeypatch.setattr(mt5_broker, "mt5", fake)
    broker = Mt5Broker(make_settings(), dry_run=dry_run)
    broker.connect()
    return broker


def test_dry_run_never_calls_order_send(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    broker = connected_broker(monkeypatch, fake, dry_run=True)

    # 1 lote = 100 oz; riesgo de 50 oz -> 0.5 lotes
    resultado = broker.place_market_order("XAUUSD!", SignalDirection.LONG, tamano_unidades=50.0, sl=1990.0, tp=2020.0)

    assert resultado.enviada is True
    assert resultado.dry_run is True
    assert resultado.volumen == pytest.approx(0.5)
    assert resultado.precio == 2001.0  # ask, por ser LONG
    assert fake.ultima_request is None  # nunca se llamó order_send


def test_live_order_sends_request_and_returns_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    broker = connected_broker(monkeypatch, fake, dry_run=False)

    resultado = broker.place_market_order("XAUUSD!", SignalDirection.SHORT, tamano_unidades=100.0, sl=2010.0, tp=1980.0)

    assert resultado.enviada is True
    assert resultado.dry_run is False
    assert resultado.ticket == 555
    assert fake.ultima_request is not None
    assert fake.ultima_request["type"] == FakeMt5.ORDER_TYPE_SELL
    assert fake.ultima_request["volume"] == pytest.approx(1.0)
    assert fake.ultima_request["price"] == 2000.0  # bid, por ser SHORT


def test_uses_fok_when_broker_does_not_support_ioc(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    fake.symbol_info_obj.filling_mode = 1  # solo FOK soportado (caso real: BridgeMarkets-MT5)
    broker = connected_broker(monkeypatch, fake, dry_run=False)

    broker.place_market_order("XAUUSD!", SignalDirection.LONG, tamano_unidades=50.0, sl=1990.0, tp=2020.0)

    assert fake.ultima_request["type_filling"] == FakeMt5.ORDER_FILLING_FOK


def test_fixed_lot_bypasses_risk_based_sizing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    broker = connected_broker(monkeypatch, fake, dry_run=True)

    resultado = broker.place_market_order("XAUUSD!", SignalDirection.LONG, sl=1990.0, tp=2020.0, lotes=0.05)

    assert resultado.volumen == pytest.approx(0.05)


def test_fixed_lot_below_broker_minimum_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    fake.symbol_info_obj.volume_min = 0.1
    broker = connected_broker(monkeypatch, fake, dry_run=True)

    resultado = broker.place_market_order("XAUUSD!", SignalDirection.LONG, sl=1990.0, tp=2020.0, lotes=0.05)

    assert resultado.enviada is False
    assert "mínimo" in resultado.motivo_rechazo


def test_specifying_both_tamano_unidades_and_lotes_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    broker = connected_broker(monkeypatch, fake, dry_run=True)
    with pytest.raises(ValueError):
        broker.place_market_order("XAUUSD!", SignalDirection.LONG, sl=1990.0, tp=2020.0, tamano_unidades=10.0, lotes=0.05)


def test_specifying_neither_tamano_unidades_nor_lotes_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    broker = connected_broker(monkeypatch, fake, dry_run=True)
    with pytest.raises(ValueError):
        broker.place_market_order("XAUUSD!", SignalDirection.LONG, sl=1990.0, tp=2020.0)


def test_volume_below_broker_minimum_is_rejected_not_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    fake.symbol_info_obj.volume_min = 0.5  # el broker exige mínimo 0.5 lotes
    broker = connected_broker(monkeypatch, fake, dry_run=True)

    # riesgo de solo 10 oz -> 0.1 lotes, por debajo del mínimo del broker (0.5)
    resultado = broker.place_market_order("XAUUSD!", SignalDirection.LONG, tamano_unidades=10.0, sl=1990.0, tp=2020.0)

    assert resultado.enviada is False
    assert resultado.motivo_rechazo is not None
    assert "mínimo" in resultado.motivo_rechazo
    assert fake.ultima_request is None


def test_failed_order_send_reports_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    fake.orden_a_devolver = SimpleNamespace(retcode=10004, order=None, price=None, comment="requote")
    broker = connected_broker(monkeypatch, fake, dry_run=False)

    resultado = broker.place_market_order("XAUUSD!", SignalDirection.LONG, tamano_unidades=50.0, sl=1990.0, tp=2020.0)

    assert resultado.enviada is False
    assert "10004" in resultado.motivo_rechazo


def test_get_open_positions_count_filters_by_magic(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    fake.positiciones = [
        SimpleNamespace(symbol="XAUUSD!", magic=Mt5Broker.MAGIC),
        SimpleNamespace(symbol="XAUUSD!", magic=999999),  # otra EA / manual, no cuenta
    ]
    broker = connected_broker(monkeypatch, fake, dry_run=True)

    assert broker.get_open_positions_count("XAUUSD!") == 1


def test_operations_require_connect_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mt5_broker, "mt5", FakeMt5())
    broker = Mt5Broker(make_settings(), dry_run=True)
    with pytest.raises(Mt5ExecutionError):
        broker.place_market_order("XAUUSD!", SignalDirection.LONG, sl=1990.0, tp=2020.0, tamano_unidades=10.0)


def test_missing_mt5_package_raises_on_init(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mt5_broker, "mt5", None)
    with pytest.raises(Mt5ExecutionError):
        Mt5Broker(make_settings())


def test_get_closed_trades_since_returns_empty_list_without_closing_deals(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    broker = connected_broker(monkeypatch, fake, dry_run=True)

    assert broker.get_closed_trades_since("XAUUSD!", datetime(2026, 1, 1, tzinfo=timezone.utc)) == []


def test_get_closed_trades_since_ignores_opening_deals_and_other_magic_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    fake.deals_historicos = [
        SimpleNamespace(magic=Mt5Broker.MAGIC, entry=FakeMt5.DEAL_ENTRY_IN, position_id=1, price=0, profit=0, volume=0.01, time=100),
        SimpleNamespace(magic=999999, entry=FakeMt5.DEAL_ENTRY_OUT, position_id=2, price=0, profit=0, volume=0.01, time=100),
    ]
    broker = connected_broker(monkeypatch, fake, dry_run=True)

    assert broker.get_closed_trades_since("XAUUSD!", datetime(2026, 1, 1, tzinfo=timezone.utc)) == []


def test_get_closed_trades_since_returns_all_closing_deals_in_ascending_order(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    fake.deals_historicos = [
        SimpleNamespace(
            magic=Mt5Broker.MAGIC, entry=FakeMt5.DEAL_ENTRY_OUT, position_id=111, price=1990.0, profit=-30.0, volume=0.15, time=200
        ),
        SimpleNamespace(
            magic=Mt5Broker.MAGIC, entry=FakeMt5.DEAL_ENTRY_OUT, position_id=222, price=2010.0, profit=50.0, volume=0.15, time=100
        ),
    ]
    broker = connected_broker(monkeypatch, fake, dry_run=True)

    cierres = broker.get_closed_trades_since("XAUUSD!", datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert [c["ticket_posicion"] for c in cierres] == [222, 111]  # ordenados por tiempo ascendente
    assert cierres[1]["profit"] == -30.0
    assert cierres[0]["precio_cierre"] == 2010.0


def test_get_open_positions_returns_empty_list_without_open_positions(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    broker = connected_broker(monkeypatch, fake, dry_run=True)

    assert broker.get_open_positions("XAUUSD!") == []


def test_get_open_positions_returns_details_of_each_own_position(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    fake.positiciones = [
        SimpleNamespace(
            symbol="XAUUSD!", magic=Mt5Broker.MAGIC, ticket=99, price_open=2000.0, sl=1990.0, tp=2020.0,
            price_current=2005.0, volume=0.1, type=FakeMt5.ORDER_TYPE_SELL,
        ),
        SimpleNamespace(
            symbol="XAUUSD!", magic=Mt5Broker.MAGIC, ticket=100, price_open=2050.0, sl=2060.0, tp=2000.0,
            price_current=2040.0, volume=0.05, type=FakeMt5.ORDER_TYPE_SELL,
        ),
        SimpleNamespace(
            symbol="XAUUSD!", magic=999999, ticket=101, price_open=2000.0, sl=0.0, tp=0.0,
            price_current=2000.0, volume=0.01, type=FakeMt5.ORDER_TYPE_BUY,
        ),
    ]
    broker = connected_broker(monkeypatch, fake, dry_run=True)

    posiciones = broker.get_open_positions("XAUUSD!")

    assert len(posiciones) == 2  # la de otro magic number no cuenta
    assert {p["ticket"] for p in posiciones} == {99, 100}
    primera = next(p for p in posiciones if p["ticket"] == 99)
    assert primera["direccion"] == SignalDirection.SHORT
    assert primera["sl"] == 1990.0


def test_update_stop_loss_in_dry_run_never_calls_order_send(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    broker = connected_broker(monkeypatch, fake, dry_run=True)

    exito = broker.update_stop_loss("XAUUSD!", ticket=99, nuevo_sl=2000.0, tp_actual=2020.0)

    assert exito is True
    assert fake.ultima_request is None


def test_update_stop_loss_live_sends_sltp_request(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    broker = connected_broker(monkeypatch, fake, dry_run=False)

    exito = broker.update_stop_loss("XAUUSD!", ticket=99, nuevo_sl=2000.0, tp_actual=2020.0)

    assert exito is True
    assert fake.ultima_request["action"] == FakeMt5.TRADE_ACTION_SLTP
    assert fake.ultima_request["position"] == 99
    assert fake.ultima_request["sl"] == 2000.0
    assert fake.ultima_request["tp"] == 2020.0


def test_update_stop_loss_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    fake.orden_a_devolver = SimpleNamespace(retcode=10004, order=None, price=None, comment="requote")
    broker = connected_broker(monkeypatch, fake, dry_run=False)

    exito = broker.update_stop_loss("XAUUSD!", ticket=99, nuevo_sl=2000.0, tp_actual=2020.0)

    assert exito is False


def test_close_position_not_found_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    broker = connected_broker(monkeypatch, fake, dry_run=True)

    resultado = broker.close_position("XAUUSD!", ticket=999)

    assert resultado.enviada is False
    assert "no se encontró" in resultado.motivo_rechazo.lower()


def test_close_position_dry_run_never_calls_order_send(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    fake.positiciones = [SimpleNamespace(symbol="XAUUSD!", ticket=7, type=FakeMt5.ORDER_TYPE_SELL, volume=0.15, sl=2010.0, tp=1990.0)]
    broker = connected_broker(monkeypatch, fake, dry_run=True)

    resultado = broker.close_position("XAUUSD!", ticket=7)

    assert resultado.enviada is True
    assert resultado.dry_run is True
    assert fake.ultima_request is None


def test_close_position_live_closes_a_short_with_a_buy_deal(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    fake.positiciones = [SimpleNamespace(symbol="XAUUSD!", ticket=7, type=FakeMt5.ORDER_TYPE_SELL, volume=0.15, sl=2010.0, tp=1990.0)]
    broker = connected_broker(monkeypatch, fake, dry_run=False)

    resultado = broker.close_position("XAUUSD!", ticket=7)

    assert resultado.enviada is True
    assert fake.ultima_request["type"] == FakeMt5.ORDER_TYPE_BUY  # opuesto a la posición SHORT
    assert fake.ultima_request["position"] == 7
    assert fake.ultima_request["volume"] == 0.15


def test_close_position_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    fake.positiciones = [SimpleNamespace(symbol="XAUUSD!", ticket=7, type=FakeMt5.ORDER_TYPE_BUY, volume=0.1, sl=1990.0, tp=2020.0)]
    fake.orden_a_devolver = SimpleNamespace(retcode=10004, order=None, price=None, comment="requote")
    broker = connected_broker(monkeypatch, fake, dry_run=False)

    resultado = broker.close_position("XAUUSD!", ticket=7)

    assert resultado.enviada is False
    assert "10004" in resultado.motivo_rechazo
