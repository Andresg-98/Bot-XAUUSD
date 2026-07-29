# Bot XAUUSD — Fase 1: Fundaciones

Ver [spec_bot_xauusd.md](spec_bot_xauusd.md) para la especificación completa.
Esta fase entrega: entorno configurado, estructura del repo, y el módulo de
ingesta (macro + precio) funcionando de forma aislada, sin lógica de trading.

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
scripts/                         # scripts manuales para probar la ingesta contra las APIs/feeds reales
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

## Probar la ingesta de forma aislada

```powershell
.\.venv\Scripts\python scripts\check_forexfactory_ingestion.py
.\.venv\Scripts\python scripts\check_macro_ingestion.py
.\.venv\Scripts\python scripts\check_price_ingestion.py
```

## Tests

```powershell
.\.venv\Scripts\pytest
```
