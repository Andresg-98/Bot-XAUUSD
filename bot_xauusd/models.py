from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ImpactLevel(str, Enum):
    ALTO = "alto"
    MEDIO = "medio"
    BAJO = "bajo"


class SignalDirection(str, Enum):
    LONG = "long"
    SHORT = "short"
    NONE = "none"


@dataclass(frozen=True)
class MacroEvent:
    """Evento macro normalizado, ver 'Output esperado del módulo' en spec_bot_xauusd.md 4.1."""

    evento: str
    fecha: datetime
    pais: str
    impacto: ImpactLevel
    valor_esperado: float | None
    valor_real: float | None
    valor_previo: float | None
    fuente: str = "FRED"


@dataclass(frozen=True)
class PriceBar:
    symbol: str
    timeframe: str
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
