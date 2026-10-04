# Opción B explorada: ¿qué debería exigir R3 al beta?

Exploración, no decisión. **No modifica ningún criterio, no sella nada y no autoriza nada.** Las cifras
salen de los registros sellados vía `scripts/research/measure_beta_alternatives.py` (solo lectura).

---

## 1. La conclusión principal: B no es una opción abierta, está cerrada por una regla escrita del Director

Planteé B como «revisar qué exige R3 al beta». La exploración la reencuadra. El documento normativo
(«Propuesta - Función Objetivo y Criterios de Admisión», §10.3, resumido en DOC-011 §11) dice:

> *MONITORIZACIÓN. No convierte jamás. Si no se puede detectar la degradación, no se puede PARAR, y ningún
> tamaño vuelve aceptable "no puedo parar": solo hace perder despacio sin saber por qué. Sigue siendo
> requisito absoluto de admisión.*

Y el §10.0 de la misma enmienda anticipa exactamente esta situación, con una cautela sobre quien redacta:

> *…el mismo agente que impuso las siete puertas redacta ahora su relajación, de modo que su sesgo se invierte
> y pasa a tener incentivo de complacencia correctora. Por eso §10.3 fija dos innegociables por escrito, y
> **cualquier futura redacción que permita esquivarlos está mal POR MUY RAZONABLE QUE SUENE EL ARGUMENTO.***

Además, DOC-011 §12 declara una prima de estado adverso inobservable «INADMISIBLE SIN MÁS, no admisible en
pequeño», y el destino (§10) pide primas «explotadas mientras la evidencia las respalde y retiradas cuando deje
de hacerlo».

**Consecuencia.** Todas las variantes de B (abajo, B0 y B1) sustituyen o suspenden el requisito de monitor.
Cada una es una redacción que permite esquivar el innegociable, y el Director escribió por adelantado que
una redacción así está mal por razonable que suene. **Yo mismo produje una (B1) y suena razonable: eso es la
señal de alarma que el §10.0 describe, no un argumento a su favor.** Abrir B exigiría que el Director **retirara
formalmente el innegociable del §10.3**, que es una decisión de otra categoría que ajustar un gate.

## 2. Lo medido

### 2.1 SPY, XYLD y QYLD no son tres ideas

Cada fondo contra su propio subyacente (retornos ajustados, brutos, sin dimensionar):

| Fondo | Subyacente | Correlación | Beta | R² | Alfa anual (t) | Sharpe fondo | Sharpe subyacente |
|---|---|---|---|---|---|---|---|
| XYLD | SPY | 0,859 | 0,695 | 0,738 | −1,53 % (−0,69) | 0,661 | 0,895 |
| QYLD | QQQ | 0,884 | 0,612 | 0,782 | −2,54 % (−1,16) | 0,677 | 0,953 |

XYLD y QYLD entre sí: correlación 0,846. Sobre los 2.198 días que comparten con SPY (2018-01 a 2026-09):
Sharpe SPY **0,813**, XYLD **0,552**, QYLD **0,601**.

**Lectura:** los dos covered calls son beta diluido con alfa negativo (no significativo). Pasan el Sharpe
porque el subyacente tuvo un Sharpe de 0,9 en estos diez años, no por la venta de opciones, y **rinden menos
que el índice que contienen**. No aportan evidencia adicional sobre SPY: tres admisiones serían una.

### 2.2 Lo que valdría el tramo bajo controles más estrictos

Las mismas series selladas, re-juzgadas por el motor con un límite de drawdown menor. El Sharpe no se mueve
(es invariante a la escala); cambian el peso y lo que gana el tramo:

| | Límite | Peso | Sharpe | Cota inf. | Cota de DD | Neto anual en 1.000 | En 200 |
|---|---|---|---|---|---|---|---|
| SPY | 15 % | 0,280 | 0,812 | 0,270 | 0,150 | 42,62 | 8,52 |
| SPY | 10 % | 0,183 | 0,812 | 0,270 | 0,100 | 27,77 | 5,55 |
| SPY | 7,5 % | 0,136 | 0,812 | 0,270 | 0,075 | 20,64 | 4,13 |
| XYLD | 15 % | 0,323 | 0,660 | 0,200 | 0,150 | 30,13 | 6,03 |
| XYLD | 10 % | 0,211 | 0,660 | 0,200 | 0,100 | 19,66 | 3,93 |
| QYLD | 15 % | 0,320 | 0,677 | 0,222 | 0,150 | 33,37 | 6,67 |
| QYLD | 10 % | 0,209 | 0,677 | 0,222 | 0,100 | 21,77 | 4,35 |

El peor año de la muestra fue 2022: SPY −19,0 %, XYLD −12,4 %, QYLD −19,7 % sin dimensionar. Con los pesos del
15 %, eso son aproximadamente −5,3 %, −4,0 % y −6,3 % del tramo.

**Lectura:** el beneficio en dinero es de decenas de dólares al año o menos. Lo que B compraría es **cruzar
a Fase 1**, no rentabilidad.

## 3. Las variantes exploradas, y por qué ninguna sobrevive al §10.3

| | Qué es | Veredicto |
|---|---|---|
| **B0** | Sustituir R3 por tamaño más estricto | Es literalmente lo que el §10.3 prohíbe |
| **B1** | Un tercer estado de R3, «no aplicable», para una clase estructural declarada antes de medir, con controles más estrictos en otras dimensiones | Evita la letra de B0 pero **suspende el requisito de monitor para una clase**: es la redacción que el §10.0 declara errónea por razonable que suene |
| **B2** | Excluir el beta por definición del destino | Es lo que la plataforma ya hace de hecho. No cambia nada |

**B1, descrita para que quede registrado qué se evaluó** (no se recomienda): clase definida antes y con
independencia de los resultados (exposiciones cuyo retorno *es* el precio de un mercado diversificado); vinculada
a la pre-declaración, de modo que XYLD y QYLD quedarían excluidas por haber declarado monitores y fallado;
R1 de 10 % o menos, vida de doce meses con re-admisión y tramo hacia adelante pre-declarado; nada retroactivo.
Entre lo existente solo rescataría **SPY**, y no pudo declarar monitor porque ninguno existe.

## 4. Comprobación de integridad (Propuesta §0, §10.0 y §11.0)

- **Más fácil o más difícil:** más fácil en R3 y más difícil en R1, vida y exigencia hacia adelante. El §10.0 ya
  fijó que una enmienda que relaja «exige un fundamento distinto y más exigente», y el §10.6 lista las tres
  disciplinas que lo contienen (declarar qué falta y qué lo mediría, caducidad, registro que nunca dice
  «admitido» a secas). **Esas disciplinas están escritas para puertas «no medidas». La monitorización está
  excluida expresamente de esa conversión.**
- **A quién rescata:** solo SPY entre lo existente. Mi afirmación anterior de «exactamente tres» era demasiado
  amplia: XYLD y QYLD no se rescatan (declararon monitor y fallaron) y son redundantes con SPY (2.1).
- **Procedencia débil, que debe quedar escrita:** la idea la formulé después de ver que SPY, XYLD y QYLD fallaban
  R3. Es el patrón que el §0 de la Propuesta prohíbe, modificar a la vista de un resultado que la modificación
  beneficiaría (§11.5).

## 5. Riesgos que B1 no eliminaría

1. **Un solo régimen.** La evidencia de SPY es una década de mercado alcista. El Sharpe sobre diez años no
   distingue una prima de un periodo favorable.
2. **Retirada.** El destino exige retirar la prima cuando la evidencia deje de respaldarla. Para el beta, la
   «muerte» es un estancamiento de décadas que ningún monitor ve. Solo la acotarían el drawdown, el tamaño y la
   vida: un límite de pérdida, no una detección.
3. **Precedente.** Retirar un innegociable para una clase es un precedente para la siguiente.
4. **Valor.** Se pagaría con ese precedente para ganar entre 20 y 43 dólares al año.

## 6. Qué decidiría el Director

1. **¿Se mantiene el innegociable del §10.3?** Si sí, B queda cerrada y la salida es A o C. Es lo que su propio
   texto pre-decide.
2. Si se quisiera reabrirlo, sería una enmienda constitucional distinta de «ajustar R3»: con fundamento
   independiente sellado antes de tocar nada, y con la cautela del §10.0 sobre el propio agente que lo propone.

## 7. Mi lectura

**No recomiendo B y la considero cerrada mientras el §10.3 siga en pie.** Lo que la exploración aporta no es una
vía, sino dos hechos que conviene tener registrados con independencia de la decisión:

- **XYLD y QYLD no aportan nada sobre SPY** (alfa negativo, rendimiento inferior al subyacente en la misma
  ventana). Dejan de ser candidatos a cualquier vía.
- **Lo único que B desbloquearía es cruzar a Fase 1 con una hipótesis de valor marginal.** Si lo que se busca es
  aprender operativamente, conviene preguntarse qué enseña Fase 1 que no hayan enseñado ya la cadena PAPER, la
  verificación continua de la maquinaria y la medición del coste de ejecución.
