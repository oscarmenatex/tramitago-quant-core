# Cribado de monitores: espacio candidato

**Estado: DRAFT_FOR_REVIEW_NOT_SEALED.** Borrador para revisión del Director. No se ha leído ningún dato de mercado y nada está sellado. La fuente es `scripts/research/monitor_screen/space.json`; este documento se genera de él.

---

## 1. Para qué sirve

Find which candidate monitor variables lead adverse returns of a premium exposure, BEFORE any premium is declared around them. Every premium measured so far was declared with its monitor and the monitor then failed R3; this inverts the order.

Four monitor links have been measured on this project and three were refuted (VIX minus realised volatility on XYLD 4 of 6 and QYLD 3 of 7, the VIX term structure on SVXY 2 of 5). Each refutation cost a full declare, capture, measure and seal cycle. A scan over a sealed list costs minutes and counts every candidate, so a variable is only built around once it has led adverse returns on a window it was selected on AND on a holdout the scan could not read. Passing the scan is NOT evidence that a premium pays: it only says the variable is worth declaring a monitor on.

## 2. Qué se fija antes de mirar nada

- **Ventana de descubrimiento:** 2018-03-01 a 2023-10-01.
- **Holdout (el escaneo no puede leerlo):** 2023-10-01 a 2026-10-01.
- **Por qué estas fechas:** The shortest sealed exposure (SVXY) begins on 2018-03-01 and all three end on 2026-10-01. About 65 percent of the span is for discovery and the last three years are the holdout. The split was fixed from the exposures' dates alone, before any return was read.
- **Regla de paso en descubrimiento:** al menos 5 folds utilizables de 7, con 10 días de estado activo como mínimo en cada uno, y consistencia de 0.70. the engine's own M1 constants (premium_engine.LINK_MINIMUM_USABLE_FOLDS and LINK_CONSISTENCY_THRESHOLD, monitors.MINIMUM_TRIGGER_DAYS_PER_FOLD). Nothing is set here, so the screen cannot be easier or harder than the gate a premium will later face.
- **Qué mide:** a fold counts when the mean next-day return of the exposure on days the state is ON is below its mean on the days it is OFF; a fold with fewer than 10 ON days is unusable, not failed.
- **Confirmación en el holdout:** on the holdout, the mean next-day exposure return on ON days minus OFF days has an upper 95 percent bound below zero. three years cannot hold five usable folds, so the holdout uses one pooled claim, declared here and not chosen after seeing the discovery result.
- **Retorno de la exposición:** next-day close-to-close return, adjusted for distributions, of the exposure held long; for the credit pair, the next-day spread return of long LQD against short IEF.
- **Dirección:** ON means the exposure's next-day return is LOWER than on OFF days; the direction is part of each claim and is never searched both ways.
- **Parámetros fijos, sin buscar:** horizonte de cambio de 21 días y mediana de 252. declared here, not searched: tuning either would multiply the candidates and turn a screen into a fit.

## 3. Variables

| Variable | Mide | Familia de mecanismo | Disponibilidad |
|---|---|---|---|
| `T10Y2Y` | slope of the Treasury curve, ten years minus two | growth and recession expectations | TO_PROBE |
| `T10Y3M` | slope of the Treasury curve, ten years minus three months | growth and recession expectations | TO_PROBE |
| `BAA10Y` | Moody Baa yield minus the ten year Treasury, the price of credit risk | price of credit risk | CAPTURED_FOR_CREDIT_PREMIUM |
| `VIXCLS` | market implied volatility of the S&P 500 | price of volatility risk | CAPTURED_FOR_VARIANCE_PREMIUM |
| `DFII10` | ten year Treasury real yield, the price of inflation protected money | discount rates | TO_PROBE |
| `DTWEXBGS` | broad trade weighted US dollar index | global financial conditions | TO_PROBE |

Todas son diarias, cotizadas, sin revisiones y conocidas al decidir. Una variable que se revisa o llega tarde no se puede usar para actuar, aunque correlacione.

## 4. Exposiciones

| Exposición | Posición | Disponible desde |
|---|---|---|
| US equity risk premium | SPY held long | 2018-01-02 |
| investment grade credit premium | LQD long against IEF short | 2018-01-02 |
| variance risk premium via the VIX futures roll | SVXY held long | 2018-03-01 |

## 5. Los 14 candidatos

| | Exposición | Variable | Estado «activo» | Mecanismo declarado |
|---|---|---|---|---|
| C01 | US equity risk premium | `T10Y2Y` | diferencia en o bajo cero | An inverted curve has preceded recessions because short rates exceed long rates only when markets expect growth to fall, and equity earnings fall with growth. |
| C02 | US equity risk premium | `T10Y3M` | diferencia en o bajo cero | The three month version of the inversion reads the policy stance more directly, and a restrictive stance lowers equity returns by tightening financing. |
| C03 | US equity risk premium | `BAA10Y` | sube respecto a 21 días antes | A widening credit spread means lenders demand more for default risk, which has led falls in equities that depend on the same borrowers. |
| C04 | US equity risk premium | `BAA10Y` | sobre su mediana de 252 días | A credit spread above its own recent median marks a regime in which risk is being priced higher, and equities carry less cushion in it. |
| C05 | US equity risk premium | `VIXCLS` | sube respecto a 21 días antes | Rising implied volatility marks rising demand for protection, and volatility clusters, so adverse days come in runs after a rise. |
| C06 | US equity risk premium | `DFII10` | sube respecto a 21 días antes | A rising real yield raises the rate at which future earnings are discounted, which lowers equity valuations when it moves quickly. |
| C07 | US equity risk premium | `DTWEXBGS` | sube respecto a 21 días antes | A strengthening dollar tightens global financial conditions and lowers the dollar value of foreign earnings of the companies in the index. |
| C08 | investment grade credit premium | `BAA10Y` | sube respecto a 21 días antes | The credit premium is the compensation for default risk, and a widening spread is the market repricing that risk against the position. |
| C09 | investment grade credit premium | `BAA10Y` | sobre su mediana de 252 días | A spread above its own recent median means the compensation is being repriced upward, and the repricing hurts the long credit leg first. |
| C10 | investment grade credit premium | `T10Y2Y` | diferencia en o bajo cero | An inverted curve signals expected recession, in which default risk rises and corporate bonds underperform Treasuries of the same duration. |
| C11 | investment grade credit premium | `VIXCLS` | sube respecto a 21 días antes | Rising equity volatility marks risk aversion across markets, and investors sell corporate bonds against Treasuries during risk aversion. |
| C12 | variance risk premium via the VIX futures roll | `VIXCLS` | sube respecto a 21 días antes | The product is short volatility, so a rising volatility index is the adverse state itself and volatility clustering says it persists. |
| C13 | variance risk premium via the VIX futures roll | `BAA10Y` | sube respecto a 21 días antes | Credit stress usually precedes or accompanies volatility spikes, so widening spreads may warn before the short volatility position is hurt. |
| C14 | variance risk premium via the VIX futures roll | `DTWEXBGS` | sube respecto a 21 días antes | A rapidly strengthening dollar is a global funding stress channel, and funding stress is a recurring cause of volatility spikes. |

## 6. Lo que ya se midió y se cuenta aunque no se vuelva a escanear

| Variable | Exposición | Resultado | Fuente |
|---|---|---|---|
| VIX minus 21 day realised volatility, at or below zero | XYLD | link 4 of 6, refuted | ADMISSION|81bdb90c |
| VIX minus 21 day realised volatility, at or below zero | QYLD | link 3 of 7, refuted | FAMILY_VERDICT|84d929a2 |
| VIX3M minus VIX, at or below zero | SVXY | link 2 of 5, refuted | ADMISSION for the volatility premium |
| BAA10Y relative to the credit loss | CREDIT | trigger on 0 of 2184 days, unreachable | ADMISSION for the credit premium |
| perpetual funding rate sign | crypto carry | exit rule met 8 of 8 on a holdout | the carry exit rule validation |

Prior tests are counted, not rescanned: a screen that forgot the four it already ran would be reporting one candidate in nineteen as one in fourteen. Total contado: **19**.

## 7. Qué esperar del azar

Con 7 folds, una variable **sin ninguna relación** con los retornos supera la regla de descubrimiento el 22.7% de las veces. Con 14 candidatos eso son **3.2 aprobados falsos esperados**. Por eso superar el descubrimiento no es evidencia: solo lo es confirmar en el holdout un único reclamo declarado antes.

## 8. Decisiones del Director registradas

- **Decisión 2** (2026-10-03): The scan is executed by a SEPARATE module that reuses the monitor evaluation and the sealing pattern. Discovery is not extended. The sealed experiment machinery is untouched, and the scan owns its own space, scan and holdout records.
- **Decisión 3** (2026-10-03): The six variables stay exactly as drafted until changing them is needed to unblock something or the project changes phase. The probe asks only about the four still to probe, and no variable is added or removed to improve a result.
- **Decisión 4** (2026-10-03): The catalogue may grow to raise the chance of finding a valid hypothesis, but only toward economic families that form an investigable space AND are justified by the next deliverable. Any addition must name its economic family and the deliverable that needs it before it is added, and is a new sealed space, not an edit.

## 9. Decisión abierta

1. Whether a link confirmed on the holdout may stand as the validated link R3 asks for, or whether every premium must still pass the fold rule on its own measurement window. It must be decided and sealed BEFORE the scan is run, because deciding after seeing who passes is the pattern section 11.5 forbids. The options, risks and benefits are in docs/monitor-screen-r3-decision.md.

## 10. Lo que NO se ha hecho

- No se leyó ninguna serie ni ningún retorno.
- No se selló el espacio: se sella tras tu revisión y tras la sonda de disponibilidad.
- No existe todavía el escáner: Discovery solo reconoce Strategies de precio, volumen y funding.
