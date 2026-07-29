"""Script manual para verificar de forma aislada el calendario de ForexFactory."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot_xauusd.ingestion.macro_forexfactory import ForexFactoryApiError, ForexFactoryClient  # noqa: E402


def main() -> None:
    client = ForexFactoryClient()
    try:
        eventos = client.get_calendar("this_week")
    except ForexFactoryApiError as exc:
        print(f"Error consultando el feed de ForexFactory: {exc}")
        return

    for evento in sorted(eventos, key=lambda e: e.fecha):
        print(
            f"[{evento.impacto.value.upper():5}] {evento.fecha} {evento.pais:4} {evento.evento} "
            f"| esperado={evento.valor_esperado} real={evento.valor_real} previo={evento.valor_previo}"
        )


if __name__ == "__main__":
    main()
