"""Script manual para verificar de forma aislada la ingesta de datos macro (FRED)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot_xauusd.config import load_settings  # noqa: E402
from bot_xauusd.ingestion.macro_fred import FredApiError, FredMacroClient  # noqa: E402


def main() -> None:
    settings = load_settings()
    if not settings.fred_api_key:
        print("Falta FRED_API_KEY. Copia .env.example a .env y completa tu API key de FRED.")
        return

    client = FredMacroClient(settings.fred_api_key)
    try:
        eventos = client.get_latest_events()
    except FredApiError as exc:
        print(f"Error consultando FRED: {exc}")
        return

    for evento in eventos:
        print(
            f"[{evento.impacto.value.upper():5}] {evento.evento} ({evento.pais}) - {evento.fecha.date()} "
            f"| real={evento.valor_real} previo={evento.valor_previo}"
        )


if __name__ == "__main__":
    main()
