from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..signals.decision_engine import Signal


class DecisionLogger:
    """
    Registra cada decisión (se ejecute o no) con su razonamiento completo, en
    formato JSON-lines (spec_bot_xauusd.md, 4.7). Un archivo plano de texto es
    intencional: es lo mínimo necesario para poder auditar 4-8 semanas de
    paper trading sin depender de infraestructura adicional (dashboard/DB
    quedan para una fase futura si hace falta).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log_evaluacion(self, señal: Signal, ejecutada: bool, detalle: str | None = None) -> None:
        self._append(
            {
                "tipo": "evaluacion",
                "direccion": señal.direccion.value,
                "score_final": señal.score_final,
                "score_macro": señal.macro.score,
                "score_tecnico": señal.tecnico.score,
                "ejecutada": ejecutada,
                "detalle": detalle,
                "razones": señal.razones,
            }
        )

    def log_evento(self, tipo: str, detalle: str) -> None:
        self._append({"tipo": tipo, "detalle": detalle})

    def _append(self, registro: dict) -> None:
        registro = {"momento": datetime.now(timezone.utc).isoformat(), **registro}
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
