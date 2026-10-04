# Censo de instrumentos por clase de mecanismo

Primer paso de la opción A del análisis del bloqueo: **medir la oferta antes de construir un buscador.**
El censo está construido y probado con una red simulada; **no se ha ejecutado**.

---

## 1. Qué responde

¿Cuántos instrumentos negociables hay, por clase económica, que se puedan operar, cortar, tengan historia
suficiente y liquidez? Si una clase tiene uno o dos instrumentos, la oferta es el límite y un buscador solo
multiplicaría los mismos pocos. Si tiene veinte, hay una familia posible.

## 2. Qué lee y qué no

| Lee (metadatos) | Nunca |
|---|---|
| nombre, bolsa, si es negociable y cortable | ningún retorno |
| **la fecha** de la primera barra mensual | ninguna barra diaria |
| el volumen en dólares de los últimos meses completos | ningún precio guardado |

Los precios que sirve el broker se usan para multiplicar por el volumen y se descartan: de cada instrumento
solo sale una fecha y un volumen en dólares. Un test verifica que ningún precio llega al registro, y que un
mensaje de error nunca arrastra la petición (que lleva la credencial).

## 3. La definición, fijada antes de ver ningún número

Está en `tramitago_quant_core/research/instrument_census.py` y su huella digital se sella con el resultado, así
que no se puede ensanchar una clase ni mover un umbral después de ver que el recuento decepciona.

- **Veinte clases** por palabras clave del nombre: covered calls, venta de puts, volatilidad, duración del Tesoro,
  inflación, crédito de grado de inversión, alto rendimiento, deuda emergente, municipales, hipotecas, preferentes,
  divisas, materias primas amplias, metales preciosos, futuros de energía, inmobiliario, dividendos, baja
  volatilidad, otros factores y cripto.
- **Historia:** A = primera barra el 2018-03-01 o antes (la ventana del cribado); B = el 2021-10-01 o antes
  (cinco años); C = más reciente.
- **Liquidez:** al menos 20 millones de dólares al mes, unos 1.000.000 al día, mil veces el primer tramo. Es un
  suelo contra lo imposible de operar, no un modelo de capacidad.
- **Productos apalancados e inversos** se cuentan aparte, porque su retorno es un múltiplo de otro.

## 4. Cómo se leerá, declarado antes

Para cada clase **no medida todavía** (se excluyen covered calls, volatilidad y crédito de grado de inversión,
que ya se midieron; y los factores de renta variable, que son beta):

| Instrumentos simples, de historia A y líquidos | Lectura |
|---|---|
| 5 o más | **del tamaño de una familia** |
| 2 a 4 | **fina**: una familia con regla conjunta es posible pero estrecha |
| 0 o 1 | **un solo instrumento**: no puede ser familia (la regla conjunta exige al menos dos miembros) |

Esa lectura solo dice si hay **oferta**. Para decidir qué declarar hará falta además un monitor observable para la
clase y un efecto esperado que alcance la barra, y eso lo dicen otras fuentes (el registro de candidatos), no
el censo.

## 5. Lo que el censo no puede decir

- **Independencia.** Dos instrumentos de una clase pueden ser la misma apuesta en dos envoltorios, como lo eran
  SPY, XYLD y QYLD. Distinguirlos exige retornos, que el censo no lee. **Cada recuento es una cota superior de
  primas independientes.**
- **Calidad de la clasificación.** Las clases salen del nombre, porque el broker no sirve categoría. Hay falsos
  positivos y falsos negativos: sirve para distinguir 3 de 300, no 12 de 14. Cualquier clase prometedora hay que
  verificarla instrumento a instrumento antes de declarar nada.
- **Tamaño del efecto ni existencia de monitor.**

## 6. Cómo se ejecuta (lo ejecutas tú, en tu máquina local, nunca en la VM)

Primero las claves, solo en variables de entorno:

```powershell
$env:ALPACA_PAPER_API_KEY_ID = Read-Host "ALPACA_PAPER_API_KEY_ID"
$secret = Read-Host "ALPACA_PAPER_API_SECRET_KEY" -AsSecureString
$env:ALPACA_PAPER_API_SECRET_KEY = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret))
```

Una prueba con 30 instrumentos (no se sella, solo comprueba claves y ritmo):

```powershell
python3.11 -B scripts\research\census_instruments.py --limit-symbols 30
```

Y el censo completo, unos diez minutos, reanudable:

```powershell
python3.11 -B scripts\research\census_instruments.py
```
