"""Búsqueda de parámetros para la Fase 3 (spec 6: 'iterar parámetros ANTES de
tocar dinero real'). Descarga el histórico UNA sola vez y corre el backtest
sobre una grilla de RiskConfig/TechnicalConfig, reportando las combinaciones
más cercanas a los objetivos de la spec (profit factor >=1.4, drawdown<=16%,
0-3 operaciones/semana)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot_xauusd.backtest.engine import BacktestEngine  # noqa: E402
from bot_xauusd.backtest.metrics import compute_metrics  # noqa: E402
from bot_xauusd.backtest.risk import RiskConfig  # noqa: E402
from bot_xauusd.config import load_settings  # noqa: E402
from bot_xauusd.ingestion.price_twelvedata import TwelveDataApiError, TwelveDataPriceClient  # noqa: E402
from bot_xauusd.signals.decision_engine import DecisionConfig, DecisionEngine  # noqa: E402
from bot_xauusd.signals.technical_trend import TechnicalConfig, TechnicalTrendEngine  # noqa: E402

AÑOS_DE_HISTORIAL = 3


def main() -> None:
    settings = load_settings()
    if not settings.twelvedata_api_key:
        print("Falta TWELVEDATA_API_KEY.")
        return

    cliente = TwelveDataPriceClient(api_key=settings.twelvedata_api_key)
    fin = datetime.now(timezone.utc)
    inicio = fin - timedelta(days=365 * AÑOS_DE_HISTORIAL)
    print("Descargando histórico (una sola vez)...")
    try:
        h4_bars = cliente.get_historical_bars("H4", inicio, fin)
        h1_bars = cliente.get_historical_bars("H1", inicio, fin)
    except TwelveDataApiError as exc:
        print(f"Error consultando TwelveData: {exc}")
        return
    print(f"H4: {len(h4_bars)} velas | H1: {len(h1_bars)} velas\n")

    semanas = max((h1_bars[-1].time - h1_bars[0].time).days / 7, 1)

    config_decision = DecisionConfig(peso_macro=0.0, peso_tecnico=1.0, umbral_compra=0.5, umbral_venta=-0.5)

    filas = []
    for atr_mult in (1.5, 2.0, 2.5, 3.0):
        for rr in (2.0, 2.5, 3.0):
            for rsi_ob, rsi_os in ((70.0, 30.0), (65.0, 35.0), (60.0, 40.0)):
                risk = RiskConfig(atr_multiplo_sl=atr_mult, relacion_riesgo_beneficio=rr)
                tecnico = TechnicalTrendEngine(TechnicalConfig(rsi_sobrecompra=rsi_ob, rsi_sobreventa=rsi_os))
                motor = DecisionEngine(technical_engine=tecnico, config=config_decision)
                resultado = BacktestEngine(decision_engine=motor, risk_config=risk).run(h4_bars, h1_bars)
                metricas = compute_metrics(resultado)
                por_semana = metricas.numero_operaciones / semanas
                filas.append(
                    {
                        "atr_mult": atr_mult,
                        "rr": rr,
                        "rsi": f"{rsi_ob:.0f}/{rsi_os:.0f}",
                        "n": metricas.numero_operaciones,
                        "por_semana": por_semana,
                        "win_rate": metricas.win_rate,
                        "pf": metricas.profit_factor,
                        "dd": metricas.max_drawdown,
                        "halted": resultado.halted_permanently,
                        "capital_final": resultado.capital_final,
                    }
                )

    def pf_valor(fila: dict) -> float:
        pf = fila["pf"]
        return pf if pf is not None and pf != float("inf") else (999 if pf == float("inf") else -1)

    filas_validas = [f for f in filas if f["n"] >= 5]  # descarta combinaciones con muestra insignificante
    filas_validas.sort(key=pf_valor, reverse=True)

    print(f"{'ATR×':>5} {'RR':>4} {'RSI':>7} {'N':>4} {'/sem':>5} {'win%':>6} {'PF':>6} {'DD%':>6} {'halt':>5} capital_final")
    for f in filas_validas[:15]:
        pf_str = "inf" if f["pf"] == float("inf") else (f"{f['pf']:.2f}" if f["pf"] is not None else "N/A")
        print(
            f"{f['atr_mult']:>5.1f} {f['rr']:>4.1f} {f['rsi']:>7} {f['n']:>4} {f['por_semana']:>5.2f} "
            f"{f['win_rate']*100:>5.1f}% {pf_str:>6} {f['dd']*100:>5.1f}% {'SÍ' if f['halted'] else 'no':>5} "
            f"{f['capital_final']:.0f}"
        )

    print(f"\nTotal combinaciones probadas: {len(filas)} (con >=5 operaciones: {len(filas_validas)})")


if __name__ == "__main__":
    main()
