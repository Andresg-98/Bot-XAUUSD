from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from ..config import Settings
from ..models import SignalDirection

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - solo ocurre fuera de Windows o sin el paquete instalado
    mt5 = None


class Mt5ExecutionError(RuntimeError):
    """No se pudo inicializar/usar la terminal MT5 para ejecución."""


@dataclass(frozen=True)
class OrderResult:
    enviada: bool
    ticket: int | None
    volumen: float | None
    precio: float | None
    sl: float | None
    tp: float | None
    motivo_rechazo: str | None
    dry_run: bool


class Mt5Broker:
    """
    Capa de ejecución vía MT5 (spec_bot_xauusd.md, 4.6), completamente separada
    del motor de decisión — puede correr en modo simulado sin tocar ninguna
    cuenta real, exactamente como exige esa sección.

    `dry_run=True` (por defecto): calcula precio/volumen/SL/TP pero NUNCA llama
    a `mt5.order_send`. Poner `dry_run=False` es una decisión explícita del
    llamador, nunca el comportamiento por defecto.
    """

    MAGIC = 20260728

    def __init__(self, settings: Settings, dry_run: bool = True) -> None:
        if mt5 is None:
            raise Mt5ExecutionError(
                "El paquete MetaTrader5 no está disponible. Instálalo con 'pip install MetaTrader5'."
            )
        self._settings = settings
        self.dry_run = dry_run
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
            raise Mt5ExecutionError(f"No se pudo inicializar MT5: {mt5.last_error()}")
        self._connected = True

    def shutdown(self) -> None:
        if self._connected:
            mt5.shutdown()
            self._connected = False

    def __enter__(self) -> "Mt5Broker":
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.shutdown()

    def get_account_equity(self) -> float:
        info = mt5.account_info()
        if info is None:
            raise Mt5ExecutionError(f"No se pudo leer la cuenta: {mt5.last_error()}")
        return float(info.equity)

    def get_open_positions_count(self, symbol: str) -> int:
        posiciones = mt5.positions_get(symbol=symbol)
        if not posiciones:
            return 0
        return len([p for p in posiciones if p.magic == self.MAGIC])

    def get_open_position(self, symbol: str) -> dict | None:
        """Detalle de nuestra posición abierta (si hay una) — para trailing stop."""
        posiciones = mt5.positions_get(symbol=symbol)
        propias = [p for p in (posiciones or []) if p.magic == self.MAGIC]
        if not propias:
            return None
        p = propias[0]
        return {
            "ticket": p.ticket,
            "entrada": p.price_open,
            "sl": p.sl,
            "tp": p.tp,
            "precio_actual": p.price_current,
            "volumen": p.volume,
            "direccion": SignalDirection.LONG if p.type == mt5.ORDER_TYPE_BUY else SignalDirection.SHORT,
        }

    def update_stop_loss(self, symbol: str, ticket: int, nuevo_sl: float, tp_actual: float) -> bool:
        """Mueve el SL de una posición abierta (spec 4.6: capa de ejecución
        separada del motor de decisión). En modo dry_run no toca MT5."""
        if self.dry_run:
            return True
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": ticket,
            "sl": nuevo_sl,
            "tp": tp_actual,
        }
        resultado = mt5.order_send(request)
        return resultado is not None and resultado.retcode == mt5.TRADE_RETCODE_DONE

    def get_last_closed_trade(self, symbol: str, desde: datetime) -> dict | None:
        """Último cierre (deal DEAL_ENTRY_OUT) de nuestras propias operaciones
        (mismo `MAGIC`) desde `desde`. None si no hay ninguno en ese rango."""
        deals = mt5.history_deals_get(desde, datetime.now(timezone.utc), group=f"*{symbol}*")
        if not deals:
            return None
        cierres = [d for d in deals if d.magic == self.MAGIC and d.entry == mt5.DEAL_ENTRY_OUT]
        if not cierres:
            return None
        ultimo = max(cierres, key=lambda d: d.time)
        return {
            "ticket_posicion": ultimo.position_id,
            "precio_cierre": ultimo.price,
            "profit": ultimo.profit,
            "volumen": ultimo.volume,
            "momento": datetime.fromtimestamp(ultimo.time, tz=timezone.utc),
        }

    # SYMBOL_FILLING_FOK/IOC (bits del campo symbol_info().filling_mode) son
    # constantes fijas del protocolo MT5, pero el paquete MetaTrader5 de Python
    # no las expone con nombre (solo las ORDER_FILLING_* de las órdenes).
    _SYMBOL_FILLING_FOK = 1
    _SYMBOL_FILLING_IOC = 2

    @classmethod
    def _resolver_filling(cls, info: object) -> int:
        """El modo de ejecución soportado varía por broker/símbolo — usarlo fijo
        (p. ej. siempre IOC) puede hacer que el broker rechace toda orden."""
        modo = getattr(info, "filling_mode", 0) or 0
        if modo & cls._SYMBOL_FILLING_IOC:
            return mt5.ORDER_FILLING_IOC
        if modo & cls._SYMBOL_FILLING_FOK:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    @staticmethod
    def _redondear_a_paso(lotes_deseados: float, info: object) -> tuple[float | None, str | None]:
        paso = info.volume_step or 0.01
        lotes = round(math.floor(lotes_deseados / paso) * paso, 2)
        if lotes < info.volume_min:
            return None, (
                f"El tamaño de posición ({lotes_deseados:.4f} lotes) redondea por debajo del mínimo del "
                f"broker ({info.volume_min})."
            )
        if info.volume_max and lotes > info.volume_max:
            lotes = info.volume_max
        return lotes, None

    def _resolver_volumen_por_riesgo(self, info: object, tamano_unidades: float) -> tuple[float | None, str | None]:
        contrato = info.trade_contract_size or 1.0
        lotes_deseados = tamano_unidades / contrato
        lotes, motivo = self._redondear_a_paso(lotes_deseados, info)
        if lotes is None:
            # Spec 4.5: rechazar la operación en vez de forzar un tamaño que viole
            # el % de riesgo configurado — nunca redondear hacia arriba para "que entre".
            return None, motivo + " Se rechaza en vez de operar con más riesgo del configurado (spec 4.5)."
        return lotes, None

    def _resolver_volumen_fijo(self, info: object, lotes_solicitados: float) -> tuple[float | None, str | None]:
        lotes, motivo = self._redondear_a_paso(lotes_solicitados, info)
        if lotes is None:
            return None, motivo + " Ajusta MT5_LOTE_FIJO en tu .env."
        return lotes, None

    def place_market_order(
        self,
        symbol: str,
        direccion: SignalDirection,
        sl: float,
        tp: float,
        *,
        tamano_unidades: float | None = None,
        lotes: float | None = None,
        comentario: str = "bot_xauusd",
    ) -> OrderResult:
        """
        El tamaño de la posición se especifica de una de dos formas,
        mutuamente excluyentes:
        - `tamano_unidades`: tamaño calculado por riesgo % + distancia de SL
          (comportamiento por defecto — spec 4.5).
        - `lotes`: tamaño de lote FIJO, decidido manualmente por el usuario
          (`MT5_LOTE_FIJO` en `.env`), que reemplaza el cálculo por riesgo.
          Nota: el SL sigue siendo por ATR, así que con lote fijo el riesgo
          en $ de cada operación varía con la volatilidad — deja de ser
          constante como con el sizing automático.
        """
        if not self._connected:
            raise Mt5ExecutionError("Debes llamar a connect() (o usar 'with') antes de operar.")
        if direccion not in (SignalDirection.LONG, SignalDirection.SHORT):
            raise ValueError("direccion debe ser LONG o SHORT.")
        if (tamano_unidades is None) == (lotes is None):
            raise ValueError("Debes especificar exactamente uno: tamano_unidades o lotes.")

        if not mt5.symbol_select(symbol, True):
            return OrderResult(False, None, None, None, None, None, f"No se pudo seleccionar '{symbol}'.", self.dry_run)

        info = mt5.symbol_info(symbol)
        if info is None:
            return OrderResult(False, None, None, None, None, None, f"Símbolo '{symbol}' no encontrado en el broker.", self.dry_run)

        if lotes is not None:
            lotes_resueltos, motivo_rechazo = self._resolver_volumen_fijo(info, lotes)
        else:
            lotes_resueltos, motivo_rechazo = self._resolver_volumen_por_riesgo(info, tamano_unidades)
        lotes = lotes_resueltos
        if lotes is None:
            return OrderResult(False, None, None, None, None, None, motivo_rechazo, self.dry_run)

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return OrderResult(False, None, lotes, None, sl, tp, "No se pudo leer el precio actual.", self.dry_run)
        precio = tick.ask if direccion == SignalDirection.LONG else tick.bid

        if self.dry_run:
            return OrderResult(True, None, lotes, precio, sl, tp, None, True)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lotes,
            "type": mt5.ORDER_TYPE_BUY if direccion == SignalDirection.LONG else mt5.ORDER_TYPE_SELL,
            "price": precio,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": self.MAGIC,
            "comment": comentario,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._resolver_filling(info),
        }
        resultado = mt5.order_send(request)
        if resultado is None or resultado.retcode != mt5.TRADE_RETCODE_DONE:
            motivo = f"retcode={getattr(resultado, 'retcode', None)} comment={getattr(resultado, 'comment', mt5.last_error())}"
            return OrderResult(False, None, lotes, precio, sl, tp, motivo, False)

        return OrderResult(True, resultado.order, lotes, resultado.price, sl, tp, None, False)
