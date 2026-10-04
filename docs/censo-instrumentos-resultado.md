# Censo de instrumentos: resultado

Censo sellado `INSTRUMENT_CENSUS|a72086424221018a3d53dca7f3…`, definición `6f4d05bbb6a5` (versión 2), a fecha 2026-10-03. Generado desde el registro sellado; **no se leyó ningún retorno**.

---

## 1. Qué es el censo

13,509 acciones y fondos negociables listados; 6,028 con aspecto de fondo; **1,501 nombrados en alguna clase y sondeados, sin ningún error**. Por historia: 596 de nivel A (primera barra el 2018-03-01 o antes), 178 de nivel B y 727 de nivel C.

## 2. La regla de lectura, declarada antes de ver ningún número, aplicada

Sobre los instrumentos **simples** (sin apalancados ni inversos), de **historia A** y **líquidos**:
5 o más = del tamaño de una familia; 2 a 4 = fina; 0 o 1 = un solo instrumento.

### Clases sin medir y que no son beta de renta variable

| Clase | Nombrados | A y líquidos | De ellos cortables | Lectura | Ejemplos |
|---|---|---|---|---|---|
| COMMODITY_BROAD | 32 | 12 | 9 | del tamaño de una familia | PDBC, BCI, GSG, DBC, FTGC, COMT |
| CRYPTO | 90 | 1 | 0 | un solo instrumento | BITA |
| CURRENCY | 23 | 17 | 10 | del tamaño de una familia | EMLC, UUP, HEFA, FXY, HFXI, FXE |
| EMERGING_DEBT | 17 | 4 | 3 | fina | EMB, EBND, FEMB, EMD |
| ENERGY_FUTURES | 12 | 4 | 4 | fina | UNG, BNO, FCG, OILK |
| HIGH_YIELD_CREDIT | 123 | 26 | 14 | del tamaño de una familia | HYG, USHY, SPHY, SHYG, HYD, HYMB |
| INFLATION_LINKED | 61 | 14 | 10 | del tamaño de una familia | TIP, VTIP, SCHP, STIP, LTPZ, SPIP |
| MORTGAGE | 28 | 6 | 4 | del tamaño de una familia | MBB, VMBS, SPMB, IVR, REM, MORT |
| MUNICIPAL | 202 | 46 | 6 | del tamaño de una familia | MUB, SUB, HYD, HYMB, CMF, PZA |
| PRECIOUS_METALS | 53 | 16 | 11 | del tamaño de una familia | GLD, SLV, IAU, PSLV, PHYS, SGOL |
| PREFERRED | 45 | 18 | 9 | del tamaño de una familia | PFF, PGX, FPE, PFFD, VRP, PFXF |
| PUT_WRITE | 1 | 0 | 0 | un solo instrumento |  |
| TREASURY_DURATION | 123 | 26 | 20 | del tamaño de una familia | TLT, BIL, IEF, SHV, SHY, USFR |

### Clases ya medidas, y clases que son beta de renta variable (la regla no se les aplica)

| Clase | A y líquidos | Por qué queda fuera |
|---|---|---|
| COVERED_CALL | 9 | medida: familia con veredicto DOES_NOT_GENERALIZE |
| DIVIDEND_FACTOR | 70 | beta de renta variable con otra etiqueta |
| INVESTMENT_GRADE_CREDIT | 42 | medida: crédito LQD contra IEF |
| LOW_VOLATILITY_FACTOR | 15 | beta de renta variable con otra etiqueta |
| OTHER_FACTOR | 71 | beta de renta variable con otra etiqueta |
| REAL_ESTATE | 22 | beta de renta variable con otra etiqueta |
| VOLATILITY | 4 | medida: SVXY y su monitor refutado |

## 3. Lo que el resultado dice

**La oferta de instrumentos no es el límite.** 9 clases sin medir y ajenas al beta son del tamaño
de una familia (COMMODITY_BROAD, CURRENCY, HIGH_YIELD_CREDIT, INFLATION_LINKED, MORTGAGE, MUNICIPAL, PRECIOUS_METALS, PREFERRED, TREASURY_DURATION), y 2 son finas (EMERGING_DEBT, ENERGY_FUTURES).
Construir un buscador no se frena por falta de envoltorios.

## 4. Lo que el resultado no dice, con el ruido ya detectado

Los recuentos son envoltorios, no ideas, y salen del nombre. Correcciones **humanas**, que no alteran los
números sellados:

- **Metales preciosos (16):** GLD, IAU, SGOL y PHYS son el mismo oro; SLV y PSLV, la plata. Son **dos o tres ideas**.
- **Duración del Tesoro (26):** TLT, IEF, SHY, BIL y SHV son vencimientos de una misma curva: **una idea** con distintos plazos.
- **Divisas (17):** diez son fondos de renta variable con cobertura de divisa (HEFA, HFXI, HEEM…). Los instrumentos de divisa de verdad son unos **siete**: FXB, FXC, FXE, FXF, FXY, UUP, UDN.
- **Crédito:** HYG aparece también en grado de inversión (la palabra «corporate bond» está en su nombre), y CDC aparece como volatilidad siendo un fondo de dividendos.
- **Cripto:** un solo instrumento con historia A (BITA); el resto es de 2024 en adelante.

Por eso el censo da una **cota superior**: del orden de diez mecanismos distintos, y buena parte de ellos
(duración, crédito, hipotecas, municipales, preferentes) está correlacionada entre sí por los tipos y el crédito.

**Tampoco dice** qué clases pagan un Sharpe neto de 0,5 ni si tienen un monitor observable. Esas dos cosas son
las que decidieron el bloqueo, y el censo no las toca.
