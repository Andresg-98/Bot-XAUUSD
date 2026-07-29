"""Script manual para verificar de forma aislada la ingesta de precio (MT5)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot_xauusd.config import load_settings  # noqa: E402
from bot_xauusd.ingestion.price_mt5 import Mt5ConnectionError, Mt5PriceClient  # noqa: E402


def main() -> None:
    settings = load_settings()
    # Si no hay credenciales en .env, mt5.initialize() intenta adjuntarse a una
    # terminal MT5 ya abierta y logueada — no siempre hace falta login/password/server.
    try:
        with Mt5PriceClient(settings) as client:
            for timeframe in ("H4", "H1"):
                barras = client.get_bars(settings.mt5_symbol, timeframe, count=5)
                print(f"--- {timeframe}: últimas {len(barras)} velas ---")
                for bar in barras:
                    print(f"{bar.time} O={bar.open} H={bar.high} L={bar.low} C={bar.close} V={bar.volume}")
    except Mt5ConnectionError as exc:
        print(f"Error de conexión MT5: {exc}")


if __name__ == "__main__":
    main()
