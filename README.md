# TramitaGO Quant Core — MVP de datos de Fase 1

Implementación de un microciclo, sin código del legado. Autoridades:
[DOC-011](https://docs.google.com/document/d/19S9qrUpSk2dnS5v3yk2-smSBFwMlUIdsd3GLgUr_II4/edit) y
[procedimiento de Fase 1](https://docs.google.com/document/d/1oTiZuqREzslP52UnXNM8rRG5t0-4Fjz5NWbxK4yFShU/edit).
La declaración histórica del procedimiento sobre capacidades ya existentes no
describe Core: este trabajo comenzó con el repositorio vacío, sin commits.

## Decisiones de esta demostración

- Runtime: Python 3.11 o posterior, sólo biblioteca estándar; no requiere instalación de paquetes.
- Fuente única: API pública Coinbase Exchange, endpoint GET de velas BTC-USD.
  [Contrato del proveedor](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles).
- Instrumento único: BTC-USD spot de ese mercado; no representa todo el mercado Bitcoin.
- Frecuencia: 86400 segundos. Diez intervalos UTC completos, desde 2024-01-01
  inclusive hasta 2024-01-11 exclusive. No se solicita una vela abierta.
- Entrada: bytes JSON originales y metadatos de captura con URL, parámetros,
  fecha, tipo de evidencia y SHA256. No requiere credenciales ni cuenta.
- Esquema CSV v1: `instrument` (string), `timestamp` (ISO8601 UTC),
  `open, high, low, close, volume` (float64 finitos), `sma_close_3` (float64 nullable).
  Precios en USD por BTC; volumen en BTC. No se agregan ni ajustan precios.
- El timestamp identifica el inicio de la vela. OHLCV y la media sólo están
  disponibles al terminar ese día, nunca en su inicio.
- Indicador único: media aritmética de los tres cierres de las velas t-2, t-1, t.
  Las dos primeras filas tienen valor nulo (campo CSV vacío); no se imputan.
  Cálculo con `math.fsum(close / 3)` y serialización float de Python.
  No hay señales, posiciones ni estrategia.

## Políticas y límites

Se normalizan el orden de campos del proveedor, tipos y timestamps; se ordenan
las filas cronológicamente. La fuente puede devolver velas fuera de la ventana:
se excluyen explícitamente y se cuentan. No se eliminan duplicados silenciosamente.
Se rechaza el lote completo ante entrada vacía, campo faltante/no numérico,
NaN/infinito, timestamp no alineado a medianoche UTC, duplicado, precio no positivo,
volumen negativo, incoherencia OHLC o día faltante. No se rellenan huecos.
El proveedor advierte que puede omitir intervalos sin operaciones: aquí un hueco
impide demostrar este dataset. La comprobación de cobertura no demuestra por sí
sola la exhaustividad de operaciones dentro de cada vela; se conserva la
agregación histórica del proveedor como entrada, sin auditoría de ticks.

`validation.json` separa recibidas, excluidas por rango, rechazadas por fila,
válidas retenidas por el rechazo del lote y aceptadas para publicación. Ante fallo
de validación sólo se producen informes de fallo, nunca `dataset.csv` ni un
manifest PASS. Una entrada cuya huella o configuración no coincide se rechaza
antes de producir resultados. Los hashes detectan cambios accidentales; no son
firmas de autenticidad ante una alteración coordinada de datos y metadatos.

No hay commit inicial: cada ejecución conserva una copia exacta de `pipeline.py`
y su SHA256 como baseline ejecutable, además de versión de Python. La misma
entrada, configuración y código producen los mismos bytes. Los directorios
existentes con bytes idénticos no se reescriben; contenidos diferentes o
incompletos provocan un fallo sin sobrescritura. Un fallo de disco puede dejar
un directorio parcial, que no constituye una ejecución válida sin manifest e
integridad correctos.

## Ejecución desde la raíz del repositorio

Pruebas offline (fixtures sintéticas definidas en el código de pruebas):

```powershell
python -B -m unittest discover -s tests -v
```

Adquisición operacional controlada, una única petición HTTPS con timeout de 30 s:

```powershell
python -B pipeline.py acquire --output artifacts/live-input
python -B pipeline.py run --input artifacts/live-input --output artifacts/live-run-1
python -B pipeline.py run --input artifacts/live-run-1 --output artifacts/live-run-2
python -B pipeline.py compare artifacts/live-run-1 artifacts/live-run-2 --output artifacts/live-comparison
```

No ejecutar los pasos posteriores si el anterior falla. `acquire` conserva la
entrada, pero no declara que sea válida. Un fallo de red queda en
`acquisition_failure.json` y retorna código 1; no demuestra adquisición.
`run` normaliza, valida, deriva la media y publica CSV e informes en una sola
ejecución. La segunda ejecución utiliza únicamente la captura conservada, sin red.
`compare` verifica hashes de archivos y compara esquema, columnas, filas, rango,
validación, indicadores completos, entrada, configuración, código y runtime.
Devuelve código 1 ante divergencia o corrupción.

Cada directorio de ejecución correcta contiene `dataset.csv`, `manifest.json`,
`validation.json`, `raw.json`, `capture.json` y `pipeline_snapshot.py`. El manifest
registra parámetros, esquema, hashes, rango, conteos y todos los valores de la
media para esta muestra pequeña. La comparación queda en `comparison.json`.
Para reproducir con el código conservado puede invocarse `pipeline_snapshot.py`
con los mismos argumentos de `run`. No es necesario descargar nuevamente.

`artifacts/` está ignorado por Git, incluidos capturas, datasets y evidencias.
Las pruebas crean sus temporales dentro de ese directorio y los limpian.
Sólo se pretende versionar `.gitignore`, este README, `pipeline.py` y las pruebas.
Los datos reales no se incluyen como fixture de pruebas automática.

Esta demostración no acredita estabilidad futura de la fuente, otros mercados
o fechas, ni reproducibilidad de una nueva descarga cuyo contenido haya cambiado.
No incluye backtesting, trading ni automatización operativa.

## Evidencia observada del microciclo — 2026-09-12

Python 3.14.3: 19 pruebas offline pasaron. La primera adquisición falló por
restricción de red del sandbox (`artifacts/live-input/acquisition_failure.json`).
Una única petición posterior con acceso de red ampliado tuvo éxito y quedó en
`artifacts/live-input-online/`. Los comandos operacionales exitosos fueron:

```powershell
python -B pipeline.py acquire --output artifacts/live-input-online
python -B pipeline.py run --input artifacts/live-input-online --output artifacts/live-run-1
python -B pipeline.py run --input artifacts/live-run-1 --output artifacts/live-run-2
python -B pipeline.py compare artifacts/live-run-1 artifacts/live-run-2 --output artifacts/live-comparison
```

Resultado: 11 filas recibidas, una excluida por estar fuera del intervalo,
10 aceptadas, cero rechazadas, cero errores. Ocho valores de SMA y dos nulos
iniciales. Las 14 comparaciones pasaron. El CSV de ambas ejecuciones tiene SHA256
`aa647b415a267d4a8a1d75ee5b85e8ae3fd6099863a0f450f8e3fc57d93c2e99`.
El entregable se demostró para esta entrada y configuración concretas. No existe
todavía un commit; no se hizo commit ni push. La evidencia detallada permanece
local en `artifacts/microcycle-evidence.json` y los manifiestos de cada ejecución.
