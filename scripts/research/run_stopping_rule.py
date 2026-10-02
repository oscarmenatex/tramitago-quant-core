"""Execute the search stopping rule, pre-declared 2026-09-30 and never run.

It turns "do we keep looking" from a question of mood into a measured statement,
and it has sat in a document for two days while the search carried on by feel --
through a carry axis, a relative-value scan, an equity scan and twenty merges.

THE POPULATION IS READ FROM THE SEALED RECORD, NOT ASSIGNED BY HAND. The rule
defines a population as the triple (data source x frequency x claim type), and an
Experiment's sealed `conditions` already carries exactly that: `instrument`,
`frequency_seconds` and the `outcome` it measures. Nothing here decides which
hypothesis belongs where; the records do, and a slug whose experiments disagree
about their own triple raises rather than being averaged into one.

WHAT THE RULE CANNOT SAY. Exhausting a population says nothing about any other --
that is section 3.1 and it is the reason the test is per-population rather than
global. And a population below the five-hypothesis minimum gets its bound
reported with BOUND NOT APPLICABLE beside it, because that number is precisely
the artefact the minimum exists to stop anyone reading as a conclusion, and
hiding it would make it harder to see rather than easier.

Reads only sealed evidence. Revisits no verdict.

    python3.11 -B scripts/research/run_stopping_rule.py
"""

import glob
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p
from tramitago_quant_core.governance.evidence_host import require_evidence_host
from tramitago_quant_core.governance.stopping_rule import (
    population_status, STOPPING_RULE_MINIMUM_HYPOTHESES, STOPPING_RULE_THRESHOLD,
    STOPPING_RULE_CONFIDENCE, REASON_EXHAUSTED,
)

OUTPUT = REPO / "artifacts" / "research" / "stopping-rule"

# Declared mappings from what a sealed record says to what the rule calls a
# population. Kept here rather than inferred, because "which asset class is this"
# is a judgement and judgements belong where they can be read.
ASSET_CLASS = {
    "BTC-USD": "crypto", "ETH-USD": "crypto", "ATOM-USD": "crypto",
    "XBTUSD": "crypto", "BTC": "crypto",
    # The low-liquidity sweep of 2026-09-29. Same source and frequency as the
    # rest of the crypto daily population: liquidity was the VARIABLE that sweep
    # tested, not a different data source, so it belongs in the same population
    # rather than four of its own that could never reach the minimum.
    "ANKR-USD": "crypto", "BAND-USD": "crypto", "KNC-USD": "crypto", "MASK-USD": "crypto",
    "SPY": "US equities",
}
CLAIM_TYPE = {
    "return": "directional", "spread_return": "relative value",
    "carry_return": "carry premium",
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _code_revision():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout.strip()


def _claim_type(outcome):
    name = (outcome or {}).get("name", "")
    stem = name.split("_t+")[0] if "_t+" in name else name
    if stem not in CLAIM_TYPE:
        raise SystemExit(f"Unmapped claim type {name!r}; declare it rather than guessing")
    return CLAIM_TYPE[stem]


def _frequency(seconds):
    return {86400: "daily"}.get(seconds, f"{seconds}s")


def _triple(conditions):
    instrument = conditions["instrument"]
    if instrument not in ASSET_CLASS:
        raise SystemExit(f"Unmapped instrument {instrument!r}; declare it rather than guessing")
    return (f"{ASSET_CLASS[instrument]}, {_frequency(conditions['frequency_seconds'])}, "
            f"{_claim_type(conditions.get('outcome'))}")


def _experiment_conditions():
    """experiment_id -> its sealed conditions, from EVERY experiments registry.

    Including the shared legacy one: four of the oldest chains (etapa27, eth,
    horizon5, volume-surge) have no per-slug experiments file because they
    predate that convention, and pairing by filename silently dropped eleven of
    forty-one validations -- a quarter of the evidence the rule is meant to weigh.
    """
    index = {}
    for path in sorted(glob.glob(str(REPO / "artifacts/research/experiments*.json"))):
        data = json.loads(Path(path).read_bytes())
        for record in next(value for value in data.values() if isinstance(value, list)):
            index[record["experiment_id"]] = record["conditions"]
    return index


def _fold_experiments():
    """fold_result_id -> experiment_id, following the sealed reference."""
    index = {}
    for path in sorted(glob.glob(
            str(REPO / "artifacts/research/walk-forward-fold-results*.json"))):
        data = json.loads(Path(path).read_bytes())
        for record in next(value for value in data.values() if isinstance(value, list)):
            index[record["fold_result_id"]] = record["reference"]["experiment"]["experiment_id"]
    return index


def _validation_population(validation, folds, conditions):
    """The population a validation belongs to, followed through its own
    references rather than guessed from a filename.

    Every fold must agree: a validation spanning populations would make the rate
    it contributes to meaningless, and averaging across them is exactly what
    section 3.1 forbids.
    """
    triples = set()
    for reference in validation["reference"]["fold_results"]:
        experiment = folds.get(reference["fold_result_id"])
        if experiment is None or experiment not in conditions:
            raise SystemExit(
                f"Cannot resolve {reference['fold_result_id']} to an Experiment; the rule "
                f"must not weigh evidence it cannot classify")
        triples.add(_triple(conditions[experiment]))
    if len(triples) != 1:
        raise SystemExit(f"A validation spans populations {sorted(triples)}")
    return triples.pop()


def main():
    require_evidence_host(REPO)
    conditions = _experiment_conditions()
    folds = _fold_experiments()
    tally = {}
    for path in sorted(glob.glob(str(REPO / "artifacts/research/statistical-validations*.json"))):
        slug = Path(path).stem.replace("statistical-validations-", "").replace(
            "statistical-validations", "(shared)")
        for validation in json.loads(Path(path).read_bytes())["validations"]:
            population = _validation_population(validation, folds, conditions)
            entry = tally.setdefault(population, {"hypotheses": 0, "passing": 0, "usable": 0,
                                                  "slugs": set()})
            entry["slugs"].add(slug)
            entry["hypotheses"] += 1
            for fold in validation["fold_summaries"]:
                if fold["criterion_result"] == "INCONCLUSIVE":
                    continue
                entry["usable"] += 1
                if fold["criterion_result"] == "MET" and fold["beats_baseline"] is True:
                    entry["passing"] += 1

    statuses = [population_status(
        population=name, hypotheses=data["hypotheses"],
        passing_folds=data["passing"], usable_folds=data["usable"])
        for name, data in sorted(tally.items())]

    measurement = {
        "kind": "stopping-rule-execution",
        "schema_version": "1",
        "measured_at": _now(),
        "code_revision": _code_revision(),
        "minimum_hypotheses": STOPPING_RULE_MINIMUM_HYPOTHESES,
        "threshold": STOPPING_RULE_THRESHOLD,
        "confidence": STOPPING_RULE_CONFIDENCE,
        "populations": statuses,
        "slugs_by_population": {name: sorted(data["slugs"])
                                for name, data in sorted(tally.items())},
        "exhausted": sorted(s["population"] for s in statuses if s["exhausted"]),
        "still_open": sorted(s["population"] for s in statuses if not s["exhausted"]),
    }
    measurement["measurement_id"] = "STOPPING_RULE|" + p.digest(p.encoded(
        {k: v for k, v in measurement.items() if k not in ("measured_at", "code_revision")}))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "measurement.json").write_bytes(p.encoded(measurement))

    print(f"minimum {STOPPING_RULE_MINIMUM_HYPOTHESES} hypotheses | threshold "
          f"{STOPPING_RULE_THRESHOLD} | confidence {STOPPING_RULE_CONFIDENCE}\n")
    print(f"{'population':<36} {'hyp':>4} {'folds':>9} {'rate':>7} {'95% up':>7}  verdict")
    print("-" * 96)
    for status in statuses:
        folds = f"{status['passing_folds']}/{status['usable_folds']}"
        verdict = ("EXHAUSTED BY MEASUREMENT" if status["exhausted"]
                   else "bound NOT APPLICABLE, below the minimum"
                   if not status["bound_is_applicable"]
                   else "still open: the bound reaches the threshold")
        print(f"{status['population']:<36} {status['hypotheses']:>4} {folds:>9} "
              f"{float(status['rate']):>7.3f} {float(status['upper_bound_95']):>7.3f}  {verdict}")
    print("-" * 96)
    print(f"{sum(s['hypotheses'] for s in statuses)} validations classified, "
          f"{sum(s['usable_folds'] for s in statuses)} usable folds")
    print(f"\nexhausted:  {', '.join(measurement['exhausted']) or 'none'}")
    print(f"still open: {', '.join(measurement['still_open']) or 'none'}")
    print("\nExhausting one population says nothing about any other -- that is section 3.1, "
          "and\nit is why the test is per-population rather than global.")
    print(f"\nsealed  {measurement['measurement_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
