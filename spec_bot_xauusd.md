# Especificación Técnica: Bot de Trading Algorítmico XAUUSD
### Estrategia híbrida macro + tendencial con ejecución automática

---

## 1. Objetivo del proyecto

Construir un sistema que:
1. Ingiere noticias/eventos macroeconómicos en tiempo real.
2. Calcula un sesgo direccional macro (USD fuerte/débil → impacto en oro).
3. Combina ese sesgo con un análisis de tendencia técnica (EMA, estructura de precio).
4. Genera señales de entrada/salida con gestión de riesgo estricta.
5. Ejecuta órdenes automáticamente en un broker, con múltiples capas de seguridad.

**No es objetivo de este proyecto:** predecir el precio exacto o el timing perfecto. El sistema opera con probabilidades y sesgos, no certezas.

---

## 2. Advertencias no negociables (leer antes de programar)

- Este bot operará dinero real. Cualquier fallo de lógica, de conexión o de datos puede generar pérdidas.
- **Fase obligatoria de demo/paper trading mínimo 4-8 semanas** antes de considerar cuenta real.
- Kill switch automático por pérdida diaria máxima (ej. -3% del capital) que detiene el bot sin intervención humana.
- Nunca desplegar sin stop-loss obligatorio en cada orden.
- Revisar los Términos de Servicio del broker y de cualquier proveedor de datos antes de automatizar (algunos prohíben scraping o trading algorítmico sin autorización).
- **Cláusula de responsabilidad:** el bot está diseñado para conectarse a cualquier broker vía MT5, sin verificar ni garantizar las condiciones específicas de cada cuenta (apalancamiento, tamaño de lote mínimo, spread, si permite EAs, etc.). Es responsabilidad exclusiva de cada usuario:
  - Confirmar que su broker y tipo de cuenta permiten el trading algorítmico que este bot ejecuta.
  - Entender que **a menor capital, mayor es el riesgo relativo** — con cuentas pequeñas, incluso el porcentaje de riesgo "conservador" definido en este documento (sección 4.5) puede no ser ejecutable con precisión debido a los tamaños de lote mínimos del broker, lo que puede forzar a operar con un riesgo real superior al planeado.
  - Ajustar los parámetros de riesgo a su propia tolerancia y situación financiera antes de operar con capital real — los valores de este documento son un punto de partida razonable, no una garantía de resultado ni una recomendación financiera personalizada.
  - El bot debe, de todas formas, **rechazar automáticamente** cualquier operación que no pueda ejecutarse dentro de los límites de riesgo configurados (nunca forzar una operación fuera de esos límites solo por conveniencia de ejecución).

---

## 3. Arquitectura general

```
┌─────────────────┐     ┌──────────────────┐     ┌───────────────────┐
│  Fuentes de      │────▶│  Motor de Reglas   │────▶│  Motor de Decisión │
│  Datos Macro     │     │  Macro (sesgo)     │     │  (combina señales) │
└─────────────────┘     └──────────────────┘     └─────────┬──────────┘
                                                              │
┌─────────────────┐     ┌──────────────────┐               │
│  Datos de Precio │────▶│  Motor de Tendencia│──────────────┘
│  en Tiempo Real  │     │  Técnica (EMA/ATR) │
└─────────────────┘     └──────────────────┘
                                                              │
                         ┌──────────────────┐               ▼
                         │  Gestión de Riesgo │◀────┌───────────────────┐
                         │  (tamaño, SL, TP)  │     │  Capa de Ejecución │
                         └──────────────────┘     │  (Broker API)      │
                                                    └───────────────────┘
                                                              │
                         ┌──────────────────┐               ▼
                         │  Logging + Alertas │◀────  Kill Switch / Monitor
                         └──────────────────┘
```

---

## 4. Componentes detallados

### 4.1 Ingesta de datos macro
**Problema con ForexFactory:** no tiene API pública oficial; su calendario se consume vía scraping, lo cual puede violar sus términos de servicio. Alternativas recomendadas (elegir una):

| Fuente | Tipo | Costo | Notas |
|---|---|---|---|
| Trading Economics API | Calendario económico + datos | Pago (tiene tier gratuito limitado) | Cobertura amplia, buena documentación |
| FRED (Federal Reserve) | Datos macro EE.UU. | Gratis | Solo EE.UU., sin calendario de eventos futuros |
| Finnhub | Calendario económico + noticias | Freemium | Buena opción para empezar |
| FXStreet API | Calendario económico | Pago | Muy usado en la industria |

**Output esperado del módulo:** evento, hora, país, impacto (alto/medio/bajo), valor esperado, valor real, valor previo.

### 4.2 Motor de reglas macro
Lógica de sesgo basada en correlaciones conocidas:
- Dato de inflación (CPI/PCE) EE.UU. por encima de lo esperado → USD fuerte → sesgo bajista en XAUUSD.
- Decisión de tasas de la Fed más hawkish de lo esperado → sesgo bajista en XAUUSD.
- Datos de empleo (NFP) fuertes → USD fuerte → sesgo bajista en XAUUSD.
- Eventos de aversión al riesgo (geopolítica, caídas bursátiles) → sesgo alcista en XAUUSD (activo refugio).
- Índice del dólar (DXY) como confirmación cruzada del sesgo.

Cada regla debe tener un peso configurable (no todas las noticias importan igual) y un "score" de sesgo entre -1 (muy bajista) y +1 (muy alcista).

### 4.3 Motor de tendencia técnica
- EMA 50 y EMA 200 en H1 y H4 para dirección de tendencia.
- ATR para medir volatilidad y ajustar stop-loss dinámicamente.
- Detección de estructura (máximos/mínimos crecientes o decrecientes).
- RSI/Stochastic como filtro de sobrecompra/sobreventa, no como señal principal.

### 4.4 Motor de decisión
Combina el score macro + el score técnico. Ejemplo de lógica simple:
```
señal_final = (peso_macro * score_macro) + (peso_tecnico * score_tecnico)

si señal_final > umbral_compra  → considerar LONG
si señal_final < umbral_venta   → considerar SHORT
si no                            → no operar
```
Los pesos y umbrales deben ser parámetros configurables y sujetos a backtesting, no valores fijos "mágicos".

### 4.5 Gestión de riesgo (módulo crítico) — Parámetros definidos (perfil conservador)

| Parámetro | Valor | Justificación |
|---|---|---|
| Riesgo máximo por operación | 0.5% del capital | Conservador; prioriza supervivencia sobre crecimiento rápido |
| Pérdida máxima diaria (kill switch temporal) | 1.5% del capital | Detiene el bot por el resto del día si se toca |
| Pérdida máxima semanal (pausa para revisión manual) | 3% del capital | Requiere revisar la lógica antes de reactivar, no reinicio automático |
| Drawdown máximo total (kill switch permanente) | 8% del capital | Detiene el bot por completo hasta revisión humana completa |
| Relación riesgo/beneficio mínima | 1:2 | Con win rate moderado (~40-45%) sigue siendo rentable |
| Operaciones simultáneas máximas | 1 | Evita exposición correlacionada (crítico si luego se suman otros metales/pares) |
| Stop-loss | Obligatorio, calculado por ATR (no pips fijos) | Se adapta a la volatilidad real del oro |
| Apalancamiento máximo recomendado | 1:10 a 1:20 | Aunque el broker ofrezca más, limitarlo reduce el riesgo de liquidación por spikes |

**Restricción crítica de cuenta pequeña:** con $100-500 de capital, 0.5% de riesgo por operación equivale a $0.50-$2.50 por trade. Esto SOLO es viable con:
- Una cuenta **cent/micro** (donde 1 lote = 1,000 unidades en vez de 100,000), o
- Un broker/prop firm que permita tamaños de posición fraccionarios muy pequeños en XAUUSD.

Si el broker no soporta esto, el bot debe tener una validación que **rechace la operación** en vez de forzar un tamaño de posición que viole el % de riesgo definido — nunca sacrificar la regla de riesgo por ejecutar el trade.

**Backtesting objetivo:** Profit factor mínimo de 1.4, drawdown máximo en backtest no mayor al doble del drawdown máximo real permitido (16%), sobre al menos 2-3 años de datos históricos de XAUUSD.

**Periodo de paper trading:** mínimo 8-10 semanas (más largo que el estándar, acorde al perfil conservador) antes de considerar cuenta real.

**Multi-activo (fase futura):** cuando se agreguen otros metales/pares, el riesgo total simultáneo entre todas las posiciones abiertas no debe superar el mismo 0.5-1% agregado — es decir, si hay 2 posiciones abiertas en distintos pares, cada una debe reducir su tamaño proporcionalmente, no sumar 0.5% + 0.5% de riesgo independiente si están correlacionados (ej. oro y plata suelen moverse juntos).

### 4.6 Capa de ejecución — Agnóstico de broker vía MT5

**Decisión de diseño:** el bot se conecta únicamente a través de **MetaTrader 5**, usando la librería oficial `MetaTrader5` en Python. Esto lo hace **compatible con cualquier broker por diseño**, porque la conexión no es a un broker específico sino a la terminal MT5 ya instalada y logueada en la máquina del usuario.

**Cómo funciona:**
```python
import MetaTrader5 as mt5

mt5.initialize(
    login=12345678,
    password="contraseña_del_usuario",
    server="NombreDelBroker-Servidor"  # ej. "Exness-MT5Real", "RoboForex-Pro", etc.
)
```
- El bot recibe las credenciales (login, password, server) que cada usuario obtiene de su propio broker al abrir su cuenta MT5.
- No hay integración por broker que programar ni mantener — cualquier broker que ofrezca MT5 es compatible automáticamente.
- Limitación real: MT5 corre nativamente en Windows (en Linux/Mac requiere Wine o una VPS con Windows) — esto sí aplica sin importar el broker.

**Se elimina la necesidad de soporte para MT4** con esta decisión, ya que se prioriza una única plataforma universal en vez de mantener dos integraciones distintas. Si en el futuro se requiere MT4 para algún broker específico que no ofrezca MT5, se puede evaluar agregar el puente ZeroMQ mencionado anteriormente como fase adicional.

El módulo de ejecución debe estar completamente separado del módulo de decisión, para poder correr en modo "simulado" sin tocar ninguna cuenta real durante pruebas, sin importar qué broker esté conectado.

### 4.7 Logging, monitoreo y alertas
- Registrar cada decisión (aunque no se ejecute) con el razonamiento: qué evento macro, qué score técnico, por qué se tomó o no la operación.
- Alertas vía Telegram/Discord/email cuando se ejecuta una orden o se activa el kill switch.
- Dashboard simple (puede ser un archivo HTML generado o algo como Streamlit) para ver estado del bot, posiciones abiertas, P&L.

---

## 4.8 Ciclo operativo del bot — Temporalidad y frecuencia

Esta sección define el comportamiento operativo exacto que Claude Code debe implementar.

### Estructura multi-temporalidad

| Timeframe | Rol | Qué determina |
|---|---|---|
| **H4** (4 horas) | Filtro de contexto / tendencia de fondo | Dirección permitida (EMA 50/200). Si H4 es alcista, el bot **solo** evalúa entradas en compra; nunca opera contra este filtro. |
| **H1** (1 hora) | Generación de la señal de entrada | Cruces de estructura, retrocesos a la EMA, confirmación de que el precio respeta la tendencia de H4. Aquí se decide SI se opera. |
| **M15** (opcional) | Refinamiento de ejecución | Solo ajusta el punto exacto de entrada y el stop-loss una vez que H1 ya aprobó la operación. No decide si operar, solo cómo. |

### Frecuencia de monitoreo vs. frecuencia de decisión

Estos dos ciclos son independientes y no deben confundirse en la implementación:

1. **Monitoreo continuo (cada 30-60 segundos):**
   - Lectura de precio en tiempo real.
   - Lectura del feed de noticias/calendario macro.
   - Verificación de kill switches (drawdown diario, semanal, total).
   - **No genera señales de entrada**, solo mantiene el estado del sistema actualizado.

2. **Evaluación de nueva señal de entrada (dos disparadores posibles):**
   - **Al cierre de cada vela H1** (una vez por hora): se recalculan EMAs, estructura de precio y sesgo técnico. Es el ciclo normal de evaluación.
   - **Inmediatamente tras un evento macro de alto impacto** (CPI, decisión de tasas, NFP, etc.): el bot re-evalúa el sesgo macro fuera de su ciclo horario normal, porque estos eventos pueden cambiar el contexto de golpe.
   - **Explícitamente prohibido:** evaluar o generar señales en cada vela M1 o en cada tick de precio — esto genera sobre-operación y ruido, incompatible con el perfil conservador definido.

### Frecuencia esperada de operaciones (criterio de aceptación)

Con los filtros conservadores definidos (alineación obligatoria de H4 + H1 + sesgo macro no contradictorio, máximo 1 operación simultánea), el número esperado de señales es de **0 a 3 por semana**, no por día.

- Si en backtesting o en paper trading el bot genera señales con mayor frecuencia que esto de forma sostenida, es indicio de que los filtros están mal calibrados o son demasiado permisivos, y debe revisarse antes de continuar — no ajustar el objetivo hacia arriba para "justificar" más operaciones.
- Este comportamiento de baja frecuencia es intencional y deseado, no un defecto a corregir.

---

## 5. Stack tecnológico sugerido

- **Lenguaje:** Python 3.11+
- **Conexión a broker:** librería oficial `MetaTrader5`, agnóstica de broker (ver sección 4.6)
- **Datos de mercado:** API del broker o proveedor tipo TwelveData/Polygon
- **Backtesting:** `backtrader` o `vectorbt`
- **Programación de tareas/tiempo real:** `asyncio` + `APScheduler`
- **Base de datos:** SQLite para empezar (migrar a PostgreSQL si escala)
- **Notificaciones:** API de Telegram Bot (gratis y simple)
- **Control de versiones:** Git, para que Claude Code pueda hacer seguimiento de cambios

---

## 6. Plan de desarrollo por fases (para Claude Code)

**Fase 1 — Fundaciones**
- Configurar entorno, estructura del repo, conexión a fuente de datos macro y de precio.
- Módulo de ingesta funcionando de forma aislada (sin lógica de trading aún).

**Fase 2 — Lógica de señales**
- Implementar motor de reglas macro.
- Implementar motor de tendencia técnica.
- Implementar motor de decisión combinado.

**Fase 3 — Backtesting**
- Probar la estrategia combinada contra datos históricos de XAUUSD (mínimo 2-3 años).
- Métricas clave: win rate, profit factor, máximo drawdown, Sharpe ratio.
- Iterar parámetros ANTES de tocar dinero real.

**Fase 4 — Paper trading**
- Conectar a cuenta demo del broker.
- Correr en vivo sin dinero real mínimo 4-8 semanas.
- Validar que el comportamiento real coincide con el backtest.

**Fase 5 — Producción controlada**
- Cuenta real con capital mínimo.
- Kill switch y alertas activas desde el primer día.
- Revisión semanal de desempeño antes de escalar capital.

---

## 7. Métricas de éxito (definidas — perfil conservador, cuenta pequeña)

Estos son los criterios de aceptación que le puedes dar directamente a Claude Code:

- **Perfil de riesgo:** Conservador
- **Capital inicial de prueba (cuenta real):** $100-500 (cuenta cent/micro obligatoria — ver sección 4.5)
- **Riesgo por operación:** 0.5% del capital
- **Pérdida máxima diaria:** 1.5% → pausa el resto del día
- **Pérdida máxima semanal:** 3% → pausa para revisión manual
- **Drawdown máximo total:** 8% → kill switch permanente
- **Relación riesgo/beneficio mínima:** 1:2
- **Profit factor mínimo en backtest:** 1.4
- **Periodo de paper trading obligatorio:** 8-10 semanas
- **Activos:** XAUUSD inicialmente; arquitectura preparada para agregar metales/pares mayores correlacionados a futuro (con ajuste de riesgo agregado, ver sección 4.5)
- **Plataforma soportada:** MT5 únicamente, agnóstico de broker (funciona con cualquier broker que ofrezca MT5, vía login/password/server del usuario)

---

## 8. Nota final

Este documento es un punto de partida, no un plan cerrado. Claude Code debería ayudarte a iterar sobre cada módulo, escribir tests, y sobre todo, correr el backtesting exhaustivo antes de que cualquier orden real se ejecute. La automatización reduce el trabajo manual, pero no reduce el riesgo de mercado — eso solo lo controla una buena gestión de riesgo.
