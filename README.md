# Bot XAUUSD — Fase 1 + Fase 2

Ver [spec_bot_xauusd.md](spec_bot_xauusd.md) para la especificación completa.

- **Fase 1 (Fundaciones):** entorno configurado, estructura del repo, módulo de
  ingesta (macro + precio) funcionando de forma aislada, sin lógica de trading.
- **Fase 2 (Lógica de señales):** motor de reglas macro, motor de tendencia
  técnica y motor de decisión combinado. Genera una `Signal` (LONG/SHORT/NONE)
  con razonamiento completo — **no ejecuta ninguna orden**, eso es la Fase 5.

## Setup

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
Copy-Item .env.example .env
# edita .env con tu FRED_API_KEY y (opcional) tus credenciales MT5
```

## Estructura

```
bot_xauusd/
├── config.py                  # carga de .env / Settings
├── models.py                   # MacroEvent, PriceBar
└── ingestion/
    ├── macro_forexfactory.py    # cliente calendario ForexFactory (fuente principal, tiempo real)
    ├── macro_fred.py             # cliente FRED (respaldo, datos oficiales de la Fed)
    └── price_mt5.py               # cliente MetaTrader5 (precio, agnóstico de broker)
└── signals/
    ├── indicators.py              # EMA, ATR, RSI, detección de estructura (funciones puras)
    ├── macro_rules.py              # motor de reglas macro (4.2): score de sesgo -1..+1
    ├── technical_trend.py           # motor de tendencia técnica (4.3): filtro H4/H1 (4.8)
    └── decision_engine.py            # motor de decisión (4.4): combina macro+técnico -> Signal
scripts/                         # scripts manuales para probar cada pieza contra datos/feeds reales
tests/                            # tests unitarios con mocks (no llaman a APIs reales)
```

## Fuente de datos macro: ForexFactory (principal)

**Aviso legal/ToS — léelo antes de usar en cuenta real:** ForexFactory no tiene
API pública oficial (spec, sección 4.1) y bloquea activamente el scraping de su
sitio (`forexfactory.com/terms-of-service` devuelve 403 a peticiones automatizadas).
El bot usa en su lugar `nfs.faireconomy.media/ff_calendar_*.json`, el feed JSON
que alimenta el propio widget embebible de ForexFactory (operado por su
proveedor "Fair Economy"). Esto evita el scraping de HTML, pero **sigue sin ser
una API documentada ni licenciada explícitamente para terceros** — es el método
que usan la mayoría de proyectos open-source de calendario ForexFactory, pero el
riesgo de ToS no es cero. Antes de pasar a cuenta real, revisa tú mismo los
términos vigentes de ForexFactory y de Fair Economy (spec, sección 2).

Da el esquema ideal de la sección 4.1 de la spec: evento, hora, país (moneda),
impacto, valor esperado (forecast), valor real (una vez publicado) y valor previo.

## Fuente de datos macro: FRED (respaldo)

Gratis, oficial y sin límite práctico, pero sin calendario de eventos futuros ni
valores de consenso — solo series históricas ya publicadas (CPI, Core PCE, NFP,
tasa de desempleo, Fed Funds Rate, índice del dólar). Útil como verificación
cruzada de datos oficiales de la Fed, no como fuente principal del calendario.
`valor_esperado` queda siempre en `None` en este cliente.

Consigue tu API key gratuita en https://fred.stlouisfed.org/docs/api/api_key.html

## Precio: MetaTrader5

Vía la librería oficial `MetaTrader5`, agnóstica de broker (spec, sección 4.6).
Requiere Windows con la terminal MT5 instalada y logueada (o credenciales
login/password/server en `.env`).

## Motor de reglas macro (4.2)

Reglas configurables con peso propio, cada una devuelve un score entre -1 (muy
bajista) y +1 (muy alcista) para XAUUSD; el score combinado es el promedio
ponderado de las reglas que sí encontraron un evento relevante:

| Regla | Peso por defecto | Lógica |
|---|---|---|
| `inflacion_cpi_pce` | 0.35 | CPI/PCE real > esperado → USD fuerte → bajista |
| `empleo_nfp` | 0.30 | NFP real > esperado → USD fuerte → bajista |
| `tasa_fed` | 0.25 | Tasa Fed real > esperado (hawkish) → bajista |
| `dxy_confirmacion` | 0.10 | Índice del dólar (FRED) subiendo → bajista |

**Limitación conocida:** la regla de "aversión al riesgo" de la spec
(geopolítica/caídas bursátiles → alcista) **no está implementada** — requiere
una fuente de sentimiento de noticias o un proxy (p. ej. VIX) que aún no existe
en el módulo de ingesta. Implementarla sin esos datos sería adivinar.

## Motor de tendencia técnica (4.3) y filtro H4/H1 (4.8)

EMA50/EMA200 en H4 fijan la dirección permitida; H1 debe confirmar la misma
dirección o no hay señal técnica (score 0). Si ambas coinciden, se suma
confirmación de estructura de precio (fractales de máximos/mínimos) y se
amortigua el score si el RSI está en sobrecompra/sobreventa (filtro, no señal
principal, como pide la spec).

## Motor de decisión (4.4)

`señal_final = peso_macro * score_macro + peso_tecnico * score_tecnico`. Si
`señal_final` supera el umbral en la dirección que permite H4 → LONG/SHORT; si
no, no se opera. **Garantía no negociable (spec 4.8):** el bot nunca genera una
señal contraria al filtro H4, sin importar cuán fuerte sea el sesgo macro — si
el score combinado "querría" ir en contra de H4, el resultado es no operar, no
invertir la dirección. Está probado explícitamente en
[tests/test_decision_engine.py](tests/test_decision_engine.py).

Pesos y umbrales (`DecisionConfig`) son un punto de partida, no valores
calibrados — se ajustan en la Fase 3 (backtesting).

## Probar cada pieza de forma aislada

```powershell
.\.venv\Scripts\python scripts\check_forexfactory_ingestion.py
.\.venv\Scripts\python scripts\check_macro_ingestion.py
.\.venv\Scripts\python scripts\check_price_ingestion.py
.\.venv\Scripts\python scripts\check_decision_engine.py   # requiere credenciales MT5 en .env
```

## Tests

```powershell
.\.venv\Scripts\pytest
```
