# Cortos en el ejecutor: qué está construido y qué decisión falta

Orden explícita del Director, 2026-10-03 (DOC-011 §7 exige una orden por etapa).

---

## 1. Lo que encontré antes de construir

Pedir «cortos en el ejecutor» suponía que bastaba añadir un lado a una orden. No es así:

- La cadena operativa de `pipeline.py` está atada a **un instrumento**: `BTC-USD` aparece como
  literal en unos 66 sitios.
- Sus términos de riesgo también son literales: capital exactamente **200 USD**, exposición
  como máximo **50**, presupuesto de riesgo exactamente **5**.
- Solo acepta los pares `(ENTER, BUY)` y `(EXIT, SELL)`.
- Alpaca **no permite cortos en cripto**.

Es decir: hoy el ejecutor no puede operar **ni un ETF largo**, y mucho menos corto. Ampliar esa
cadena a acciones relaja restricciones que protegen capital real, y eso es una decisión tuya,
no un efecto lateral de añadir un lado.

## 2. Lo que sí está construido

`tramitago_quant_core/execution/equity_order.py`: el **contrato de órdenes de renta variable,
largo y corto**. Puro, solo PAPER, sin red. Es lo que llamaría una cadena cableada.

El lado se deriva de dos hechos, nunca se escribe a mano:

| | LONG | SHORT |
|---|---|---|
| ENTER | BUY | SELL |
| EXIT | SELL | BUY |

Lo que el contrato **rechaza**, y por qué:

| Regla | Por qué |
|---|---|
| Un corto sin movimiento adverso máximo declarado, o cuya pérdida en ese punto supera el presupuesto por posición | un corto pierde sin límite; un largo pierde como mucho su nocional |
| Un corto que no lleva su orden de cobertura protectora (OTO con stop) | que la pérdida quede acotada en el broker y no solo en un documento |
| Activo no *shortable* o no *easy to borrow* | un corto difícil de pedir prestado puede ser reclamado o llevar un coste que nadie midió |
| Corto con acciones fraccionadas | el broker no ofrece cortos fraccionados |
| Cubrir más de lo que está corto | dejaría una posición larga que nadie pidió |
| Abrir mientras hay cualquier posición abierta en el instrumento | una posición por instrumento: ninguna orden puede voltear una posición |
| Cripto | no se puede cortar en el broker |
| Órdenes que no son límite | |

Garantías comprobadas por test, no declaradas:
- **Ninguna secuencia de órdenes aceptadas puede voltear una posición** (4.000 órdenes
  aleatorias contra un libro que ejecuta todo lo aceptado).
- Todo corto aceptado cabe en su presupuesto de pérdida en el stop.
- Las tres protecciones críticas se probaron quitándolas una a una: cada mutación rompe el
  test que corresponde.

Incluye también `borrow_cost_per_day`: Alpaca no cobra por un activo fácil de prestar, así que
el valor honesto es cero, pero un coste de préstamo es un número **declarado**, no una omisión.

## 3. Lo que NO está hecho, a propósito

- **No se ha tocado `pipeline.py`.** El test que vigila que el ejecutor no abra cortos sigue en
  verde y `EXECUTOR_CAN_OPEN_SHORTS` sigue en `False`: la cadena operativa todavía no puede
  hacerlo, y el registro de candidatos no debe decir lo contrario.
- **No hay cortos con capital real.** El contrato solo emite peticiones al host PAPER.
- **No se ha enviado ninguna orden** y no se ha usado ninguna credencial.
- **El lado de investigación no cambia.** Medir un candidato corto (VIXM, carry de divisas)
  necesita que el motor de primas soporte un único instrumento corto (hoy solo soporta pares).
  Es un cambio aparte y menor, y no era lo pedido.

## 4. Decisión del Director: cablear la cadena

Para que una orden corta salga por la cadena operativa hay que cambiar, en `pipeline.py`:

| Dónde | Qué es hoy |
|---|---|
| ~4543 y ~4682 | `instrument must be BTC-USD` al preparar propuestas (real y paper) |
| ~4578 y ~4712 | el lado como `{"ENTER": "BUY", "EXIT": "SELL"}` |
| ~4891–4894 | validación de instrumento y del par acción/lado |
| ~5064 | la petición Alpaca exige `BTC-USD` |
| ~5142–5155 | revalidación con capital == 200, exposición ≤ 50, presupuesto == 5 |
| ~5572 | «Only a BTC-USD limit order is allowed» |

Opciones:

1. **No cablear todavía.** El contrato queda listo; la cadena sigue siendo de BTC-USD.
2. **Cablear solo PAPER, con un contrato de instrumento declarado** (tabla de instrumentos
   permitidos con sus topes), dejando intactos los literales de capital real. Es la opción que
   recomiendo: permite medir cortos en PAPER sin tocar lo que protege dinero real.
3. **Generalizar también la cadena real.** Es el cambio que hace falta para Fase 1 con acciones,
   y cambia los topes de capital. Requiere tu decisión expresa y no recomiendo hacerlo antes de
   que exista una hipótesis admitida que lo necesite.

Cualquier cableado debe actualizar el registro de candidatos **en el mismo cambio**, y el test
que lee `pipeline.py` debe seguir diciendo la verdad.

## 5. Riesgos propios del corto, para tenerlos presentes

- Pérdida sin límite si no hay cobertura protectora; por eso es obligatoria en el contrato.
- *Short squeeze*: el movimiento adverso se concentra justo cuando más cuesta cubrir. Un stop
  puede ejecutarse peor que su precio.
- Recall del préstamo: el broker puede obligar a cubrir.
- Dividendos: quien está corto los paga.
- Costes de margen y límites de la cuenta, que dependen del tipo de cuenta.
- Ninguno de estos está en una serie de retornos histórica.
