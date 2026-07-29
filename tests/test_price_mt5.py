from __future__ import annotations

import pytest

import bot_xauusd.ingestion.price_mt5 as price_mt5
from bot_xauusd.config import Settings
from bot_xauusd.ingestion.price_mt5 import Mt5ConnectionError, Mt5PriceClient


class FakeMt5:
    TIMEFRAME_M15 = 15
    TIMEFRAME_H1 = 60
    TIMEFRAME_H4 = 240

    def __init__(self) -> None:
        self.initialized = False
        self.rates_to_return: list[dict] | None = None
        self.init_should_succeed = True

    def initialize(self, **kwargs: object) -> bool:
        self.initialized = self.init_should_succeed
        return self.init_should_succeed

    def shutdown(self) -> None:
        self.initialized = False

    def last_error(self) -> tuple[int, str]:
        return (1, "fake error")

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int):
        return self.rates_to_return


def make_settings(**overrides: object) -> Settings:
    base = dict(
        fred_api_key=None,
        mt5_login=1,
        mt5_password="pw",
        mt5_server="srv",
        mt5_terminal_path=None,
    )
    base.update(overrides)
    return Settings(**base)


def test_connect_and_get_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    fake.rates_to_return = [
        {"time": 1700000000, "open": 1900.0, "high": 1905.0, "low": 1898.0, "close": 1902.0, "tick_volume": 120},
    ]
    monkeypatch.setattr(price_mt5, "mt5", fake)

    client = Mt5PriceClient(make_settings())
    with client:
        bars = client.get_bars("XAUUSD", "H1", 1)

    assert fake.initialized is False  # shutdown() se llamó al salir del 'with'
    assert len(bars) == 1
    bar = bars[0]
    assert bar.symbol == "XAUUSD"
    assert bar.timeframe == "H1"
    assert bar.close == 1902.0
    assert bar.time.tzinfo is not None


def test_get_bars_without_connect_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(price_mt5, "mt5", FakeMt5())
    client = Mt5PriceClient(make_settings())

    with pytest.raises(Mt5ConnectionError):
        client.get_bars()


def test_connect_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    fake.init_should_succeed = False
    monkeypatch.setattr(price_mt5, "mt5", fake)
    client = Mt5PriceClient(make_settings())

    with pytest.raises(Mt5ConnectionError):
        client.connect()


def test_unsupported_timeframe_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMt5()
    monkeypatch.setattr(price_mt5, "mt5", fake)
    client = Mt5PriceClient(make_settings())
    client.connect()

    with pytest.raises(ValueError):
        client.get_bars("XAUUSD", "M1")


def test_missing_mt5_package_raises_on_init(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(price_mt5, "mt5", None)

    with pytest.raises(Mt5ConnectionError):
        Mt5PriceClient(make_settings())
