"""Render docs/censo-instrumentos-resultado.md from the SEALED census, so the table cannot drift.

    python3.11 -B scripts/research/render_census_result.py

Reads only the sealed census record. The reading of each class is the rule declared before any count
was seen, applied by code (`reading`), and the corrections for known classification noise are listed
apart and labelled as a human reading, never mixed into the sealed numbers.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tramitago_quant_core.research.instrument_census import reading

REGISTRY = REPO / "artifacts" / "research" / "instrument-census.json"
DOC = REPO / "docs" / "censo-instrumentos-resultado.md"

# Classes the rule does not apply to, and why. Declared in docs/censo-instrumentos.md.
ALREADY_MEASURED = {"COVERED_CALL": "medida: familia con veredicto DOES_NOT_GENERALIZE",
                    "VOLATILITY": "medida: SVXY y su monitor refutado",
                    "INVESTMENT_GRADE_CREDIT": "medida: crédito LQD contra IEF"}
EQUITY_BETA = {"DIVIDEND_FACTOR", "OTHER_FACTOR", "LOW_VOLATILITY_FACTOR", "REAL_ESTATE"}

LABEL = {"FAMILY_SIZED": "del tamaño de una familia", "THIN": "fina", "SINGLE_INSTRUMENT": "un solo instrumento"}


def main():
    census = json.loads(REGISTRY.read_bytes())["censuses"][-1]
    rows = census["rows"]
    summary = census["summary"]
    tiers = {t: sum(1 for r in rows if r["tier"] == t) for t in ("A", "B", "C")}
    candidates = [label for label in summary if label not in ALREADY_MEASURED and label not in EQUITY_BETA]
    family_sized = [l for l in candidates if reading(summary[l]["plain_tier_a_liquid"]) == "FAMILY_SIZED"]
    thin = [l for l in candidates if reading(summary[l]["plain_tier_a_liquid"]) == "THIN"]
    lines = [
        "# Censo de instrumentos: resultado",
        "",
        f"Censo sellado `{census['census_id'][:44]}…`, definición `{census['definition_digest'][:12]}` "
        f"(versión {census['definition']['definition_version']}), a fecha {census['asof']}. "
        "Generado desde el registro sellado; **no se leyó ningún retorno**.",
        "",
        "---",
        "",
        "## 1. Qué es el censo",
        "",
        f"{census['assets_active_tradable_total']:,} acciones y fondos negociables listados; "
        f"{census['fund_like_total']:,} con aspecto de fondo; **{census['probed']:,} nombrados en alguna clase y "
        f"sondeados, sin ningún error**. Por historia: {tiers['A']} de nivel A (primera barra el 2018-03-01 o antes), "
        f"{tiers['B']} de nivel B y {tiers['C']} de nivel C.",
        "",
        "## 2. La regla de lectura, declarada antes de ver ningún número, aplicada",
        "",
        "Sobre los instrumentos **simples** (sin apalancados ni inversos), de **historia A** y **líquidos**:",
        "5 o más = del tamaño de una familia; 2 a 4 = fina; 0 o 1 = un solo instrumento.",
        "",
        "### Clases sin medir y que no son beta de renta variable",
        "",
        "| Clase | Nombrados | A y líquidos | De ellos cortables | Lectura | Ejemplos |",
        "|---|---|---|---|---|---|",
    ]
    for label, row in summary.items():
        if label in ALREADY_MEASURED or label in EQUITY_BETA:
            continue
        lines.append(f"| {label} | {row['named']} | {row['plain_tier_a_liquid']} | {row['plain_tier_a_liquid_shortable']} | "
                     f"{LABEL[reading(row['plain_tier_a_liquid'])]} | {', '.join(row['examples'])} |")
    lines += ["", "### Clases ya medidas, y clases que son beta de renta variable (la regla no se les aplica)", "",
              "| Clase | A y líquidos | Por qué queda fuera |", "|---|---|---|"]
    for label, row in summary.items():
        if label in ALREADY_MEASURED:
            lines.append(f"| {label} | {row['plain_tier_a_liquid']} | {ALREADY_MEASURED[label]} |")
        elif label in EQUITY_BETA:
            lines.append(f"| {label} | {row['plain_tier_a_liquid']} | beta de renta variable con otra etiqueta |")
    lines += [
        "",
        "## 3. Lo que el resultado dice",
        "",
        f"**La oferta de instrumentos no es el límite.** {len(family_sized)} clases sin medir y ajenas al beta son del tamaño",
        f"de una familia ({', '.join(family_sized)}), y {len(thin)} son finas ({', '.join(thin)}).",
        "Construir un buscador no se frena por falta de envoltorios.",
        "",
        "## 4. Lo que el resultado no dice, con el ruido ya detectado",
        "",
        "Los recuentos son envoltorios, no ideas, y salen del nombre. Correcciones **humanas**, que no alteran los",
        "números sellados:",
        "",
        "- **Metales preciosos (16):** GLD, IAU, SGOL y PHYS son el mismo oro; SLV y PSLV, la plata. Son **dos o tres ideas**.",
        "- **Duración del Tesoro (26):** TLT, IEF, SHY, BIL y SHV son vencimientos de una misma curva: **una idea** con distintos plazos.",
        "- **Divisas (17):** diez son fondos de renta variable con cobertura de divisa (HEFA, HFXI, HEEM…). Los instrumentos de divisa de verdad son unos **siete**: FXB, FXC, FXE, FXF, FXY, UUP, UDN.",
        "- **Crédito:** HYG aparece también en grado de inversión (la palabra «corporate bond» está en su nombre), y CDC aparece como volatilidad siendo un fondo de dividendos.",
        "- **Cripto:** un solo instrumento con historia A (BITA); el resto es de 2024 en adelante.",
        "",
        "Por eso el censo da una **cota superior**: del orden de diez mecanismos distintos, y buena parte de ellos",
        "(duración, crédito, hipotecas, municipales, preferentes) está correlacionada entre sí por los tipos y el crédito.",
        "",
        "**Tampoco dice** qué clases pagan un Sharpe neto de 0,5 ni si tienen un monitor observable. Esas dos cosas son",
        "las que decidieron el bloqueo, y el censo no las toca.",
        "",
    ]
    DOC.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {DOC.relative_to(REPO)}")


if __name__ == "__main__":
    main()
