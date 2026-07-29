from __future__ import annotations

from datetime import datetime, timezone

from ..config import Settings
from ..models import PriceBar

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - solo ocurre fuera de Windows o sin el paquete instalado
    mt5 = None

# Nombres de las constantes TIMEFRAME_* que expone el paquete MetaTrader5.
_TIMEFRAME_ATTRS = {
    "M15": "TIMEFRAME_M15",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
}


class Mt5ConnectionError(RuntimeError):
    """No se pudo inicializar/usar la terminal MT5."""


class Mt5PriceClient:
    """
    Cliente de ingesta de precio vía MetaTrader5, agnóstico de broker
    (spec_bot_xauusd.md, 4.6): se conecta a la terminal MT5 ya instalada con las
    credenciales (login/password/server) del broker del usuario.
    """

    def __init__(self, settings: Settings) -> None:
        if mt5 is None:
            raise Mt5ConnectionError(
                "El paquete MetaTrader5 no está disponible. Instálalo con "
                "'pip install MetaTrader5' (requiere Windows y la terminal MT5 instalada)."
            )
        self._settings = settings
        self._connected = False

    def connect(self) -> None:
        kwargs: dict[str, object] = {}
        if self._settings.mt5_terminal_path:
            kwargs["path"] = self._settings.mt5_terminal_path
        if self._settings.mt5_login:
            kwargs.update(
                login=self._settings.mt5_login,
                password=self._settings.mt5_password,
                server=self._settings.mt5_server,
            )
        if not mt5.initialize(**kwargs):
            raise Mt5ConnectionError(f"No se pudo inicializar MT5: {mt5.last_error()}")
        self._connected = True

    def shutdown(self) -> None:
        if self._connected:
            mt5.shutdown()
            self._connected = False

    def __enter__(self) -> "Mt5PriceClient":
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.shutdown()

    def get_bars(self, symbol: str = "XAUUSD", timeframe: str = "H1", count: int = 200) -> list[PriceBar]:
        if not self._connected:
            raise Mt5ConnectionError("Debes llamar a connect() (o usar 'with') antes de pedir datos.")

        attr = _TIMEFRAME_ATTRS.get(timeframe)
        tf_const = getattr(mt5, attr, None) if attr else None
        if tf_const is None:
            raise ValueError(f"Timeframe no soportado: '{timeframe}' (usa {list(_TIMEFRAME_ATTRS)})")

        rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, count)
        if rates is None:
            raise Mt5ConnectionError(f"No se pudieron obtener velas de {symbol}: {mt5.last_error()}")

        return [
            PriceBar(
                symbol=symbol,
                timeframe=timeframe,
                time=datetime.fromtimestamp(rate["time"], tz=timezone.utc),
                open=float(rate["open"]),
                high=float(rate["high"]),
                low=float(rate["low"]),
                close=float(rate["close"]),
                volume=float(rate["tick_volume"]),
            )
            for rate in rates
        ]
