# Decisión abierta: ¿puede un vínculo confirmado en el holdout valer como el que pide R3?

Documento para la decisión del Director. **No decide nada**: expone opciones, riesgos y beneficios,
y dice a quién afectaría. Es una decisión de gobernanza y lleva la carga del §11.0, porque la
dirección en que puede moverse es la que afloja un gate.

---

## 1. Qué pide R3 hoy

Un monitor es admisible solo si el **vínculo inferencial** en que descansa está validado
(DOC-011 §12; INSUFFICIENT_EVIDENCE no es un aprobado). Hay dos formas:

- **M1, empírica:** el vínculo se prueba por folds **sobre la misma ventana en que se mide la
  prima**: en al menos 5 folds utilizables (con 10 días de estado activo cada uno), el retorno medio
  con el estado activo debe ser peor que sin él, en al menos el 70 % de los folds.
- **M2, identidad:** el vínculo se deduce por aritmética de un parámetro con fuente. Solo está
  disponible si M1 era inalcanzable, nunca si M1 se midió y falló.

Los cuatro vínculos medidos hasta hoy: tres refutados (XYLD 4 de 6, QYLD 3 de 7, SVXY 2 de 5) y uno
inalcanzable (crédito, 0 disparos en 2.184 días).

## 2. Qué añadiría la vía nueva

El cribado produciría, para una **variable** y una **exposición**, un vínculo confirmado en un holdout
que el escaneo no pudo leer. La pregunta es si una prima puede declarar ese vínculo como su monitor
sin pasar además M1 en su propia ventana.

| Opción | Qué cambia en R3 | Dirección |
|---|---|---|
| **A. No** | Nada. Cada prima pasa M1 propio. El cribado solo orienta qué declarar. | Neutra |
| **B. Vía alternativa, con condiciones** | Una prima puede apoyarse en un vínculo del cribado **en lugar de** M1 propio. | **Más fácil** en un aspecto, más estricta en otros |
| **C. Requisito adicional** | La prima debe pasar M1 propio **y** el vínculo del cribado. | **Más difícil**, nunca más fácil |

## 3. Beneficios (de la opción B)

1. **Separa selección y confirmación.** M1 prueba el vínculo sobre la misma muestra donde se midió el
   Sharpe: usa los datos dos veces. El cribado selecciona en una ventana y confirma en otra que no vio.
   Metodológicamente es más limpio.
2. **Más potencia para un solo reclamo.** M1 reparte 6 o 7 folds de poco más de un año: es una prueba
   de signos de baja potencia, y por eso un 4 de 6 puede ser ruido o ser real. Un contraste agrupado
   con bootstrap por bloques sobre tres años, con un único reclamo declarado antes, tiene más resolución.
3. **Un vínculo sirve a varias primas.** Una variable validada para una exposición no se vuelve a
   probar por cada envoltorio, lo que ahorra ciclos y reduce multiplicidad.
4. **Convierte el cribado en algo con consecuencia.** Con la opción A, un candidato que pasa no cambia
   ningún gate: el trabajo solo orienta.

## 4. Riesgos y cómo se mitigan

1. **Aflojar un gate.** Es la dirección que lleva la carga mayor del §11.0. *Mitigación:* decidirlo y
   sellarlo **antes** de ejecutar el escaneo. Decidir después de ver quién pasa es exactamente el patrón
   del §11.5.
2. **Un solo régimen.** Tres años de holdout (2023-2026) son un régimen concreto: tipos altos y mercado
   alcista. Un vínculo confirmado ahí puede no valer en otro. *Mitigación:* exigir que se refiera a la
   exposición exacta y reconfirmarlo cada año (es un disparador de calendario, ya diseñado).
3. **Multiplicidad residual.** Se esperan unos 3,2 aprobados falsos en descubrimiento, y todos
   compartirían un único holdout. *Mitigación:* declarar ahora la corrección de Bonferroni: si pasan
   *k* candidatos, cada uno debe superar el holdout con confianza 1 − 0,05/*k*, y el holdout se usa
   **una sola vez**.
4. **Monitor no es mecanismo.** Un vínculo confirmado dice que la variable anticipa retornos *de la
   exposición*, no que la prima siga pagando. Ya ocurrió: el control de XYLD mostró que el monitor
   detectaba caídas de mercado y no la prima. Una variable de mercado (curva, spread, VIX) detendría la
   posición en las caídas: es un **stop de mercado**, legítimo pero otra afirmación (XYLD caveat 5).
5. **El vínculo no es el beneficio de actuar sobre él.** Se mide sobre retornos brutos del día siguiente.
   Actuar implica latencia y costes de entrar y salir, que el vínculo no incluye. R3 compara latencia con
   drawdown, no el resultado neto de la regla de salida. *Mitigación:* exigir además una prueba neta de
   la regla de salida, como la del carry (8 de 8 folds en un holdout).
6. **Con dos vías, gana la más fácil.** Con una vía M1 y otra externa, el incentivo es elegir la que pasa.
   *Mitigación:* la vía se declara **en la pre-declaración, antes de medir** la prima, y no se cambia.
7. **Falsa confianza.** El daño operativo está acotado (tramo pequeño, drawdown limitado a 15 %,
   aprobación manual de cada orden). El daño real sería **epistémico**: creer que se sabe cuándo parar.

## 5. A quién rescataría, que es lo que el §11.0 exige decir

El §11.0 pide medir qué admite un cambio antes de argumentarlo. Medido contra lo ya sellado:

- **Ninguna de las 11 admisiones selladas cita un vínculo del cribado**, porque el cribado aún no
  existe. Algunas usan las mismas series (el crédito usa BAA10Y; las de volatilidad, el VIX), pero con
  su propio vínculo. Por eso **nada cambia de forma retroactiva** y ningún registro se toca.
- Pero hay **tres hipótesis ya medidas y denegadas solo por R3** que la vía podría beneficiar si un
  candidato confirmara: **SPY** (Sharpe 0,81, cota 0,27, peso 28 %), **XYLD** (0,66) y **QYLD** (0,68).
  La vía B es, por tanto, un aflojamiento que beneficiaría a candidatos ya observados, lo que es el
  patrón que el §11.5 trata con más sospecha.
- SPY es el caso más sensible: el registro dice que su único observable que no es P&L no existe
  (*NO_NON_PNL_VARIABLE_EXISTS*) y que la renta variable queda rechazada porque nadie puede decir
  cuándo muere. Un vínculo confirmado de la curva o del spread con SPY sería un stop de mercado, no
  la detección de la muerte de la prima.

**Salvaguarda propuesta:** ninguna hipótesis denegada se re-juzga. Cualquiera que quiera usar la vía
nueva se **re-declara como hipótesis nueva** (nueva versión, con sus siete respuestas y su
multiplicidad), con el monitor elegido antes de capturar. Así no cambia ningún registro y el beneficio
cuesta una declaración, no un re-juicio.

## 6. Condiciones mínimas si se adopta la opción B

1. Decisión y fundamento sellados **antes** del escaneo, con la comprobación de integridad del §11.0.
2. El reclamo de holdout es único y fue declarado en el espacio (ya lo está).
3. Corrección por el número de candidatos que pasan, declarada ahora.
4. El vínculo vale solo para la **exposición exacta** del cribado.
5. Reconfirmación anual como disparador.
6. Prueba neta de la regla de salida, además del vínculo.
7. La vía se declara en la pre-declaración de la prima, antes de medirla.
8. Nada retroactivo: las denegadas se quedan denegadas.

## 7. Recomendación

- **Decidir ahora, antes del escaneo.** La razón no es que convenga abrir la vía, sino que decidir
  después de ver los resultados es lo que no se puede hacer.
- Entre las tres, recomiendo **B con las ocho condiciones**, por una razón y una cautela:
  *razón:* con A el cribado no puede cambiar nada, y sin consecuencia posible el trabajo es solo
  orientación; *cautela:* la opción C es la única que no afloja nada, así que si prefieres cero riesgo
  de aflojar, C es coherente: el cribado serviría entonces para elegir monitores mejores, no para
  desbloquear.
- Si dudas entre B y C, la decisión no es irreversible: se puede empezar por C (solo más estricto),
  observar los resultados del escaneo y pasar a B por una revisión posterior con el procedimiento
  completo. El coste es el tiempo.

## 8. Lo que esta decisión no resuelve

- **Estados adversos raros** (§12): el holdout de tres años tiene aún menos días de estado raro que un
  fold. La vía no hace admisibles las primas de cola.
- **El Sharpe.** Un monitor validado no hace que una prima pague. Hay que seguir superando 0,5 neto.
- **El mecanismo.** Un monitor puede vigilar el mercado y no la prima.
