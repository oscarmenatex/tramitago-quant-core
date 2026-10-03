# Cortos en el ejecutor: estado actual

Orden explícita del Director, 2026-10-03 (DOC-011 §7 exige una orden por etapa). Primero se
construyó el contrato de órdenes (PR #137); después se cableó la cadena **solo PAPER**, con un
contrato de instrumento (esta actualización).

---

## 1. Qué cambió en la cadena

La cadena de `pipeline.py` estaba atada a un instrumento: `BTC-USD` como literal y los términos
de riesgo también (capital exactamente 200 USD, exposición ≤ 50, presupuesto exactamente 5).
Ahora los términos se leen **por instrumento** desde `execution/instrument_contract.py`:

| | BTC-USD | Resto (acciones y ETF) |
|---|---|---|
| Términos | constantes en el código, iguales a los literales de siempre | `config/instrument_contracts.json`, validados |
| Entornos | PAPER y LIVE | **solo PAPER**, y no es un ajuste: ningún campo puede nombrar otro entorno |
| Cortos | no | solo si el contrato lo declara, con movimiento adverso máximo y pérdida por posición |
| ¿Se puede sobrescribir por fichero? | No | Sí (es el punto), salvo declarar BTC-USD, que se rechaza |

El comportamiento de BTC-USD **no ha cambiado**: sus 76 pruebas de la cadena (propuesta,
aprobación, revalidación, petición, ejecución PAPER y LIVE, binding de evidencia) pasan sin
modificar.

## 2. El flujo PAPER para un corto

```
observe_alpaca_paper_instrument      el broker dice si es negociable, shortable, fácil de prestar,
                                     fraccionable, y cuánto hay en cartera (observado, no escrito)
instrument_risk_config               el contrato de riesgo, derivado del contrato de instrumento
prepare_instrument_order_proposal    juzgada contra lo observado y contra el contrato
record_manual_approval               (sin cambios) tu aprobación
revalidate_approved_proposal         (generalizada) falla si el contrato cambió tras aprobar
prepare_alpaca_request               (generalizada) rechaza LIVE para cualquier instrumento no BTC
execute_alpaca_paper_order           un corto sale como orden OTO con su stop protector
observe_alpaca_paper_position        la posición corta se observa con cantidad negativa
```

Una propuesta de instrumento **no lleva comité**, así que `execute_alpaca_live_order` nunca puede
ejecutarla: exige un comité de capital sellado.

## 3. Lo que sigue cerrado, y está probado

- **Capital real:** ningún instrumento que no sea BTC-USD puede prepararse, validarse ni
  ejecutarse en LIVE. Hay tres barreras independientes: el entorno al preparar la petición, la
  validez de la petición, y el cuerpo de la orden real. Se comprobó quitando cada una.
- **Un contrato cambiado tras aprobar** invalida la revalidación (la identidad del contrato va en
  la propuesta). Quitar el instrumento del contrato también.
- **Un corto sin su stop** no puede convertirse en orden.
- **Nada voltea una posición:** cubrir más de lo que está corto, o abrir con una posición abierta,
  se rechaza.
- **El transporte PAPER** solo permite consultas de posición y activo para símbolos con contrato.
  El transporte LIVE sigue sin conocer nada que no sea BTC-USD.
- **Una salida nunca queda bloqueada por un tope de entrada.** Un corto que se ha ido en contra
  puede necesitar una cobertura mayor que su nocional de apertura. Las salidas tienen su propio
  tope (`max_exit_exposure_usd`, por defecto el doble del de entrada).

## 4. Números del contrato: CONFIRMADOS por el Director (2026-10-03)

`config/instrument_contracts.json` trae tres instrumentos con cifras en la misma proporción que el
piloto de BTC-USD (exposición 25 % del capital, presupuesto 2,5 %). Las propuse yo y el Director
las **confirmó** el 2026-10-03; cambiarlas es una decisión suya:

| | Capital | Exposición | Presupuesto | Corto |
|---|---|---|---|---|
| XYLD, QYLD | 1000 | 250 | 25 | no |
| VIXM | 1000 | 250 | 25 | sí, movimiento adverso 10 %, pérdida máxima 25 |

Con esos números, un corto de VIXM a 20 USD admite como máximo 12 acciones (240 USD; pérdida en
el stop 24 USD).

## 5. Lo que NO está hecho

- **Capital real con acciones.** Es la opción 3 del documento anterior y sigue sin hacerse: cambia
  los topes de capital y requiere tu decisión expresa, preferiblemente cuando exista una hipótesis
  admitida que lo necesite.
- **Un script de operación** que encadene estos pasos (necesitaría tus credenciales de Alpaca PAPER,
  que yo no toco). Hoy es una API de funciones, probada con un transporte simulado.
- **Cuenta:** no se consulta si la cuenta tiene los cortos habilitados ni su margen. Si no los
  tiene, el broker rechazará la orden, y esa respuesta queda registrada como rechazo.
- **Ninguna hipótesis corta está declarada.** El motor ya puede medirla (sección 7), pero declarar
  VIXM corto, o el carry de divisas, exige sus siete respuestas y su monitor, antes de capturar nada.
- **Pruebas de la suite completa:** un paquete `tests` ajeno en site-packages tapa al del
  repositorio, y por eso seis módulos fallan al importarse con `unittest discover` (también en
  `main`). Se ejecutaron aparte con el paquete correcto y pasan.

## 6. Riesgos propios del corto

- Pérdida sin límite si no hay cobertura protectora; por eso es obligatoria.
- *Short squeeze*: el movimiento adverso se concentra justo cuando más cuesta cubrir. Un stop puede
  ejecutarse peor que su precio.
- Recall del préstamo: el broker puede obligar a cubrir.
- Dividendos: quien está corto los paga.
- Márgenes y límites de la cuenta, que dependen de su tipo.
- Ninguno de estos está en una serie de retornos histórica.

## 7. Medir un corto simple en el motor de primas

Un spec puede declarar `instrument.direction: "SHORT"`. Reglas:

- El retorno diario de la posición es el **negativo** del retorno del instrumento (nocional
  reequilibrado cada día). Lo que el instrumento paga, por ejemplo una distribución, es un coste
  para quien está corto.
- **Debe declarar `cost.borrow_annual`**, aunque sea `"0"`: un coste de préstamo omitido se lee
  igual que uno gratis. Se carga cada día en posición, y escala con el peso como el resto de costes.
- **Debe declarar `instrument.max_adverse_move`**: un corto no tiene límite natural de pérdida.
  Es el espejo del stop que el contrato de instrumento exige al ejecutarlo.
- Un par ya contiene una pata corta, así que `SHORT` solo vale para un instrumento único. Declarar
  estos campos en un largo se rechaza.
- El **control** del monitor se mantiene en la misma dirección que la posición, y el vínculo del
  monitor se prueba sobre el retorno **de la posición**, no sobre el del instrumento.
- El resultado incluye un bloque `short`: peor día en contra y cuántos días el instrumento subió
  más que el movimiento adverso declarado. En esos días un stop habría ejecutado por encima de su
  precio y la pérdida presupuestada se habría superado. Es información, no una puerta: la cota de
  drawdown ya contiene los *squeezes* que haya en la muestra.

Comprobado: un corto de unos retornos es **exactamente** un largo de su negación; el coste de
préstamo cuesta exactamente su tasa anual por día en posición; y el régimen de los largos no
cambia (el test de migración reproduce las cifras selladas de SPY, SVXY y crédito).

Límite que conviene recordar: la muestra histórica de un producto que cae casi siempre contiene
pocos *squeezes*, y un corto pierde donde la historia tiene menos datos.
