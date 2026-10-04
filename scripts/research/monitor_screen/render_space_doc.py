"""Render docs/monitor-screen-space.md from space.json, so the document cannot drift from it.

    python3.11 -B scripts/research/monitor_screen/render_space_doc.py

Reads no market data. Refuses a space the validator refuses.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from tramitago_quant_core.research.monitor_screen_space import (
    validate_space, chance_of_passing, expected_false_passes)

SPACE = REPO / "scripts" / "research" / "monitor_screen" / "space.json"
DOC = REPO / "docs" / "monitor-screen-space.md"

FORM_TEXT = {
    "SIGN_AT_OR_BELOW_ZERO": "diferencia en o bajo cero",
    "CHANGE_21D_ABOVE_ZERO": "sube respecto a 21 días antes",
    "ABOVE_TRAILING_MEDIAN_252": "sobre su mediana de 252 días",
}


def main():
    space = validate_space(json.loads(SPACE.read_text(encoding="utf-8")))
    windows = space["windows"]
    exposures = {e["id"]: e for e in space["exposures"]}
    lines = [
        "# Cribado de monitores: espacio candidato",
        "",
        f"**Estado: {space['status']}.** Borrador para revisión del Director. No se ha leído ningún dato "
        "de mercado y nada está sellado. La fuente es `scripts/research/monitor_screen/space.json`; "
        "este documento se genera de él.",
        "",
        "---",
        "",
        "## 1. Para qué sirve",
        "",
        space["purpose"],
        "",
        space["justification"],
        "",
        "## 2. Qué se fija antes de mirar nada",
        "",
        f"- **Ventana de descubrimiento:** {windows['discovery']['start_utc'][:10]} a "
        f"{windows['discovery']['end_exclusive_utc'][:10]}.",
        f"- **Holdout (el escaneo no puede leerlo):** {windows['holdout']['start_utc'][:10]} a "
        f"{windows['holdout']['end_exclusive_utc'][:10]}.",
        f"- **Por qué estas fechas:** {windows['why_these']}",
        f"- **Regla de paso en descubrimiento:** al menos {space['pass_rule']['minimum_usable_folds']} folds "
        f"utilizables de {space['folds']}, con 10 días de estado activo como mínimo en cada uno, y "
        f"consistencia de {space['pass_rule']['consistency_threshold']}. {space['pass_rule']['source']}",
        f"- **Qué mide:** {space['pass_rule']['what_it_tests']}.",
        f"- **Confirmación en el holdout:** {space['holdout_rule']['claim']}. "
        f"{space['holdout_rule']['why_not_the_fold_rule']}.",
        f"- **Retorno de la exposición:** {space['return_definition']}.",
        f"- **Dirección:** {space['adverse_direction']}.",
        f"- **Parámetros fijos, sin buscar:** horizonte de cambio de {space['fixed_parameters']['change_horizon_days']} "
        f"días y mediana de {space['fixed_parameters']['median_window_days']}. {space['fixed_parameters']['why_fixed']}.",
        "",
        "## 3. Variables",
        "",
        "| Variable | Mide | Familia de mecanismo | Disponibilidad |",
        "|---|---|---|---|",
    ]
    for v in space["variables"]:
        lines.append(f"| `{v['series']}` | {v['measures']} | {v['mechanism_family']} | {v['availability']} |")
    lines += [
        "",
        "Todas son diarias, cotizadas, sin revisiones y conocidas al decidir. Una variable que se "
        "revisa o llega tarde no se puede usar para actuar, aunque correlacione.",
        "",
        "## 4. Exposiciones",
        "",
        "| Exposición | Posición | Disponible desde |",
        "|---|---|---|",
    ]
    for e in space["exposures"]:
        lines.append(f"| {e['name']} | {e['instrument']} | {e['available_from']} |")
    lines += [
        "",
        f"## 5. Los {len(space['candidates'])} candidatos",
        "",
        "| | Exposición | Variable | Estado «activo» | Mecanismo declarado |",
        "|---|---|---|---|---|",
    ]
    for c in space["candidates"]:
        lines.append(f"| {c['id']} | {exposures[c['exposure']]['name']} | `{c['variable']}` | "
                     f"{FORM_TEXT[c['form']]} | {c['mechanism']} |")
    chance = chance_of_passing(space["folds"])
    lines += [
        "",
        "## 6. Lo que ya se midió y se cuenta aunque no se vuelva a escanear",
        "",
        "| Variable | Exposición | Resultado | Fuente |",
        "|---|---|---|---|",
    ]
    for p in space["ledger_prior"]:
        lines.append(f"| {p['variable']} | {p['exposure']} | {p['result']} | {p['source']} |")
    lines += [
        "",
        f"{space['multiplicity']['note']} Total contado: **{space['multiplicity']['total_tested_on_this_question']}**.",
        "",
        "## 7. Qué esperar del azar",
        "",
        f"Con {space['folds']} folds, una variable **sin ninguna relación** con los retornos supera la regla de "
        f"descubrimiento el {chance:.1%} de las veces. Con {len(space['candidates'])} candidatos eso son "
        f"**{expected_false_passes(space):.1f} aprobados falsos esperados**. Por eso superar el "
        "descubrimiento no es evidencia: solo lo es confirmar en el holdout un único reclamo declarado antes.",
        "",
        "## 8. Decisiones abiertas",
        "",
    ]
    lines += [f"{i}. {text}" for i, text in enumerate(space["open_decisions"], start=1)]
    lines += [
        "",
        "## 9. Lo que NO se ha hecho",
        "",
        "- No se leyó ninguna serie ni ningún retorno.",
        "- No se selló el espacio: se sella tras tu revisión y tras la sonda de disponibilidad.",
        "- No existe todavía el escáner: Discovery solo reconoce Strategies de precio, volumen y funding.",
        "",
    ]
    DOC.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {DOC.relative_to(REPO)}  ({len(space['candidates'])} candidates, "
          f"{expected_false_passes(space):.1f} expected false passes)")


if __name__ == "__main__":
    main()
