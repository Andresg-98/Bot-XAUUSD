"""Corre el backtest de la Fase 3 contra histórico real de TwelveData (no toca MT5
ni ejecuta ninguna orden). Imprime las métricas objetivo de spec_bot_xauusd.md 4.5/7:
profit factor >= 1.4, drawdown máximo en backtest <= 16%."""
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

AÑOS_DE_HISTORIAL_DEFECTO = 3
PROFIT_FACTOR_OBJETIVO = 1.4
DRAWDOWN_MAXIMO_OBJETIVO = 0.16  # spec 4.5: el doble del 8% real permitido


def main() -> None:
    años = float(sys.argv[1]) if len(sys.argv) > 1 else AÑOS_DE_HISTORIAL_DEFECTO

    settings = load_settings()
    if not settings.twelvedata_api_key:
        print("Falta TWELVEDATA_API_KEY. Copia .env.example a .env y completa tu API key gratuita de TwelveData.")
        return

    cliente = TwelveDataPriceClient(api_key=settings.twelvedata_api_key)
    fin = datetime.now(timezone.utc)
    inicio = fin - timedelta(days=365 * años)

    print(f"Descargando histórico H4/H1 de XAU/USD desde {inicio.date()} hasta {fin.date()} (TwelveData)...")
    try:
        h4_bars = cliente.get_historical_bars("H4", inicio, fin)
        h1_bars = cliente.get_historical_bars("H1", inicio, fin)
    except TwelveDataApiError as exc:
        print(f"Error consultando TwelveData: {exc}")
        return

    print(f"H4: {len(h4_bars)} velas | H1: {len(h1_bars)} velas")
    if not h4_bars or not h1_bars:
        print("No hay suficientes datos para correr el backtest.")
        return

    años_reales = (h1_bars[-1].time - h1_bars[0].time).days / 365.25
    if años_reales < 2.0:
        print(
            f"AVISO: solo hay {años_reales:.1f} años de histórico disponibles (spec pide mínimo 2-3). "
            "Los resultados no son concluyentes."
        )

    # El score macro está fijo en 0 durante todo este backtest (ver README: no hay
    # calendario histórico disponible). Con los pesos por defecto (0.5/0.5) el score
    # técnico solo podría llegar a 0.5 como máximo, empatando el umbral por defecto
    # (0.5) sin superarlo nunca — la comparación es estricta (spec 4.4). Por eso, y
    # SOLO para este backtest "solo técnico", el peso completo va al motor técnico;
    # la config por defecto (para cuando sí haya macro real) no se toca.
    config_solo_tecnico = DecisionConfig(peso_macro=0.0, peso_tecnico=1.0, umbral_compra=0.5, umbral_venta=-0.5)
    motor_decision = DecisionEngine(config=config_solo_tecnico)
    resultado = BacktestEngine(decision_engine=motor_decision, risk_config=RiskConfig()).run(h4_bars, h1_bars)
    metricas = compute_metrics(resultado)

    semanas_totales = max((h1_bars[-1].time - h1_bars[0].time).days / 7, 1)
    operaciones_por_semana = metricas.numero_operaciones / semanas_totales

    print("\n--- Resultado del backtest (solo técnico; score macro=0, ver README) ---")
    print(f"Operaciones totales: {metricas.numero_operaciones} ({operaciones_por_semana:.2f}/semana sobre todo el período)")
    if resultado.trades:
        dias_activo = max((resultado.trades[-1].apertura - resultado.trades[0].apertura).days, 1)
        semanas_activo = dias_activo / 7
        por_semana_activa = metricas.numero_operaciones / semanas_activo
        print(
            f"  Ventana realmente activa: {resultado.trades[0].apertura.date()} -> "
            f"{resultado.trades[-1].apertura.date()} ({por_semana_activa:.2f}/semana en esa ventana — "
            "más representativo si el kill switch se activó antes de terminar el período)"
        )
    print(f"Win rate: {metricas.win_rate:.1%}")
    pf = metricas.profit_factor
    print(f"Profit factor: {'inf' if pf == float('inf') else (f'{pf:.2f}' if pf is not None else 'N/A')}")
    print(f"Máximo drawdown: {metricas.max_drawdown:.1%}")
    print(f"Sharpe ratio (por operación, simplificado): {metricas.sharpe_ratio}")
    print(f"Capital inicial: {resultado.capital_inicial:.2f} -> final: {resultado.capital_final:.2f}")
    if resultado.halted_permanently:
        print(
            f"⚠ Kill switch de drawdown total se activó en: {resultado.halted_en} — el bot no habría operado "
            "más después de esa fecha dentro de este período."
        )

    print("\n--- Contra los objetivos de la spec (4.5 y 7) ---")
    pf_ok = pf is not None and pf >= PROFIT_FACTOR_OBJETIVO
    dd_ok = metricas.max_drawdown <= DRAWDOWN_MAXIMO_OBJETIVO
    print(f"Profit factor >= {PROFIT_FACTOR_OBJETIVO}: {'OK' if pf_ok else 'NO CUMPLE'}")
    print(f"Drawdown máximo <= {DRAWDOWN_MAXIMO_OBJETIVO:.0%}: {'OK' if dd_ok else 'NO CUMPLE'}")
    if not (0 <= operaciones_por_semana <= 3):
        print(
            f"AVISO (spec 4.8): {operaciones_por_semana:.2f} operaciones/semana (promedio sobre todo el período) "
            "está fuera del rango esperado (0-3) — revisa la ventana activa arriba antes de concluir nada."
        )


if __name__ == "__main__":
    main()
