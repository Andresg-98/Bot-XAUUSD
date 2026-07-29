"""Script manual para verificar el motor de decisión completo con datos reales:
calendario de ForexFactory + velas H4/H1 de MT5. No ejecuta ninguna orden."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot_xauusd.config import load_settings  # noqa: E402
from bot_xauusd.ingestion.macro_forexfactory import ForexFactoryApiError, ForexFactoryClient  # noqa: E402
from bot_xauusd.ingestion.price_mt5 import Mt5ConnectionError, Mt5PriceClient  # noqa: E402
from bot_xauusd.signals.decision_engine import DecisionEngine  # noqa: E402


def main() -> None:
    settings = load_settings()
    if not settings.mt5_login:
        print("Falta MT5_LOGIN/MT5_PASSWORD/MT5_SERVER. Copia .env.example a .env y completa tus credenciales.")
        return

    try:
        eventos = ForexFactoryClient().get_calendar("this_week")
    except ForexFactoryApiError as exc:
        print(f"Error consultando el feed de ForexFactory: {exc}")
        return

    try:
        with Mt5PriceClient(settings) as client:
            h4_bars = client.get_bars("XAUUSD", "H4", count=250)
            h1_bars = client.get_bars("XAUUSD", "H1", count=250)
    except Mt5ConnectionError as exc:
        print(f"Error de conexión MT5: {exc}")
        return

    señal = DecisionEngine().evaluate(eventos, h4_bars, h1_bars)

    print(f"Dirección: {señal.direccion.value.upper()} | señal_final={señal.score_final:+.2f}")
    print(f"Score macro: {señal.macro.score:+.2f} | Score técnico: {señal.tecnico.score:+.2f}")
    print("Razonamiento:")
    for razon in señal.razones:
        print(f"  - {razon}")


if __name__ == "__main__":
    main()
