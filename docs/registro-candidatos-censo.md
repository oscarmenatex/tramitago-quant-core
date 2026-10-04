# Registro de candidatos ampliado con las clases del censo: para revisión

**Estado: SIN SELLAR.** `python3.11 -B scriptsesearch\expand_candidate_register_census.py` es un simulacro; sellar exige
`--seal`. Los rangos de efecto son **estimaciones mías, recordadas y débiles**, y sellarlo es un acto del Director.

## 1. Dos partes

**Correcciones** (porque el registro vigente engaña): XYLD y QYLD seguían como «sin probar» con monitor «comprobable». Ahora son
`MEASURED_DEAD` con la refutación medida (4 de 6 y 3 de 7), y DIVO hereda el fallo como **inferido** (pesa en contra, no elimina).

**Adiciones**: una entrada por cada clase del censo que está sin medir, no es beta de renta variable y tiene un pagador.

## 2. Las siete entradas nuevas

| Entrada | Rango recordado | Veredicto del registro | Cortos | Monitor |
|---|---|---|---|---|
| high-yield credit premium, via HYG against IEF | 0.15–0.50 | UNCERTAIN (barra 0.500) | sí | EMPIRICAL_LINK_REFUTED |
| emerging-market sovereign debt premium, via EMB against IEF | 0.10–0.40 | BELOW_BAR (barra 0.500) | sí | UNEXAMINED |
| mortgage premium, via MBB against IEF | 0.00–0.30 | BELOW_BAR (barra 0.500) | sí | UNEXAMINED |
| municipal bond premium, via MUB against IEF | 0.00–0.35 | BELOW_BAR (barra 0.500) | sí | UNEXAMINED |
| preferred securities premium, via PFF held long | 0.10–0.45 | BELOW_BAR (barra 0.500) | no | UNEXAMINED |
| inflation risk premium, via TIP against IEF | 0.00–0.30 | BELOW_BAR (barra 0.500) | sí | UNEXAMINED |
| energy futures roll premium, via energy futures funds held SHORT (UNG, BNO) | 0.20–0.90 | UNCERTAIN (barra 0.500) | sí | UNEXAMINED |

### Por qué cada rango, en las palabras de cada entrada

**high-yield credit premium, via HYG against IEF**

- Pagador: corporate borrowers with weaker credit, who pay a spread above Treasuries for the money.
- Rango: RECALLED, wide and weaker than any range measured on this platform; an input to a feasibility test and never evidence. Anchored on the investment-grade pair MEASURED on this platform at a net Sharpe of 0.19 over 2018 to 2026: high yield pays a larger spread but carries a larger equity beta, so the range is widened upward from that point and stays weak. The expression is a PAIR: long the credit fund against the ten-year Treasury fund at equal notional, as the investment-grade pair already measured here, which requires a short
- Monitor (EMPIRICAL_LINK_REFUTED): the screen measured BAA10Y states against the investment-grade pair: the 21-day widening met the link in 2 of 7 folds (C08) and the level above its median in 1 of 6 (C09), both refuted and the second with the sign opposite to the declared one. High yield is a sibling exposure, so this weighs against it and does not eliminate it. The ICE high-yield spread itself holds about three years on FRED
- Datos: Alpaca SIP, MEASURED in the instrument census of 2026-10-03: HYG first monthly bar 2016-01-01, liquid by the census floor, shortable; IEF first monthly bar 2016-01-01, liquid by the census floor, shortable

**emerging-market sovereign debt premium, via EMB against IEF**

- Pagador: emerging-market governments, who pay a spread above Treasuries to borrow in dollars.
- Rango: RECALLED, wide and weaker than any range measured on this platform; an input to a feasibility test and never evidence. Sovereign spread premia are documented to be positive and unstable, with losses concentrated in crises. The expression is a PAIR: long the credit fund against the ten-year Treasury fund at equal notional, as the investment-grade pair already measured here, which requires a short
- Monitor (UNEXAMINED): no daily public series of an emerging-market sovereign spread was identified for this project: a gap in the search and not a finding about the premium
- Datos: Alpaca SIP, MEASURED in the instrument census of 2026-10-03: EMB first monthly bar 2016-01-01, liquid by the census floor, shortable; IEF first monthly bar 2016-01-01, liquid by the census floor, shortable

**mortgage premium, via MBB against IEF**

- Pagador: homeowners, whose option to prepay investors are paid to bear.
- Rango: RECALLED, wide and weaker than any range measured on this platform; an input to a feasibility test and never evidence. The excess return of agency mortgage securities over Treasuries is small and mostly compensation for the prepayment option. The expression is a PAIR: long the credit fund against the ten-year Treasury fund at equal notional, as the investment-grade pair already measured here, which requires a short
- Monitor (UNEXAMINED): the mortgage rate is published WEEKLY, so a decision latency of days is built in, and whether the spread leads the pair's returns has not been tested
- Datos: Alpaca SIP, MEASURED in the instrument census of 2026-10-03: MBB first monthly bar 2016-01-01, liquid by the census floor, shortable; IEF first monthly bar 2016-01-01, liquid by the census floor, shortable

**municipal bond premium, via MUB against IEF**

- Pagador: municipal issuers and the tax code, which pay investors through a tax exemption.
- Rango: RECALLED, wide and weaker than any range measured on this platform; an input to a feasibility test and never evidence. Before tax, the excess of municipal over Treasury bonds is near zero; the benefit is the exemption, which a tax-free account cannot use and which the platform's pre-tax returns do not count. The expression is a PAIR: long the credit fund against the ten-year Treasury fund at equal notional, as the investment-grade pair already measured here, which requires a short
- Monitor (UNEXAMINED): no daily public series of that ratio was identified: a gap in the search and not a finding about the premium
- Datos: Alpaca SIP, MEASURED in the instrument census of 2026-10-03: MUB first monthly bar 2016-01-01, liquid by the census floor, not shortable; IEF first monthly bar 2016-01-01, liquid by the census floor, shortable

**preferred securities premium, via PFF held long**

- Pagador: banks and other issuers, who pay for loss-absorbing capital that ranks below debt and above common stock.
- Rango: RECALLED, wide and weaker than any range measured on this platform; an input to a feasibility test and never evidence. Preferred funds carry interest-rate duration and a large equity beta, so most of what they earn is exposure already measured under other names, and 2022 was a heavy loss for rates and for financials together
- Monitor (UNEXAMINED): a gap in the search and not a finding; if none exists the candidate falls under the same no-monitor ruling as the equity premium
- Datos: Alpaca SIP, MEASURED in the instrument census of 2026-10-03: PFF first monthly bar 2016-01-01, liquid by the census floor, shortable

**inflation risk premium, via TIP against IEF**

- Pagador: investors who pay for protection against inflation, through a lower yield than a nominal bond.
- Rango: RECALLED, wide and weaker than any range measured on this platform; an input to a feasibility test and never evidence. The inflation risk premium in breakevens is small and changes sign, and 2021 to 2022 rewarded the protection heavily, so a window containing it is flattering. The expression is a PAIR: long the credit fund against the ten-year Treasury fund at equal notional, as the investment-grade pair already measured here, which requires a short
- Monitor (UNEXAMINED): published daily with a long history, but its trigger frequency over this window has not been measured and whether its changes lead the pair's returns has not been tested
- Datos: Alpaca SIP, MEASURED in the instrument census of 2026-10-03: TIP first monthly bar 2016-01-01, liquid by the census floor, shortable; IEF first monthly bar 2016-01-01, liquid by the census floor, shortable

**energy futures roll premium, via energy futures funds held SHORT (UNG, BNO)**

- Pagador: holders of long energy futures funds, who pay the roll cost when the curve is in contango.
- Rango: RECALLED, wide and weaker than any range measured on this platform; an input to a feasibility test and never evidence, and the widest of all because the sign is well documented and the tail is the whole story: a short in a contango fund harvests the roll and loses sharply in a squeeze. Needs the short-simple measurement the engine now supports, with a declared borrow cost and adverse move
- Monitor (UNEXAMINED): no daily public series of the curve shape was identified on FRED: a gap in the search and not a finding about the premium
- Datos: Alpaca SIP, MEASURED in the instrument census of 2026-10-03: UNG first monthly bar 2016-01-01, liquid by the census floor, shortable; BNO first monthly bar 2016-01-01, liquid by the census floor, shortable

## 3. Lo que queda fuera, con su razón

- **Metales preciosos:** el oro y la plata no pagan carry, así que no hay pagador y el destino de primas de riesgo no aplica.
- **Cripto:** un solo instrumento con historia suficiente, y los fondos al contado no pagan nada.
- **Inmobiliario, dividendos, baja volatilidad y otros factores:** beta de renta variable o mala valoración con otra etiqueta.
- **Ya en el registro:** carry de divisas, prima de plazo, roll de materias primas, covered calls, venta de puts, volatilidad y crédito de grado de inversión.

## 4. Qué concluye el registro

- De las siete, **ninguna es factible** (ni su extremo conservador alcanza la barra); **dos son inciertas** (crédito de alto rendimiento y venta en corto de futuros de energía) y **cinco se descartan solas**: su extremo optimista queda bajo 0,50.
- Las dos inciertas **requieren cortos**, que la cadena operativa solo puede abrir en PAPER: están bloqueadas para capital real.
- El crédito de alto rendimiento además arrastra un monitor probablemente refutado por el cribado del 3 de octubre.
- **Admisibles en principio: 1 de 22** (DIVO, que sigue pesando en contra por su monitor inferido). Antes eran 3 de 15.
- Un solo cambio desbloquearía a cinco candidatos: permitir cortos con capital real (VIXY, VIXM, carry de divisas, crédito de alto rendimiento y la venta de energía).
