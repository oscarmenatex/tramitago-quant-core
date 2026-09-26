# TramitaGO Quant Core

Plataforma de investigación cuantitativa construida de forma incremental y basada en evidencia:
nada avanza sin evidencia verificable, ningún módulo se adelanta a lo que la fase actual autoriza.
Este repositorio es Fase 0 de la visión completa (DOC-001 §9): investigación simulada, sin
capital real ni conexión a ningún broker.

Los documentos rectores (DOC-001 a DOC-009, Etapa 1/Etapa 2/Etapa 3, y las especificaciones
`M1.x`/`M2.x`/`M3.x`) viven fuera de este repositorio, en una carpeta de documentación local del
propietario del proyecto. No están commiteados aquí; este README resume su contenido relevante
para poder trabajar sin ellos a mano.

## Estado actual (corte 2026-09-26)

| Etapa / Milestone | Estado |
|---|---|
| **Etapa 1** — Mínimo vertical (M1.1–M1.3) | Cerrada en código. Ciclo FORWARD_PAPER completo: adquisición y validación de datos, SMA3 causal, Risk PAPER estricto, activación remota recurrente y no interactiva. **T9** (observación longitudinal de 7 días distintos, 2026-09-21 a 2026-09-27) corriendo en una VM de Oracle Cloud; a un solo slot de cerrar formalmente. |
| **Etapa 2** — Expansión controlada (M2.1–M2.5) | Cerrada en código. Hypothesis con criterio de aceptación predefinido, Experimento histórico y resultado sellado, Research Execution/Result, Disposition + Knowledge (preserva también resultados negativos), retroalimentación controlada completa: PAPER Evidence → Recommendation → Governance Authorization. |
| **Etapa 2.6** — Rigor estadístico (M2.6) | Cerrada en código. Partición walk-forward, ejecución por fold contra baseline buy-and-hold, veredicto agregado (VALIDATED/NOT_VALIDATED/INSUFFICIENT_EVIDENCE), aditivo — nunca reescribe la Disposition de muestra única. |
| **Etapa 3** — Robustez operacional (M3.1–M3.3) | En curso. M3.1 (contrato de estado operacional unificado) y M3.3 (auditoría de M2.x contra ese contrato) cerrados. M3.2 (certificar M1.3) pendiente del cierre real de T9. |

Suite completa: `python -m pytest -q` → 501 tests, 276 subtests, todo en verde.

## Qué NO es este repositorio

No hay Risk Engine cuantitativo (límites de drawdown/exposición), Portfolio (multi-instrumento),
Execution/Broker Adapter real (nunca se conecta a Alpaca ni a ningún broker — toda la operación
PAPER es interna, en `state.json`), ni Fiscalidad. Todo eso está identificado pero no autorizado
todavía (ver documentación local, sección "candidatos posteriores a Etapa 3").

## Arquitectura, por capacidad (DOC-005)

- **Data / M1.2** — adquisición y validación de observaciones públicas de Coinbase para el ciclo
  FORWARD_PAPER.
- **Decision / M1.1** — indicador SMA3 causal y decisión persistida.
- **Risk PAPER / M1.1** — autoridad de transición de estado PAPER (no es un Risk Engine
  cuantitativo).
- **Activación recurrente / M1.3** — política, ledger/lease, recuperación y reintentos acotados,
  status de solo lectura, entrypoint remoto no interactivo (`run-forward-paper-activation`).
- **Research / M2.1–M2.3, M2.6** — Hypothesis, Experimento histórico sellado, Research
  Execution/Result, partición y validación walk-forward.
- **Strategy Evaluation / M2.4-T1, M2.5-T2** — Disposition (juzga contra el criterio propio de la
  Hypothesis) y Recommendation (juzga PAPER Evidence contra límites declarados).
- **Knowledge / M2.4-T2, M2.5-T1** — preserva hipótesis, metodología, resultados (incluidos los
  negativos) e incorpora evidencia PAPER como referencia verificable, nunca como copia.
- **Governance / M2.5-T3** — autoriza o rechaza una nueva versión de configuración; nunca toca la
  configuración vigente ni T9; demostrado en aislamiento.

Todo M2.x se invoca únicamente vía API de Python (no hay subcomandos de CLI para M2.x); la
cobertura de tests en `tests/test_m2*.py` es la referencia de uso de cada función pública.

## Ejecutar la suite de tests

```powershell
python -m pytest -q
```

o, sin dependencias de terceros:

```powershell
python -B -m unittest discover -s tests -v
```

## CLI de `pipeline.py`

Cadena original de adquisición/validación de datos (M1.1, la demostración más antigua del
repositorio):

```powershell
python -B pipeline.py acquire --output artifacts/live-input
python -B pipeline.py run --input artifacts/live-input --output artifacts/live-run-1
python -B pipeline.py run --input artifacts/live-run-1 --output artifacts/live-run-2
python -B pipeline.py compare artifacts/live-run-1 artifacts/live-run-2 --output artifacts/live-comparison
```

Entrypoint remoto no interactivo de M1.3 (el que corre recurrentemente en la VM de Oracle bajo
T9 — **no ejecutar contra una instalación productiva sin saber lo que se hace**; es el mismo
mecanismo que sostiene la observación longitudinal en curso):

```powershell
python -B pipeline.py run-forward-paper-activation --data-dir <ruta> [--now <ISO8601Z>] [--timeout-seconds <n>]
```

Ver `deploy/README.md` para el bootstrap completo (systemd timer, crontab) de una instalación
nueva.

`pipeline.py` expone además subcomandos intermedios de M1.1 (`evaluate`, `observe`, `decide`,
`initialize-paper-session`, `compose-paper-sma3`, `apply-paper-risk`, `run-paper-cycle`, órdenes
PAPER/LIVE controladas, etc.) — cada uno documentado por su propio archivo de test
(`tests/test_m1*.py`).

## Esquema de datos (dataset histórico BTC-USD)

- Fuente única: API pública Coinbase Exchange, endpoint GET de velas.
  [Contrato del proveedor](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles).
- Instrumento único: BTC-USD spot; frecuencia 86400 s (velas diarias UTC).
- Esquema CSV: `instrument` (string), `timestamp` (ISO8601 UTC), `open, high, low, close, volume`
  (float64 finitos), `sma_close_3` (float64 nullable, dos primeras filas nulas por warmup).
  El timestamp identifica el inicio de la vela; OHLCV y la media sólo están disponibles al
  terminar ese día.
- Se rechaza el lote completo ante entrada vacía, campo faltante/no numérico, NaN/infinito,
  timestamp no alineado a medianoche UTC, duplicado, precio no positivo, volumen negativo,
  incoherencia OHLC o día faltante. No se rellenan huecos ni se corrige silenciosamente nada.
- Cada ejecución conserva una copia exacta de `pipeline.py` (SHA256) y su versión de Python como
  baseline ejecutable; misma entrada + configuración + código producen los mismos bytes.

`artifacts/` está en `.gitignore` salvo los datasets de investigación ya sellados bajo
`artifacts/research/` (Hypothesis, Experiment, Result — ver M2.1–M2.3), que sí se versionan por
ser evidencia inmutable de investigación, no salida temporal de ejecución.

## Despliegue

`deploy/` contiene el `systemd` service/timer y el crontab de ejemplo para instalar el
entrypoint de M1.3 en un host persistente (la instalación real corre en una VM de Oracle Cloud,
bajo observación T9). Ver `deploy/README.md`.
