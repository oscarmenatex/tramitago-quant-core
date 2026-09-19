# M1.3-T8 — Materializar ejecución remota mínima no interactiva

Objetivo de esta carpeta: que un host remoto persistente (un VPS mínimo)
active el entrypoint de FORWARD_PAPER, termine, y vuelva a esperar — sin
ningún comando manual diario. Nada de lo que sigue selecciona una
plataforma en la nube concreta; solo asume "una máquina Linux con Python
3.11+, cron o systemd, y salida a Internet hacia la API pública de
Coinbase". Lo demás (proveedor, tamaño de instancia, hardening del SO)
queda fuera del alcance de M1.3.

## Qué existe ya en el código (`pipeline.py`)

- `forward_paper_activation_entrypoint(data_dir, now_utc=None, timeout_seconds=30)`
  — la única función que un host remoto necesita llamar repetidamente.
  Resuelve todas las rutas de T1–T6 a partir de un único `data_dir`, crea
  y reutiliza una identidad de host persistida (`owner-id.txt`) para el
  lease, determina el instante actual con el reloj real si no se le pasa
  uno (solo para pruebas), y no conoce SMA3, Risk ni la posición PAPER —
  únicamente llama al entrypoint `attempt_forward_paper_activation` ya
  existente.
- Subcomando CLI: `python3 -B pipeline.py run-forward-paper-activation --data-dir <ruta> [--now <ISO8601Z>] [--timeout-seconds <n>]`.
  Sin entrada interactiva, imprime el resultado como JSON en stdout y
  termina con código 0 para cualquier resultado terminal normal
  (`PASS`, `BLOCKED`, `RECOVERABLE_ERROR`, `RECOVERY_REQUIRED` no son
  fallos del entrypoint en sí — para saber si una activación concreta
  necesita atención, usa `forward_paper_activation_status`, no el código
  de salida).

## Layout fijo de `data_dir`

```
<data_dir>/
  configuration.json          # registro de configuración M1.2 (bootstrap)
  policy.json                 # política de activación T1 (bootstrap)
  session/state.json          # sesión PAPER (bootstrap + M1.1/M1.2)
  invocation.json             # invocación M1.2 (bootstrap)
  owner-id.txt                # identidad de este host (creado solo, no tocar)
  activation-ledger/          # T2: lease/ledger por activación
  activation-receipts/        # T4/T5: recibos e intentos por activación
  dataset.json, selection.json, fixture.json, acceptance.json,
  indicator.json, cycle.json, m12-invocation-result.json   # M1.1/M1.2
  output/                     # evidencia publicada por cada llamada
```

## Paso 0 — Preparación única (no es un comando diario)

Esto se ejecuta **una sola vez** al desplegar, no en cada tick. Es la
única intervención manual que este documento autoriza — no viola
"sin comandos diarios", que se refiere a la operación recurrente:

```bash
cd /opt/tramitago-quant-core
python3 -B - <<'PY'
import pipeline as p
from pathlib import Path

data_dir = Path("/var/lib/tramitago-quant-core/forward-paper")
started_at = "2026-09-19T00:00:00Z"        # ajustar al instante real de arranque
session_id = "PAPER_SESSION|<identidad-unica>"

configuration = p.forward_paper_configuration()
(data_dir / "configuration.json").parent.mkdir(parents=True, exist_ok=True)
(data_dir / "configuration.json").write_bytes(p.encoded({
    "schema_version": p.FORWARD_PAPER_CONFIGURATION_REGISTRY_SCHEMA_VERSION,
    "configurations": [configuration], "session_configurations": [],
}))
p.initialize_paper_session(
    data_dir / "session" / "state.json", data_dir / "session-init",
    session_id, "PAPER", started_at)
p.prepare_forward_paper_activation_policy(
    data_dir / "policy.json", data_dir / "configuration.json")
p.prepare_forward_paper_invocation(
    data_dir / "session" / "state.json", data_dir / "configuration.json",
    data_dir / "invocation.json", session_id, started_at, started_at)
print("bootstrap OK:", data_dir)
PY
```

## Paso 1 — Instalar el disparador (elegir uno)

### Opción A: cron

```bash
crontab deploy/forward_paper_activation.crontab.example
# editar antes REPO_PATH y DATA_DIR dentro del archivo
```

### Opción B: systemd (service + timer)

```bash
sudo cp deploy/forward-paper-activation.service deploy/forward-paper-activation.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now forward-paper-activation.timer
```

Ambas opciones satisfacen literalmente el objetivo de T8: el host activa
el entrypoint, el proceso termina (`Type=oneshot` en systemd; cron ya
ejecuta y termina por diseño), y el propio disparador (cron/systemd timer)
es quien "espera" hasta el siguiente tick — el entrypoint no implementa
ningún bucle ni demonio propio.

## Paso 2 — Verificar sin tocar nada operativo

```bash
python3 -B - <<'PY'
import pipeline as p
status = p.forward_paper_activation_status(
    "/var/lib/tramitago-quant-core/forward-paper/policy.json",
    "/var/lib/tramitago-quant-core/forward-paper/configuration.json",
    "/var/lib/tramitago-quant-core/forward-paper/activation-ledger",
    "/var/lib/tramitago-quant-core/forward-paper/activation-receipts",
    "2026-09-19T12:00:00Z")  # instante actual real
print(status)
PY
```

Esto es puramente de lectura (T6): nunca llama a M1.2, nunca escribe
nada.

## Fuera de alcance aquí (igual que en el resto de M1.3)

Alpaca PAPER/LIVE, credenciales de broker, aplicación móvil, panel
complejo, notificaciones avanzadas, múltiples instancias, multi-worker,
colas distribuidas, alta disponibilidad, autoscaling, Kubernetes,
automatización LIVE. Elegir proveedor de nube, tamaño de instancia y
hardening del sistema operativo es una decisión del operador, no de este
microciclo.
