"""Retrospective multiplicity audit: what does the WHOLE search look like?

Thirty-seven sealed Statistical Validations exist, and each was judged on its own
merits. Only fourteen carried any correction for having been tried alongside
others, and those fourteen were corrected for their own batch of eight -- never
for the search they are part of. Twenty-three were judged as though they were the
only idea anyone ever had.

That is precisely the laundering Bounded Discovery was built to prevent, and it
has been sitting in the project's own history the whole time. This measures it.

Nothing is fetched and no verdict is revisited. The sealed Dispositions stand;
this reads them and asks a question none of them could answer alone. Its own
output is sealed with a content hash and lists every validation it consumed, so
it can be re-derived without network.

    python3.11 -B scripts/research/run_multiplicity_audit.py
"""

import glob
import json
import math
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pipeline as p

SOURCES = "artifacts/research/statistical-validations*.json"
OUTPUT = REPO / "artifacts" / "research" / "multiplicity-audit"
BASE_SIGNIFICANCE = 0.05


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _code_revision():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True, check=True).stdout.strip()


def _load():
    loaded = []
    for path in sorted(glob.glob(str(REPO / SOURCES))):
        for record in json.loads(Path(path).read_bytes())["validations"]:
            loaded.append((Path(path).stem.replace("statistical-validations-", ""), record))
    return loaded


def _binomial_tail(passing, usable, probability=0.5):
    """P(X >= passing) for X ~ Binomial(usable, probability)."""
    return sum(math.comb(usable, k) * probability ** k * (1 - probability) ** (usable - k)
               for k in range(passing, usable + 1))


def _usable(validation):
    return [fold for fold in validation["fold_summaries"]
            if fold["criterion_result"] != "INCONCLUSIVE"]


def _passing(folds):
    return sum(1 for fold in folds
               if fold["criterion_result"] == "MET" and fold["beats_baseline"] is True)


def main():
    loaded = _load()
    folds = [fold for _, validation in loaded for fold in validation["fold_summaries"]]
    usable_folds = [fold for fold in folds if fold["criterion_result"] != "INCONCLUSIVE"]

    # The compound fold gate is "criterion MET and beats the baseline". Whether
    # the second half ever excluded anything is a question about the apparatus,
    # not about any hypothesis, and nothing had asked it.
    baseline_ever_binds = any((fold["criterion_result"] == "MET") != (fold["beats_baseline"] is True)
                              for fold in folds)

    passing_folds = sum(1 for fold in usable_folds
                        if fold["criterion_result"] == "MET" and fold["beats_baseline"] is True)
    fold_pass_rate = passing_folds / len(usable_folds)

    metrics = sorted(fold["metric"] for fold in folds if fold["metric"] is not None)
    positive = sum(1 for value in metrics if value > 0)
    negative = sum(1 for value in metrics if value < 0)

    # How many of these searches would have "validated" by chance alone, under the
    # project's OWN null (each usable fold a fair coin) and under the rate its own
    # folds actually show. The second is not a null -- the observed rate contains
    # whatever signal there is -- it is here to show which direction the fair-coin
    # assumption errs in.
    expected_fair = expected_observed = 0.0
    per_search = []
    for name, validation in loaded:
        usable = _usable(validation)
        passing = _passing(usable)
        needed = math.ceil(float(validation["consistency_threshold"]) * len(usable))
        expected_fair += _binomial_tail(needed, len(usable), 0.5)
        expected_observed += _binomial_tail(needed, len(usable), fold_pass_rate)
        per_search.append({
            "source": name,
            "outcome": validation["outcome"],
            "usable_folds": len(usable),
            "passing_folds": passing,
            "consistency_threshold": validation["consistency_threshold"],
            "binomial_p_value": f"{_binomial_tail(passing, len(usable)):.6f}",
            "corrected_for_a_batch": validation.get("batch") is not None,
            "validation_id": validation["validation_id"],
        })

    observed_validated = sum(1 for _, v in loaded if v["outcome"] == "VALIDATED")
    best = min(per_search, key=lambda item: float(item["binomial_p_value"]))
    corrected_alpha = BASE_SIGNIFICANCE / len(loaded)

    measurement = {
        "kind": "retrospective-multiplicity-audit",
        "schema_version": "1",
        "measured_at": _now(),
        "code_revision": _code_revision(),
        "searches": len(loaded),
        "searches_corrected_for_a_batch": sum(1 for item in per_search
                                              if item["corrected_for_a_batch"]),
        "folds_total": len(folds),
        "folds_usable": len(usable_folds),
        "fold_pass_rate": f"{fold_pass_rate:.6f}",
        "baseline_gate_ever_binds": baseline_ever_binds,
        "fold_metric_mean": f"{math.fsum(metrics) / len(metrics):.9f}",
        "fold_metric_median": f"{metrics[len(metrics) // 2]:.9f}",
        "fold_metric_positive": positive,
        "fold_metric_negative": negative,
        "outcomes": dict(sorted(Counter(v["outcome"] for _, v in loaded).items())),
        "validated_observed": observed_validated,
        "validated_expected_fair_coin": f"{expected_fair:.4f}",
        "validated_expected_at_observed_fold_rate": f"{expected_observed:.4f}",
        "best_binomial_p_value": best["binomial_p_value"],
        "best_source": best["source"],
        "bonferroni_alpha_for_the_whole_search": f"{corrected_alpha:.8f}",
        "best_clears_the_whole_search": float(best["binomial_p_value"]) <= corrected_alpha,
        "per_search": sorted(per_search, key=lambda item: item["source"]),
    }
    measurement["measurement_id"] = "MULTIPLICITY_AUDIT|" + p.digest(p.encoded(
        {k: v for k, v in measurement.items() if k not in ("measured_at", "code_revision")}))

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "measurement.json").write_bytes(p.encoded(measurement))

    print(f"searches          {measurement['searches']}  "
          f"({measurement['searches_corrected_for_a_batch']} ever corrected for a batch, "
          f"none for the whole search)")
    print(f"outcomes          {json.dumps(measurement['outcomes'])}")
    print(f"folds             {measurement['folds_usable']} usable of {measurement['folds_total']}")
    print(f"fold pass rate    {measurement['fold_pass_rate']}  "
          f"(the sealed binomial test assumes 0.5)")
    print(f"baseline gate     {'binds somewhere' if baseline_ever_binds else 'NEVER binds -- it has excluded nothing, ever'}")
    print(f"fold metric       mean {measurement['fold_metric_mean']}  "
          f"median {measurement['fold_metric_median']}  "
          f"{positive} positive / {negative} negative")
    print()
    print(f"VALIDATED observed            {measurement['validated_observed']}")
    print(f"  expected by chance alone    {measurement['validated_expected_fair_coin']} "
          f"(fair coin per fold)")
    print(f"  expected at the observed    {measurement['validated_expected_at_observed_fold_rate']} "
          f"fold rate")
    print()
    print(f"best binomial p across all    {measurement['best_binomial_p_value']} "
          f"({measurement['best_source']})")
    print(f"alpha for {measurement['searches']} searches          "
          f"{measurement['bonferroni_alpha_for_the_whole_search']}")
    print(f"clears the whole search       {measurement['best_clears_the_whole_search']}")
    print()
    print(f"sealed            {measurement['measurement_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
