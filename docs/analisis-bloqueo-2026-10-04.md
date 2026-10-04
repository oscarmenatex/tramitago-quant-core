# Análisis del bloqueo: estado a 2026-10-04

Sustituye en la práctica a la auditoría del §13 de DOC-011 (2026-10-03), que quedó desfasada: desde
entonces se midieron XYLD y QYLD, se selló la regla conjunta y la regla C, se ejecutó el cribado de
monitores y se construyó el contrato de cortos en PAPER. **No modifica ningún criterio ni autoriza nada.**
Todo lo que sigue sale de los registros sellados y del código, no de memoria.

---

## 1. Dónde estamos: ocho hipótesis medidas, ninguna admitida

Última admisión de cada hipótesis con medición (Sh = Sharpe neto y su cota inferior, R1 drawdown, R2
supervivencia, R3 monitorabilidad, R4 capacidad, R5 coste de decisión):

| Hipótesis | Sharpe punto | Cota inf. | Sh | R1 | R2 | R3 | R4 | R5 |
|---|---|---|---|---|---|---|---|---|
| carry cripto (salida, holdout) | — | −2,634 | falla | falla | ok | ok | n/e | n/e |
| **SPY** (prima de renta variable) | 0,812 | +0,270 | ok | ok | ok | **n/e** | ok | ok |
| crédito (LQD contra IEF) | 0,188 | −0,337 | **falla** | ok | ok | ok | ok | ok |
| SVXY (prima de volatilidad) | 0,502 | −0,045 | **falla** | ok | ok | **falla** | ok | ok |
| **XYLD** (covered call, S&P 500) | 0,660 | +0,200 | ok | ok | ok | **falla** | ok | ok |
| **QYLD** (covered call, Nasdaq-100) | 0,677 | +0,222 | ok | ok | ok | **falla** | ok | ok |

(n/e = no evaluable; no es lo mismo que fallar.) Las otras dos medidas son hipótesis antiguas de señales
(eth-sma3 y spy-mom10), sin interés aquí: con las seis de la tabla suman las ocho.

## 2. Qué bloquea exactamente

**Ninguna hipótesis cumple a la vez el Sharpe y la monitorabilidad.** Tres cruzan el Sharpe y fallan **solo**
en R3 (SPY, XYLD, QYLD). Una se puede vigilar y no paga (crédito). Eso ya lo decía el §13, pero ahora son
tres las que pagan y fallan el monitor, y el cribado de esta semana cerró la salida más obvia.

**La evidencia de monitores, sumada:** cuatro vínculos medidos en las declaraciones (tres refutados, uno
inalcanzable) más 14 candidatos del cribado: 10 refutados, 3 inalcanzables (las inversiones de curva, estado
raro, §12) y 1 aprobado en el margen mínimo, sobre la exposición que ya falla el Sharpe y con el mecanismo
más débil del espacio. El único monitor validado de todo el proyecto es el del carry cripto (8 de 8 folds en
un holdout), que pierde un 5,83 % anual neto.

**Por qué la conjunción está vacía en lo medido** (esto es lectura, no ley):
1. Lo que cruza el Sharpe es **beta de renta variable** en tres envoltorios. SPY, XYLD y QYLD son una idea
   con amplitud efectiva cercana a uno, no tres hallazgos.
2. La «muerte» de la prima de renta variable es indistinguible de un drawdown a resolución anual. Ninguna
   variable ensayada (curva, spreads, VIX, tipos reales, dólar) lo separa con siete folds de un año.
3. Las primas que no son beta y tienen monitores limpios (carry cripto, crédito) pagan menos de 0,5 o pierden.

## 3. Qué tenemos

- **Evidencia y gobierno:** registros sellados e inmutables; contrato de pre-declaración de siete preguntas;
  seis gates de admisión con sus esquemas; juez de nivel con cotas por bootstrap; regla de parada, registro
  de multiplicidad; regla conjunta de familia con veredicto sellado; regla C del cribado, sellada antes del
  escaneo; ciclo de vida derivado y disparadores puros.
- **Motor de primas por spec:** largos, **cortos simples** y pares; comprobación de distribuciones;
  dimensionado contra la cota de drawdown del 15 %. Un spec nuevo se mide en minutos, no en un runner nuevo.
- **Datos:** Alpaca (SIP, ajustado y crudo), FRED vía la VM sin que la clave salga de ella, proveedores
  cripto. Capturas selladas, verificables sin red.
- **Cribado de monitores:** espacio sellado, escáner con holdout de un solo uso, ya ejecutado una vez.
- **Ejecución:** cadena PAPER para BTC-USD, **contrato de instrumento** (largos y cortos en PAPER para
  acciones, con stop protector), capital real solo para BTC-USD; comité de capital; control de riesgo.
- **Operación:** la verificación de la maquinaria corre sola en la VM (782 PASS, 0 errores desde el
  1 de octubre) y la VM está actualizada a `main`.

## 4. Qué falta

**Lo decisivo: una hipótesis admitida. 0 de 8.** Y más precisamente, una que tenga a la vez Sharpe neto
con cota inferior positiva y un monitor con vínculo validado.

**Oferta de candidatos.** Lo que queda en el registro, con los rangos de efecto declarados:

| Candidato | Efecto recordado | Problema |
|---|---|---|
| VIXM corto | 0,30–0,70 | su monitor (pendiente de la curva) ya se refutó en SVXY |
| Carry de divisas | 0,30–0,50 | el extremo optimista solo toca la barra de 0,50 |
| Prima de plazo | 0,20–0,40 | por debajo de la barra |
| Roll de materias primas | 0,10–0,40 | por debajo de la barra |
| DIVO, PUTW | — | misma familia que XYLD y QYLD |

No hay hoy un candidato con un efecto esperado que alcance 0,50 neto, no sea beta y tenga un monitor sin
refutar.

**Pendiente, no decisivo para cruzar:**
- `forward_paper_configuration()` sigue siendo un literal (muerde al operar, no al cruzar).
- La regla C está sellada pero **no se aplica en el código de admisión**: necesita un esquema nuevo.
- Nada une el ciclo de vida con la ejecución; los disparadores no tienen planificador ni canal de aviso.
- Capital real con acciones: sin construir, a propósito.
- El repositorio de documentos sigue sin control de versiones y el §13 de DOC-011 está desfasado.
- El test hacia adelante constituido el 2026-10-03 no se puede mirar hasta el 2027-10-06.

## 5. Lectura

El problema ya no es de ingeniería. La plataforma ha hecho lo que se le pidió: refutó ocho veces, con sus
propias reglas, y ninguna de las intervenciones sobre criterios rescató nada.

Lo que sí es nuevo: **R3 más el umbral de Sharpe excluyen a la vez dos clases.** Excluyen el beta
(no tiene monitor) y excluyen las primas pequeñas (no tienen Sharpe). Solo sobreviviría una prima que no sea
beta, pague 0,5 neto y se pueda vigilar, y en lo medido no hay ninguna. Eso no prueba que no exista, pero
sí que no está en el espacio explorado y que buscarla cuesta una familia económica nueva cada vez.

## 6. Salidas, todas del Director

| | Qué es | Coste y límite |
|---|---|---|
| **A. Mantener la barra y ampliar la oferta** | Familias económicas nuevas, justificadas por el siguiente entregable (decisión 4) | Lento; los rangos recordados de lo que queda están casi todos bajo la barra |
| **B. Revisar qué exige R3 para el beta** | Preguntarse si, para una exposición cuya muerte es un drawdown, un monitor es el control adecuado o lo son R1, el tramo y la vida acotados | Rescataría **exactamente tres hipótesis ya observadas** (SPY, XYLD, QYLD; unos 30 a 43 dólares al año en un tramo de 1.000). Es el patrón del §11.5 por construcción y revierte el §12 («inadmisible sin más»). Exigiría un fundamento independiente sellado antes |
| **C. Esperar a que el tiempo produzca observaciones** | Hipótesis con fecha hacia adelante y re-medición anual | Con folds de un año, los cinco folds que exige M1 son cinco años, no meses |

Si el Director opta por B, el diseño que menos choca con el §11.0 es el que **no es más fácil**: cambiar el
control, no aflojarlo (por ejemplo, un R1 más estricto, tramo menor, vida más corta, remedición anual), y
sin re-juzgar ninguna hipótesis denegada, que se re-declararía como una nueva.
