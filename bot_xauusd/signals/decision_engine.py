from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..models import MacroEvent, PriceBar, SignalDirection
from .indicators import TrendDirection
from .macro_rules import MacroRuleEngine, MacroScoreResult
from .technical_trend import TechnicalScoreResult, TechnicalTrendEngine


@dataclass(frozen=True)
class DecisionConfig:
    """
    Pesos y umbrales del motor de decisión (spec_bot_xauusd.md, 4.4). Son un
    punto de partida razonable, no valores calibrados — "deben ser parámetros
    configurables y sujetos a backtesting, no valores fijos mágicos" (spec 4.4).
    """

    peso_macro: float = 0.5
    peso_tecnico: float = 0.5
    umbral_compra: float = 0.5
    umbral_venta: float = -0.5
    redistribuir_peso_si_macro_vacio: bool = False
    """
    Si es True: en los ciclos donde ninguna regla macro encontró un evento
    relevante (`macro.resultados` vacío — no es lo mismo que "score 0 con
    reglas activas que se cancelan entre sí"), el peso de `peso_macro` se
    redistribuye por completo a `peso_tecnico` SOLO para ese ciclo. Sin esto,
    con los pesos por defecto (0.5/0.5) el técnico nunca puede superar
    estrictamente el umbral por sí solo — se queda empatado exactamente en
    él — así que en la práctica el bot nunca operaría en los (frecuentes)
    ciclos sin un evento macro activo. Por defecto False para no cambiar el
    comportamiento ya probado; el bot en vivo lo activa explícitamente.
    """


@dataclass(frozen=True)
class Signal:
    direccion: SignalDirection
    score_final: float
    macro: MacroScoreResult
    tecnico: TechnicalScoreResult
    razones: list[str]


class DecisionEngine:
    """
    Motor de decisión (spec_bot_xauusd.md, 4.4):
        señal_final = peso_macro * score_macro + peso_tecnico * score_tecnico

    Garantía no negociable de la sección 4.8: la dirección permitida la fija
    ÚNICAMENTE la tendencia H4 del motor técnico. El bot nunca genera una señal
    contraria a ese filtro — si el score combinado apuntara al lado contrario
    de H4, el resultado es "no operar", nunca invertir la dirección. Este
    motor aplica esa garantía de forma independiente al `TechnicalTrendEngine`
    (que ya neutraliza su propio score cuando H1 no confirma a H4), como
    defensa en profundidad ante cualquier combinación de scores.
    """

    def __init__(
        self,
        macro_engine: MacroRuleEngine | None = None,
        technical_engine: TechnicalTrendEngine | None = None,
        config: DecisionConfig | None = None,
    ) -> None:
        self._macro_engine = macro_engine or MacroRuleEngine()
        self._technical_engine = technical_engine or TechnicalTrendEngine()
        self._config = config or DecisionConfig()

    def evaluate(
        self,
        macro_events: Sequence[MacroEvent],
        h4_bars: Sequence[PriceBar],
        h1_bars: Sequence[PriceBar],
    ) -> Signal:
        config = self._config
        macro = self._macro_engine.evaluate(macro_events)
        tecnico = self._technical_engine.evaluate(h4_bars, h1_bars)

        peso_macro, peso_tecnico = config.peso_macro, config.peso_tecnico
        redistribuido = config.redistribuir_peso_si_macro_vacio and not macro.resultados
        if redistribuido:
            peso_tecnico = peso_macro + peso_tecnico
            peso_macro = 0.0

        score_final = peso_macro * macro.score + peso_tecnico * tecnico.score
        razones = [
            f"Score macro={macro.score:+.2f} (peso {peso_macro}), "
            f"score técnico={tecnico.score:+.2f} (peso {peso_tecnico}) => señal_final={score_final:+.2f}",
            *(["Sin eventos macro relevantes este ciclo — peso redistribuido 100% al técnico."] if redistribuido else []),
            *[f"[macro] {r.regla}: {r.razon} (score={r.score:+.2f}, peso={r.peso})" for r in macro.resultados],
            *tecnico.razones,
        ]

        if tecnico.tendencia_h4 == TrendDirection.ALCISTA:
            if score_final > config.umbral_compra:
                direccion = SignalDirection.LONG
            else:
                direccion = SignalDirection.NONE
                razones.append(
                    f"H4 alcista permite solo LONG, pero señal_final ({score_final:+.2f}) no supera "
                    f"el umbral de compra ({config.umbral_compra})."
                )
        elif tecnico.tendencia_h4 == TrendDirection.BAJISTA:
            if score_final < config.umbral_venta:
                direccion = SignalDirection.SHORT
            else:
                direccion = SignalDirection.NONE
                razones.append(
                    f"H4 bajista permite solo SHORT, pero señal_final ({score_final:+.2f}) no supera "
                    f"el umbral de venta ({config.umbral_venta})."
                )
        else:
            direccion = SignalDirection.NONE
            razones.append("H4 neutral — sin dirección permitida, no se opera.")

        return Signal(direccion=direccion, score_final=score_final, macro=macro, tecnico=tecnico, razones=razones)
