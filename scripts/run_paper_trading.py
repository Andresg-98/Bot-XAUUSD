"""Fase 4 — Paper trading (spec 6): corre el loop en vivo contra tu cuenta DEMO
de MT5, sin dinero real. Por defecto corre en modo SIMULADO (dry-run): calcula
todo pero nunca envía una orden real. Pasa --live explícitamente para que sí
envíe órdenes reales a tu cuenta MT5 (debe ser una cuenta demo).

Este script no termina solo — corre indefinidamente hasta que lo detengas con
Ctrl+C. Está pensado para dejarlo corriendo en una terminal (ver README)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot_xauusd.backtest.risk import RiskConfig  # noqa: E402
from bot_xauusd.config import load_settings  # noqa: E402
from bot_xauusd.execution.mt5_broker import Mt5Broker, Mt5ExecutionError  # noqa: E402
from bot_xauusd.ingestion.macro_forexfactory import ForexFactoryClient  # noqa: E402
from bot_xauusd.ingestion.macro_fred import FredMacroClient  # noqa: E402
from bot_xauusd.ingestion.news_sentiment import AlphaVantageNewsSentimentClient  # noqa: E402
from bot_xauusd.ingestion.price_mt5 import Mt5ConnectionError, Mt5PriceClient  # noqa: E402
from bot_xauusd.live.decision_log import DecisionLogger  # noqa: E402
from bot_xauusd.live.loop import run_forever  # noqa: E402
from bot_xauusd.live.state import KillSwitchStateStore  # noqa: E402
from bot_xauusd.signals.decision_engine import DecisionConfig, DecisionEngine  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    live = "--live" in sys.argv
    settings = load_settings()

    print(f"Modo: {'*** LIVE (envía órdenes reales a MT5) ***' if live else 'SIMULADO (dry-run, no envía nada)'}")
    print(f"Símbolo: {settings.mt5_symbol}")
    if settings.mt5_lote_fijo is not None:
        print(f"Lotaje: FIJO {settings.mt5_lote_fijo} (MT5_LOTE_FIJO) — reemplaza el sizing automático por riesgo")
    else:
        print("Lotaje: automático por riesgo % + ATR (spec 4.5)")

    try:
        broker = Mt5Broker(settings, dry_run=not live)
        price_client = Mt5PriceClient(settings)
        broker.connect()
        price_client.connect()
    except (Mt5ExecutionError, Mt5ConnectionError) as exc:
        print(f"Error de conexión MT5: {exc}")
        return

    logger = DecisionLogger(REPO_ROOT / "logs" / "decisiones.jsonl")
    state_store = KillSwitchStateStore(REPO_ROOT / "data" / "kill_switch_state.json")
    risk = RiskConfig(trailing_habilitado=True)
    print(
        f"Trailing stop: breakeven a {risk.trailing_activar_en_r}R, "
        f"luego ATR(H1)×{risk.trailing_atr_multiplo} — revisado cada tick de monitoreo"
    )

    fred_client = FredMacroClient(settings.fred_api_key) if settings.fred_api_key else None
    print(
        "Fuente macro FRED (DXY/VIX): activa"
        if fred_client
        else "Fuente macro FRED (DXY/VIX): INACTIVA (falta FRED_API_KEY en .env)"
    )
    sentiment_client = (
        AlphaVantageNewsSentimentClient(settings.alphavantage_api_key) if settings.alphavantage_api_key else None
    )
    print(
        "Refuerzo de sentimiento de noticias: activo"
        if sentiment_client
        else "Refuerzo de sentimiento de noticias: INACTIVO (falta ALPHAVANTAGE_API_KEY en .env)"
    )

    try:
        run_forever(
            symbol=settings.mt5_symbol,
            broker=broker,
            macro_client=ForexFactoryClient(),
            price_client=price_client,
            decision_engine=DecisionEngine(config=DecisionConfig(redistribuir_peso_si_macro_vacio=True)),
            risk=risk,
            logger=logger,
            state_store=state_store,
            lote_fijo=settings.mt5_lote_fijo,
            fred_client=fred_client,
            sentiment_client=sentiment_client,
        )
    except KeyboardInterrupt:
        print("\nDetenido por el usuario (Ctrl+C).")
    finally:
        broker.shutdown()
        price_client.shutdown()


if __name__ == "__main__":
    main()
