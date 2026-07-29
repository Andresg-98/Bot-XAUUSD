# Bot XAUUSD — Fase 1 + Fase 2 + Fase 3 + Fase 4

Ver [spec_bot_xauusd.md](spec_bot_xauusd.md) para la especificación completa.

- **Fase 1 (Fundaciones):** entorno configurado, estructura del repo, módulo de
  ingesta (macro + precio) funcionando de forma aislada, sin lógica de trading.
- **Fase 2 (Lógica de señales):** motor de reglas macro, motor de tendencia
  técnica y motor de decisión combinado. Genera una `Signal` (LONG/SHORT/NONE)
  con razonamiento completo — **no ejecuta ninguna orden**, eso es la Fase 5.
- **Fase 3 (Backtesting):** motor propio que reproduce las velas históricas y
  llama al mismo `DecisionEngine` de la Fase 2, simulando entradas, SL/TP y
  kill switches, con métricas de win rate / profit factor / drawdown / Sharpe.
- **Fase 4 (Paper trading):** loop en vivo contra tu cuenta DEMO de MT5 —
  monitoreo continuo, evaluación por cierre de H1 o evento macro de alto
  impacto (4.8), capa de ejecución separada del motor de decisión (4.6), kill
  switches con estado persistente, y logging de cada decisión (4.7).

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
└── backtest/
    ├── risk.py                    # RiskConfig: perfil conservador de la spec (4.5/7)
    ├── engine.py                   # BacktestEngine: simula trades sobre DecisionEngine real
    └── metrics.py                   # win rate, profit factor, max drawdown, Sharpe
└── execution/
    └── mt5_broker.py               # Mt5Broker (4.6): envía órdenes reales, dry_run=True por defecto
└── live/
    ├── state.py                   # kill switches con estado persistente en disco (4.5)
    ├── decision_log.py             # log de cada decisión en JSON-lines (4.7)
    └── loop.py                      # orquestador: monitoreo + disparadores de evaluación (4.8)
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

## Precio en vivo: MetaTrader5

Vía la librería oficial `MetaTrader5`, agnóstica de broker (spec, sección 4.6).
Requiere Windows con la terminal MT5 instalada y logueada (o credenciales
login/password/server en `.env`). **El nombre del símbolo de oro varía por
broker** (`XAUUSD`, `XAUUSD!`, `XAUUSD.micro!`, `GOLD`...) — configúralo con
`MT5_SYMBOL` en `.env` si `check_price_ingestion.py` falla con el símbolo por
defecto; el script lista los símbolos disponibles que contienen XAU/GOLD.

## Precio histórico para backtesting: TwelveData

**Por qué una fuente distinta a la de ejecución en vivo:** al conectar con MT5
para esta Fase 3 encontramos que el historial de XAUUSD disponible en el
servidor del broker (cuenta demo) era de solo ~6 meses — muy por debajo del
mínimo de 2-3 años que pide la spec (4.5, 6) para que las métricas del
backtest sean estadísticamente significativas. La spec ya anticipaba esta
posibilidad (sección 5: "Datos de mercado: API del broker **o proveedor tipo
TwelveData/Polygon**"), así que el histórico de backtesting usa TwelveData
mientras la ejecución en vivo (Fase 5) sigue siendo exclusivamente MT5 (spec
4.6) — nunca se mezclan.

Consigue tu API key gratuita en https://twelvedata.com/pricing (el tier
gratuito es suficiente, pero la paginación respeta su límite de tasa con una
pausa de 8s entre páginas — traer 3 años de H1 puede tardar varios minutos).

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
calibrados — se ajustan en esta Fase 3.

## Backtesting (Fase 3)

`BacktestEngine` (spec 6) recorre las velas H1 con su contexto H4 y llama al
mismo `DecisionEngine` que correría en vivo — no hay una segunda
implementación de la lógica de señales que pueda divergir del bot real.

**Limitación conocida y decisión deliberada:** no existe un archivo histórico
del calendario de ForexFactory (el feed solo da semana actual/pasada/siguiente)
y FRED no tiene valores de consenso históricos, así que **el score macro se
fija en 0 (neutral) durante todo el backtest** — se decidió explícitamente no
simular macro con datos parciales o inventados. Esto significa que el backtest
valida honestamente el motor técnico + el filtro H4 + la gestión de riesgo,
pero no valida la contribución del motor de reglas macro. Si más adelante
consigues un calendario histórico con forecast, `BacktestEngine` acepta un
`macro_events_provider(momento) -> eventos` para conectarlo sin tocar el resto.

**Diseño anti-lookahead:** una señal decidida al cierre de la vela H1 `i` se
ejecuta al *open* de la vela `i+1`, nunca al close de la vela que la generó.

**Simulación de riesgo (`RiskConfig`, perfil conservador de 4.5/7):** tamaño de
posición por 0.5% de riesgo/operación y distancia de SL = ATR(H1) × 2.5, TP a
1:3 R:R (mínimo exigido por spec: 1:2), máximo 1 operación simultánea, y los
tres kill switches de la spec (pérdida diaria 1.5%, semanal 3%, drawdown total
8%). No simula el límite de tamaño de lote mínimo del broker (spec 4.5) — eso
se valida en la Fase 5.

```powershell
.\.venv\Scripts\python scripts\run_backtest.py       # corre el backtest con los parámetros actuales
.\.venv\Scripts\python scripts\optimize_backtest.py  # grid search sobre ATR/RR/RSI (descarga el histórico 1 vez)
```

Ambos requieren `TWELVEDATA_API_KEY` en `.env`. `run_backtest.py` imprime win
rate, profit factor, drawdown máximo y Sharpe (simplificado, por operación), y
los compara contra los objetivos de la spec: profit factor ≥ 1.4 y drawdown de
backtest ≤ 16%.

### Resultado real (histórico 2023-07 a 2026-07, XAU/USD, solo técnico)

Con los parámetros iniciales de la spec (ATR×1.5, RR 1:2) el bot sobre-operó
(~6.2 op/semana en su ventana activa, muy por encima del rango 0-3 esperado
por 4.8) y el kill switch de drawdown total (8%) lo detuvo permanentemente a
los ~4 meses. Un grid search de 36 combinaciones (`optimize_backtest.py`)
sobre `atr_multiplo_sl` × `relacion_riesgo_beneficio` × umbrales de RSI dio
como mejor resultado **ATR×2.5, RR 1:3, RSI 70/30** (ahora los valores por
defecto de `RiskConfig`): se mantuvo activo ~2 años, 1.76 operaciones/semana
(dentro de rango), profit factor **1.31**, drawdown 8.2% (el kill switch se
activó correctamente, no es un bug).

**Ninguna de las 36 combinaciones alcanzó el profit factor objetivo de 1.4.**
Esto es un resultado honesto, no un error: la spec diseñó la estrategia para
combinar macro + técnico, y este backtest solo prueba la mitad técnica por la
limitación de datos macro históricos (ver arriba). Los valores actuales son un
punto de partida mejor que los iniciales, no una estrategia lista para cuenta
real — falta al menos: (1) validar con la capa macro cuando haya datos
históricos, y (2) seguir iterando (`optimize_backtest.py` es fácil de
extender con más combinaciones).

## Paper trading (Fase 4)

Loop en vivo contra tu cuenta DEMO de MT5. Reutiliza el mismo `DecisionEngine`
del backtest — otra vez, ninguna lógica de señales duplicada.

**Capa de ejecución (`Mt5Broker`, spec 4.6):** separada del motor de decisión,
con `dry_run=True` por defecto (nunca envía nada real salvo que se pida
explícitamente `dry_run=False`). Antes de la primera orden real descubrimos
que este broker (BridgeMarkets-MT5) solo soporta el modo de llenado **FOK**,
no IOC — el código ahora detecta el modo soportado por símbolo en vez de
asumir uno fijo (`_resolver_filling`). También rechaza (no fuerza) cualquier
operación cuyo tamaño por riesgo redondee por debajo del lote mínimo del
broker (spec 4.5).

**Ciclo de monitoreo (spec 4.8):** cada 45s revisa precio/kill switches, sin
generar señales. Una nueva evaluación solo se dispara al cierre de una vela
H1, o si aparece un evento macro de impacto ALTO recién publicado — nunca en
cada tick, como prohíbe explícitamente la spec. **El calendario de
ForexFactory se cachea con refresco cada 180s** (`intervalo_macro_segundos`),
separado del intervalo de monitoreo — corriendo el bot en vivo descubrimos
que pedirlo cada 45s hace que el feed gratuito devuelva `429 Rate Limited`.
Un segundo bug relacionado (también encontrado en vivo): si el intento
fallaba, la marca de "último intento" nunca se actualizaba, así que cada
tick de 45s reintentaba de inmediato en vez de esperar los 180s — una
tormenta de reintentos que empeoraba el bloqueo. Ya corregido: la marca se
actualiza haya éxito o no.

**Redistribución de peso cuando el macro no tiene nada que aportar
(`DecisionConfig.redistribuir_peso_si_macro_vacio`):** con los pesos por
defecto (0.5/0.5), si el macro da score 0.0 porque ninguna regla encontró un
evento relevante activo (que es la mayoría del tiempo — un evento de alto
impacto no está pasando en cualquier momento dado), el técnico solo puede
empatar el umbral exacto, nunca superarlo — el bot en vivo casi nunca
dispararía una señal. `run_paper_trading.py` y `check_decision_engine.py`
activan esta opción: cuando el macro no encontró ningún evento relevante
este ciclo (no solo "score 0", sino cero reglas activas), su peso se
redistribuye 100% al técnico solo para ese ciclo. Si el macro SÍ tiene
reglas activas (aunque su score neto sea 0 porque se cancelan entre sí), se
respeta el peso configurado normalmente — el macro real nunca se ignora
cuando sí está aportando algo.

**Lotaje manual (`MT5_LOTE_FIJO`, opcional):** por defecto el tamaño de cada
entrada se calcula automáticamente por riesgo % + distancia de SL en ATR
(spec 4.5). Si defines `MT5_LOTE_FIJO` en `.env`, el bot usa ese lote fijo
para todas las entradas en su lugar — el SL sigue siendo por ATR, así que con
lote fijo el riesgo en $ de cada operación deja de ser constante (varía con
la volatilidad). El sizing automático sigue siendo el default recomendado
por ser el que preserva la garantía de riesgo constante de la spec.

**Kill switches con estado persistente (`live/state.py`):** el equity base
diario/semanal y el drawdown máximo se guardan en `data/kill_switch_state.json`
para sobrevivir a un reinicio del script durante las 4-8 semanas — si el
kill switch permanente ya se activó, sigue activo aunque reinicies.

**Logging (spec 4.7):** cada decisión (se ejecute o no), con su razonamiento
completo, se registra en `logs/decisiones.jsonl` (JSON-lines). Sin Telegram/
Discord por ahora — se puede sumar después sin tocar el resto.

**Validación end-to-end real:** antes de dejarlo corriendo, se probó un ciclo
completo en modo simulado contra MT5/ForexFactory reales, y luego una orden
real de 0.01 lotes a la cuenta demo (ticket ejecutado y cerrado
correctamente) para confirmar que el envío de órdenes funciona con este
broker específico — no solo con mocks.

### Cómo correrlo

```powershell
.\.venv\Scripts\python scripts\run_paper_trading.py          # modo simulado (dry-run), no envía nada real
.\.venv\Scripts\python scripts\run_paper_trading.py --live    # envía órdenes reales a tu cuenta MT5 (debe ser DEMO)
```

No termina solo — corre indefinidamente hasta que lo detengas con Ctrl+C.
Está pensado para dejarlo corriendo en una terminal abierta; si cierras la
terminal o apagas el PC, el bot se detiene hasta que lo reinicies a mano. Si
necesitas que sobreviva reinicios de Windows sin supervisión, usa el
Programador de tareas de Windows para relanzar el script — no incluido aún.

**Antes de correrlo en `--live`:** confirma que "AutoTrading" esté activado
en tu terminal MT5 (botón en la barra de herramientas) — sin eso, toda orden
real falla aunque la cuenta lo permita.

## Probar cada pieza de forma aislada

```powershell
.\.venv\Scripts\python scripts\check_forexfactory_ingestion.py
.\.venv\Scripts\python scripts\check_macro_ingestion.py
.\.venv\Scripts\python scripts\check_price_ingestion.py
.\.venv\Scripts\python scripts\check_decision_engine.py   # usa la sesión MT5 activa o credenciales en .env
```

## Tests

```powershell
.\.venv\Scripts\pytest
```
