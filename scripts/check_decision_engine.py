"""Script manual para verificar el motor de decisión completo con datos reales:
calendario de ForexFactory + velas H4/H1 de MT5. No ejecuta ninguna orden."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot_xauusd.config import load_settings  # noqa: E402
from bot_xauusd.ingestion.macro_forexfactory import ForexFactoryApiError, ForexFactoryClient  # noqa: E402
from bot_xauusd.ingestion.price_mt5 import Mt5ConnectionError, Mt5PriceClient  # noqa: E402
from bot_xauusd.signals.decision_engine import DecisionConfig, DecisionEngine  # noqa: E402


def main() -> None:
    settings = load_settings()
    # Si no hay credenciales en .env, mt5.initialize() intenta adjuntarse a una
    # terminal MT5 ya abierta y logueada — no siempre hace falta login/password/server.
    try:
        eventos = ForexFactoryClient().get_calendar("this_week")
    except ForexFactoryApiError as exc:
        print(f"Error consultando el feed de ForexFactory: {exc}")
        return

    try:
        with Mt5PriceClient(settings) as client:
            h4_bars = client.get_bars(settings.mt5_symbol, "H4", count=250)
            h1_bars = client.get_bars(settings.mt5_symbol, "H1", count=250)
    except Mt5ConnectionError as exc:
        print(f"Error de conexión MT5: {exc}")
        return

    # Misma config que usa run_paper_trading.py: si el macro no tiene eventos
    # relevantes activos, su peso se redistribuye al técnico (ver decision_engine.py).
    config = DecisionConfig(redistribuir_peso_si_macro_vacio=True)
    señal = DecisionEngine(config=config).evaluate(eventos, h4_bars, h1_bars)

    print(f"Dirección: {señal.direccion.value.upper()} | señal_final={señal.score_final:+.2f}")
    print(f"Score macro: {señal.macro.score:+.2f} | Score técnico: {señal.tecnico.score:+.2f}")
    print("Razonamiento:")
    for razon in señal.razones:
        print(f"  - {razon}")


if __name__ == "__main__":
    main()
