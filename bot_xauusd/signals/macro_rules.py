from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from ..models import MacroEvent


def _clamp(value: float, minimo: float = -1.0, maximo: float = 1.0) -> float:
    return max(minimo, min(maximo, value))


@dataclass(frozen=True)
class RuleResult:
    regla: str
    score: float
    peso: float
    razon: str


@dataclass(frozen=True)
class MacroScoreResult:
    score: float
    resultados: list[RuleResult]


# Una regla devuelve None si no encuentra ningún evento relevante en la lista
# (no participa en el score combinado), o (score, razón) si sí aplica.
RuleFn = Callable[[Sequence[MacroEvent]], "tuple[float, str] | None"]


@dataclass(frozen=True)
class MacroRule:
    nombre: str
    peso: float
    evaluar: RuleFn


def _matches(evento: MacroEvent, *, contiene: Sequence[str], moneda: str = "USD") -> bool:
    if evento.pais.upper() != moneda:
        return False
    titulo = evento.evento.lower()
    return any(fragmento in titulo for fragmento in contiene)


def _regla_inflacion(eventos: Sequence[MacroEvent], normalizador: float = 0.2) -> "tuple[float, str] | None":
    """CPI/PCE por encima de lo esperado -> USD fuerte -> sesgo bajista en XAUUSD (spec 4.2)."""
    relevantes = [
        e
        for e in eventos
        if _matches(e, contiene=("cpi", "pce")) and e.valor_real is not None and e.valor_esperado is not None
    ]
    if not relevantes:
        return None
    sorpresas = [e.valor_real - e.valor_esperado for e in relevantes]  # type: ignore[operator]
    sorpresa_media = sum(sorpresas) / len(sorpresas)
    score = _clamp(-sorpresa_media / normalizador)
    return score, f"Sorpresa media de inflación (real-esperado)={sorpresa_media:+.2f} sobre {len(relevantes)} evento(s)."


def _regla_empleo(eventos: Sequence[MacroEvent], normalizador: float = 100.0) -> "tuple[float, str] | None":
    """NFP fuerte -> USD fuerte -> sesgo bajista en XAUUSD (spec 4.2). Normalizador en miles (100 = 100K)."""
    relevantes = [
        e
        for e in eventos
        if _matches(e, contiene=("non-farm employment change", "non-farm payrolls", "nonfarm payrolls"))
        and e.valor_real is not None
        and e.valor_esperado is not None
    ]
    if not relevantes:
        return None
    evento = relevantes[0]
    sorpresa_miles = (evento.valor_real - evento.valor_esperado) / 1000.0  # type: ignore[operator]
    score = _clamp(-sorpresa_miles / normalizador)
    return score, f"Sorpresa de NFP (real-esperado)={sorpresa_miles:+.1f}K."


def _regla_tasa_fed(eventos: Sequence[MacroEvent], normalizador: float = 0.25) -> "tuple[float, str] | None":
    """Decisión de tasas más hawkish de lo esperado -> sesgo bajista en XAUUSD (spec 4.2)."""
    relevantes = [
        e
        for e in eventos
        if _matches(e, contiene=("federal funds rate",)) and e.valor_real is not None and e.valor_esperado is not None
    ]
    if not relevantes:
        return None
    evento = relevantes[0]
    sorpresa = evento.valor_real - evento.valor_esperado  # type: ignore[operator]
    score = _clamp(-sorpresa / normalizador)
    return score, f"Sorpresa de tasa Fed (real-esperado)={sorpresa:+.2f} pp."


def _regla_dxy(eventos: Sequence[MacroEvent], normalizador: float = 1.0) -> "tuple[float, str] | None":
    """Índice del dólar como confirmación cruzada: DXY subiendo -> sesgo bajista en XAUUSD (spec 4.2)."""
    relevantes = [
        e
        for e in eventos
        if ("dólar" in e.evento.lower() or "dxy" in e.evento.lower())
        and e.valor_real is not None
        and e.valor_previo is not None
    ]
    if not relevantes:
        return None
    evento = relevantes[0]
    variacion = evento.valor_real - evento.valor_previo  # type: ignore[operator]
    score = _clamp(-variacion / normalizador)
    return score, f"Variación del índice del dólar (real-previo)={variacion:+.2f}."


def _regla_riesgo_vix(eventos: Sequence[MacroEvent], normalizador: float = 5.0) -> "tuple[float, str] | None":
    """
    Aversión al riesgo, proxy cuantitativo (spec 4.2: "eventos de aversión al
    riesgo... -> sesgo alcista en XAUUSD, activo refugio"). El VIX (FRED) sube
    cuando el mercado tiene miedo/pánico -> sesgo alcista en el oro.
    `normalizador`=5.0 puntos de VIX para score máximo (±1) — un movimiento de
    esa magnitud entre observaciones ya es una señal de estrés real.
    """
    relevantes = [
        e for e in eventos if "vix" in e.evento.lower() and e.valor_real is not None and e.valor_previo is not None
    ]
    if not relevantes:
        return None
    evento = relevantes[0]
    variacion = evento.valor_real - evento.valor_previo  # type: ignore[operator]
    score = _clamp(variacion / normalizador)
    return score, f"Variación del VIX (real-previo)={variacion:+.2f} puntos."


def _regla_sentimiento_noticias(eventos: Sequence[MacroEvent], normalizador: float = 1.0) -> "tuple[float, str] | None":
    """
    Refuerzo del proxy de aversión al riesgo: sentimiento agregado de noticias
    de mercados financieros (Alpha Vantage NEWS_SENTIMENT). Sentimiento
    negativo (temor, ventas masivas, crisis) -> aversión al riesgo -> sesgo
    alcista en XAUUSD. El score de Alpha Vantage ya viene aproximadamente en
    -1..+1, así que se invierte directamente (no es sobre el oro, es sobre el
    mercado en general — negativo ahí es positivo para el refugio).
    """
    relevantes = [e for e in eventos if "sentimiento de noticias" in e.evento.lower() and e.valor_real is not None]
    if not relevantes:
        return None
    evento = relevantes[0]
    sentimiento_mercado = evento.valor_real  # type: ignore[assignment]
    score = _clamp(-sentimiento_mercado / normalizador)
    return score, f"Sentimiento agregado de noticias de mercado={sentimiento_mercado:+.2f} (Alpha Vantage)."


# Pesos configurables (spec 4.2: "cada regla debe tener un peso configurable").
# Son un punto de partida razonable, no valores calibrados — deben ajustarse en
# la Fase 3 (backtesting), igual que el resto de parámetros del motor de decisión.
# riesgo_vix y sentimiento_noticias implementan juntas la regla de "aversión al
# riesgo" de la spec — VIX como señal principal (cuantitativa, confiable),
# noticias como refuerzo (peso menor, fuente más frágil/rate-limited).
DEFAULT_RULES: tuple[MacroRule, ...] = (
    MacroRule("inflacion_cpi_pce", 0.30, _regla_inflacion),
    MacroRule("empleo_nfp", 0.25, _regla_empleo),
    MacroRule("tasa_fed", 0.20, _regla_tasa_fed),
    MacroRule("dxy_confirmacion", 0.10, _regla_dxy),
    MacroRule("riesgo_vix", 0.10, _regla_riesgo_vix),
    MacroRule("sentimiento_noticias", 0.05, _regla_sentimiento_noticias),
)


class MacroRuleEngine:
    """
    Motor de reglas macro (spec_bot_xauusd.md, 4.2). Combina reglas configurables,
    cada una con su propio peso, en un score de sesgo entre -1 (muy bajista) y
    +1 (muy alcista) para XAUUSD. Solo participan en el promedio ponderado las
    reglas que encontraron un evento relevante; si ninguna aplica, el score es 0.
    """

    def __init__(self, reglas: Sequence[MacroRule] = DEFAULT_RULES) -> None:
        self._reglas = reglas

    def evaluate(self, eventos: Sequence[MacroEvent]) -> MacroScoreResult:
        resultados: list[RuleResult] = []
        for regla in self._reglas:
            resultado = regla.evaluar(eventos)
            if resultado is None:
                continue
            score, razon = resultado
            resultados.append(RuleResult(regla=regla.nombre, score=score, peso=regla.peso, razon=razon))

        if not resultados:
            return MacroScoreResult(score=0.0, resultados=[])

        peso_total = sum(r.peso for r in resultados)
        score_combinado = sum(r.score * r.peso for r in resultados) / peso_total
        return MacroScoreResult(score=_clamp(score_combinado), resultados=resultados)
