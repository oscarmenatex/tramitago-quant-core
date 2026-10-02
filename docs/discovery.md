# Discovery: cómo se usa

Guía práctica de `tramitago_quant_core/research/discovery.py`. Ejemplo real y
ejecutable: [`scripts/research/run_carry_exit_discovery.py`](../scripts/research/run_carry_exit_discovery.py).

---

## 1. Qué es, en una frase

Discovery es la casilla **Idea** del ciclo oficial de DOC-004 §4, que la
plataforma se saltó durante 39 hipótesis. Produce **observaciones baratas**
(Findings), no veredictos.

La diferencia que lo justifica todo es de coste:

| | coste | reversible |
|---|---|---|
| **Discovery** → un Finding | segundos | sí, sin huella |
| **Research** → un veredicto sellado | un ciclo completo | **no** |

Sin Discovery, descartar una idea cuesta lo mismo que validarla.

---

## 2. Qué le pasas

Un **espacio declarado**, y nada más. Siete campos, todos obligatorios:

```python
space = constitute_discovery_space(
    "artifacts/discovery/discovery-spaces.json",

    justification = "por qué este espacio y no otro, por escrito",
    candidates    = [ {"strategy_id": ..., "parameters": {...}}, ... ],
    discovery_window = {"start_utc": ..., "end_exclusive_utc": ...},
    holdout_window   = {"start_utc": ..., "end_exclusive_utc": ...},
    minimum_support  = 30,
    minimum_minority_state_frequency = "0.20",
    declared_by = "quién", declared_at = "cuándo",
)
```

**`candidates`** — cada uno nombra una Strategy que la maquinaria sellada sepa
reconstruir (las de `_EXPERIMENT_STRATEGY_CONSTRUCTORS`). Es un límite
deliberado: Discovery no puede proponer algo que la validación no pueda
re-derivar después. Si mides pares o varias series, cada candidato lleva además
`"series": "ETH-USD/SOL-USD"`, y entonces pasas las filas como `{serie: filas}`.

**`holdout_window`** — obligatorio y **disjunto**. No hay flag para saltarlo.

**`minimum_support`** — mínimo de filas en el grupo más pequeño. Una media sobre
seis observaciones no es una observación de nada.

**`minimum_minority_state_frequency`** — el gate de representatividad. Si el
grupo raro es demasiado raro, ningún monitor podría observarlo nunca, así que el
candidato es **inadmisible de entrada, no débil**.

> Los dos umbrales se sellan **aquí**, en la identidad del espacio. No se le
> pasan al scan. Así no se pueden relajar después de ver a quién excluyen.

---

## 3. Cómo se ejecuta

Tres llamadas, y **el orden es parte del contrato**:

```python
space = constitute_discovery_space(...)          # 1. SELLAR, antes de tocar datos
rows  = cargar_filas()                           # 2. cargar, después
observations = scan_discovery_space(space, rows) # 3. medir todos los candidatos

scan = constitute_discovery_scan(
    "artifacts/discovery/discovery-scans.json",
    space=space, observations=observations,
    rows_digest=discovery_rows_digest(rows),     # ancla al dato
    scanned_at=..., scan_code_revision=...)

finding = finding_from_scan(                     # 4. opcional: el mejor pasa a Finding
    "artifacts/discovery/findings.json",
    scan=scan, space=space, rank=0, created_by=..., created_at=...)
```

Declarar el espacio **después** de mirar los datos es el fallo que esta capa
entera existe para impedir, y es el único que el sello no puede detectar: ocurre
antes de que el espacio se escriba. Por eso el runner sella primero y descarga
después, y por eso conviene que tus runners hagan lo mismo.

Y corre así:

```bash
python -B scripts/research/run_carry_exit_discovery.py
```

Es re-ejecutable: el espacio, el scan y el Finding son idempotentes. Volver a
correrlo devuelve las mismas identidades y no duplica nada.

---

## 4. Cómo validas que funciona

Cuatro comprobaciones, de la más barata a la más fuerte.

**a) La suite.** 50 tests cubren el módulo:

```bash
python -m pytest tests/test_discovery.py -q
```

**b) El scan se niega a leer el holdout.** No es una convención, levanta
excepción. Pásale filas que crucen la frontera y tiene que fallar:

```bash
python -B -c "
import sys; sys.path.insert(0,'.')
import pipeline as p
from tramitago_quant_core.research.discovery import load_discovery_space, scan_discovery_space
space = load_discovery_space('artifacts/discovery/discovery-spaces.json', '<space_id>')
fuera = [{'timestamp': space['holdout_window']['start_utc'], 'close': 1.0}]
try:
    scan_discovery_space(space, fuera); print('MAL: lo aceptó')
except ValueError as e:
    print('BIEN:', e)
"
```

**c) Todo artefacto se re-deriva de su propio registro.** Nada se confía:

```bash
python -B -c "
import sys, json; sys.path.insert(0,'.')
from pathlib import Path
from tramitago_quant_core.research.discovery import _discovery_scan_record_is_valid
d = json.loads(Path('artifacts/discovery/discovery-scans.json').read_bytes())
print(all(map(_discovery_scan_record_is_valid, d['scans'])), len(d['scans']), 'scans')
"
```

**d) La prueba que de verdad convence: un candidato que SABES roto.** Mete en el
espacio un diseño ya documentado como degenerado y mira si el filtro lo rechaza
solo. El ejemplo lo hace con `CARRY_FUNDING_THRESHOLD`, el diseño de la #37:

```
  THRESHOLD(0)    minoría 0.030   353/11    NO
  REFUSED: support 11 below the declared minimum 30
```

Lo rechazó en segundos. La #37 gastó un ciclo de validación sellado para llegar
a la misma conclusión.

---

## 5. Cómo funciona por dentro

Para cada candidato, sobre las filas de la ventana de descubrimiento:

1. **Clasifica** con el contrato Strategy → `UPPER` / `LOWER_OR_EQUAL`.
2. **Mide** el Outcome que la Strategy declara (retorno de cierre, de spread o de
   carry) y calcula `media(UPPER) − media(LOWER)`.
3. **Filtra**: soporte y frecuencia del estado minoritario.
4. **Ordena** por efecto descendente, con desempate por orden declarado.
5. **Sella** las observaciones de **todos** los candidatos, incluidos los
   rechazados y su motivo.

Lo que el Finding se lleva consigo:

```
CANDIDATES_EXAMINED|8          ← la multiplicidad, sin la cual todo esto blanquea
SELECTED_RANK|0
SCAN_EFFECT_SPREAD|worst=…|median=…|positive=…|negative=…
MINORITY_STATE_FREQUENCY|…
ROWS_SHA256|…                  ← qué filas produjeron el número
HOLDOUT_UNREAD|…
```

### Cómo se lee el resultado

**La dispersión importa más que el ganador.** Un reparto simétrico alrededor de
cero es la firma del ruido: bajo un mecanismo real la distribución está sesgada,
sin él está centrada. Por eso el resumen lleva `worst_effect`, `median_effect` y
el reparto positivos/negativos, y por eso el Finding los dice en su propio texto.

Tres scans hasta hoy:

| espacio | N | reparto | lectura |
|---|---|---|---|
| OHLCV diario (eje medido muerto) | 17 | 10 + / 7 − | centrado — ruido |
| valor relativo, 6 pares | 18 | 8 + / 10 − | centrado — ruido |
| reglas de salida del carry | 8 | **7 + / 0 −** | sesgado — merece el siguiente paso |

**Pero el conteo sellado sobreestima las miradas independientes.** Siete ventanas
sobre la misma serie son señales casi duplicadas: que las siete coincidan en
signo **no son siete confirmaciones**. La amplitud efectiva no está construida
todavía, y hasta que lo esté, ese "7 de 7" se lee como una, no como siete.

---

## 6. Lo que Discovery NO hace

- **No valida nada.** Un Finding no es evidencia y no llega a un comité.
- **No promociona.** Pasar un Finding a Hipótesis son dos pasos manuales: poner
  `FINDING|<id>` en la procedencia de la Hipótesis, y luego `promote_finding(...)`.
  Automatizarlo sobre los mismos datos que produjeron la observación recrearía
  el data snooping que toda la separación existe para impedir.
- **No inventa.** Enumera dentro de un espacio que alguien declara. El prior
  económico sigue siendo humano.
- **No sabe buscar afirmaciones de nivel.** Solo compara dos grupos. Una prima de
  riesgo es una afirmación sobre una posición, y para eso está
  `level_claim.py` — al que Discovery hoy no alimenta.
- **No va en un timer.** Es episódico: un espacio declarado cada vez.

---

## 7. Receta mínima

1. Escribe la justificación **antes** de mirar nada.
2. Elige candidatos por razones estructurales, nunca por correlación medida —
   eso contaminaría el espacio mismo, y es el único fallo que el sello no ve.
3. Reserva un holdout disjunto y no lo toques.
4. Mete un candidato que sepas roto, para ver al filtro trabajar.
5. Lee la **dispersión**, no el ganador.
6. Si promocionas, corrige por **N**, no por 1.
