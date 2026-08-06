"""Reactiva manualmente la pausa semanal del bot (spec 4.5: "requiere revisar
la lógica antes de reactivar, no reinicio automático"). Corre esto SOLO
después de revisar por qué se activó — no es un botón para "seguir operando
sin más". Resetea la base de equity semanal al equity actual."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot_xauusd.config import load_settings  # noqa: E402
from bot_xauusd.execution.mt5_broker import Mt5Broker, Mt5ExecutionError  # noqa: E402
from bot_xauusd.live.state import KillSwitchStateStore, reactivar_pausa_semanal  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    settings = load_settings()
    store = KillSwitchStateStore(REPO_ROOT / "data" / "kill_switch_state.json")

    estado_actual = store.load()
    if estado_actual is None:
        print("No hay estado de kill switch guardado — nada que reactivar.")
        return
    if not estado_actual.halted_semanalmente:
        print("La pausa semanal no está activa ahora mismo — no hace falta hacer nada.")
        return

    print(f"Pausa semanal activada en: {estado_actual.halted_semanalmente_en}")
    print(f"Equity al inicio de esa semana: {estado_actual.equity_inicio_semana:.2f}")

    try:
        broker = Mt5Broker(settings, dry_run=True)  # dry_run=True: esto no envía órdenes, solo lee equity
        broker.connect()
        equity_actual = broker.get_account_equity()
        broker.shutdown()
    except Mt5ExecutionError as exc:
        print(f"Error de conexión MT5: {exc}")
        return

    print(f"Equity actual: {equity_actual:.2f}")
    confirmacion = input("¿Confirmas que revisaste la situación y quieres reactivar? (escribe 'si'): ")
    if confirmacion.strip().lower() != "si":
        print("Cancelado — no se reactivó nada.")
        return

    nuevo_estado = reactivar_pausa_semanal(store, datetime.now(timezone.utc), equity_actual)
    print(f"Reactivado. Nueva base de equity semanal: {nuevo_estado.equity_inicio_semana:.2f}")


if __name__ == "__main__":
    main()
