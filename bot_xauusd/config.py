from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    fred_api_key: str | None
    twelvedata_api_key: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None
    mt5_terminal_path: str | None
    mt5_symbol: str


def load_settings() -> Settings:
    login = os.getenv("MT5_LOGIN")
    return Settings(
        fred_api_key=os.getenv("FRED_API_KEY") or None,
        twelvedata_api_key=os.getenv("TWELVEDATA_API_KEY") or None,
        mt5_login=int(login) if login else None,
        mt5_password=os.getenv("MT5_PASSWORD") or None,
        mt5_server=os.getenv("MT5_SERVER") or None,
        mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
        # El nombre exacto del símbolo de oro varía por broker (ej. "XAUUSD",
        # "XAUUSD!", "XAUUSD.micro!", "GOLD"). Configúralo en .env si el tuyo
        # no es el estándar "XAUUSD" (ver scripts/check_price_ingestion.py).
        mt5_symbol=os.getenv("MT5_SYMBOL") or "XAUUSD",
    )
