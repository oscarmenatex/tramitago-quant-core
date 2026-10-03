"""Lifecycle status: derived from sealed records, never stored, never written."""

import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tramitago_quant_core.research.lifecycle import (
    derive_status, load_config, add_months, reopening_condition,
    AUTORIZADO, MEDIDO, DENEGADO, NOT_DERIVABLE, DEFAULT_REMEASURE_MONTHS)

REPO = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO / "artifacts" / "research"
H1 = "HYPOTHESIS|11111111-1111-1111-1111-111111111111"
H2 = "HYPOTHESIS|22222222-2222-2222-2222-222222222222"
R3 = "R3 monitorability: latency < drawdown / daily loss if dead"
SHARPE = "maximando: net Sharpe >= 0.50 AND its lower 95% bound > 0"


def _admission(hypothesis_id, outcome, decided, failed=(), unevaluable=()):
    return {"hypothesis_id": hypothesis_id, "outcome": outcome,
            "gates_failed": list(failed), "gates_not_evaluable": list(unevaluable),
            "materialization": {"decided_at": decided}}


class Registries:
    def __init__(self, admissions, families=(), verdicts=()):
        self.root = Path(tempfile.mkdtemp())
        self.admissions = self._write("admissions.json", {"admissions": list(admissions)})
        self.hypotheses = self._write("hypotheses.json", {"hypotheses": [
            {"hypothesis_id": H1, "version": 1, "target_metric": "net_sharpe(ONE)"},
            {"hypothesis_id": H2, "version": 1, "target_metric": "net_sharpe(TWO)"}]})
        self.families = self._write("families.json", {"families": list(families)})
        self.verdicts = self._write("verdicts.json", {"verdicts": list(verdicts)})

    def _write(self, name, content):
        path = self.root / name
        path.write_text(json.dumps(content), encoding="utf-8")
        return path

    def status(self, as_of=date(2026, 10, 3), months=12):
        return derive_status(admissions=self.admissions, hypotheses=self.hypotheses,
                             families=self.families, verdicts=self.verdicts, as_of=as_of,
                             remeasure_months=months)


def _family(*members):
    return {"family_id": "MECHANISM_FAMILY|x", "member_hypotheses": {m: m for m in members}}


def _verdict(verdict, at="2026-10-03T20:00:00.000000Z"):
    return {"family_id": "MECHANISM_FAMILY|x", "verdict": verdict, "reason": "r",
            "decided_at": at}


class StateTests(unittest.TestCase):
    def test_a_denied_hypothesis_says_what_failed_and_what_would_reopen_it(self):
        reg = Registries([_admission(H1, "ADMISION_DENEGADA", "2026-10-03T10:00:00Z",
                                     failed=[R3], unevaluable=[SHARPE])])
        member = reg.status()["members"][0]
        self.assertEqual(member["state"], DENEGADO)
        self.assertEqual(set(member["reopens_with"]), {R3, SHARPE})
        self.assertIn("monitor", member["reopens_with"][R3])
        self.assertEqual(member["label"], "net_sharpe(ONE)")

    def test_admitted_outside_every_family_is_authorised(self):
        reg = Registries([_admission(H1, "ADMITIDA", "2026-10-03T10:00:00Z")])
        self.assertEqual(reg.status()["members"][0]["state"], AUTORIZADO)

    def test_admitted_in_a_family_that_does_not_generalize_is_only_measured(self):
        reg = Registries([_admission(H1, "ADMITIDA", "2026-10-03T10:00:00Z")],
                         families=[_family(H1)], verdicts=[_verdict("DOES_NOT_GENERALIZE")])
        member = reg.status()["members"][0]
        self.assertEqual(member["state"], MEDIDO)
        self.assertFalse(member["family_clearance"]["cleared"])

    def test_admitted_in_a_family_that_generalizes_is_authorised(self):
        reg = Registries([_admission(H1, "ADMITIDA", "2026-10-03T10:00:00Z")],
                         families=[_family(H1)], verdicts=[_verdict("GENERALIZES")])
        self.assertEqual(reg.status()["members"][0]["state"], AUTORIZADO)

    def test_the_latest_admission_governs_not_the_best_one(self):
        reg = Registries([_admission(H1, "ADMITIDA", "2026-10-03T10:00:00Z"),
                          _admission(H1, "ADMISION_DENEGADA", "2026-10-04T10:00:00Z",
                                     failed=[R3])])
        members = reg.status()["members"]
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["state"], DENEGADO)

    def test_counts_and_undeclared_admissions(self):
        reg = Registries([_admission(H1, "ADMISION_DENEGADA", "2026-10-03T10:00:00Z")])
        report = reg.status()
        self.assertEqual(report["counts"], {AUTORIZADO: 0, MEDIDO: 0, DENEGADO: 1})
        self.assertEqual(report["declared_without_admission"], 1)       # H2


class NotInventedTests(unittest.TestCase):
    def test_states_with_no_record_behind_them_are_never_produced(self):
        reg = Registries([_admission(H1, "ADMITIDA", "2026-10-03T10:00:00Z"),
                          _admission(H2, "ADMISION_DENEGADA", "2026-10-03T11:00:00Z")])
        report = reg.status()
        self.assertEqual(set(report["not_derivable"]), set(NOT_DERIVABLE))
        for member in report["members"]:
            self.assertNotIn(member["state"], NOT_DERIVABLE)

    def test_it_writes_nothing(self):
        reg = Registries([_admission(H1, "ADMISION_DENEGADA", "2026-10-03T10:00:00Z")])
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in reg.root.iterdir()}
        reg.status()
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in reg.root.iterdir()}
        self.assertEqual(before, after)


class CadenceTests(unittest.TestCase):
    def test_due_date_follows_the_configured_months_and_flags_overdue(self):
        reg = Registries([_admission(H1, "ADMISION_DENEGADA", "2026-01-15T10:00:00Z")])
        member = reg.status(as_of=date(2026, 12, 1), months=6)["members"][0]
        self.assertEqual(member["remeasure_due"], "2026-07-15")
        self.assertTrue(member["remeasure_overdue"])
        member = reg.status(as_of=date(2026, 12, 1), months=12)["members"][0]
        self.assertFalse(member["remeasure_overdue"])

    def test_month_arithmetic_clamps_to_the_end_of_a_short_month(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2028, 1, 31), 1), date(2028, 2, 29))
        self.assertEqual(add_months(date(2026, 11, 30), 3), date(2027, 2, 28))

    def test_the_cadence_comes_from_a_file_and_defaults_only_when_there_is_none(self):
        root = Path(tempfile.mkdtemp())
        self.assertEqual(load_config(root / "none.json")["remeasure_every_months"],
                         DEFAULT_REMEASURE_MONTHS)
        path = root / "c.json"
        path.write_text('{"remeasure_every_months": 6}', encoding="utf-8")
        self.assertEqual(load_config(path)["remeasure_every_months"], 6)

    def test_an_invalid_cadence_is_refused_not_defaulted(self):
        root = Path(tempfile.mkdtemp())
        for bad in ('{"remeasure_every_months": 0}', '{"remeasure_every_months": "12"}',
                    '{"remeasure_every_months": true}', '{}'):
            path = root / "c.json"
            path.write_text(bad, encoding="utf-8")
            with self.assertRaises(ValueError, msg=bad):
                load_config(path)

    def test_every_gate_has_a_reopening_condition(self):
        for gate in (R3, SHARPE, "R1 drawdown: x", "R2 survival: x", "R4 capacity: x",
                     "R5 decision cost: x", "something unknown"):
            self.assertTrue(reopening_condition(gate))


class RealRecordsTests(unittest.TestCase):
    """Against the sealed records of this repository."""

    @classmethod
    def setUpClass(cls):
        if not (ARTIFACTS / "admissions.json").exists():
            raise unittest.SkipTest("the sealed artifacts are not in this checkout")
        cls.report = derive_status(
            admissions=ARTIFACTS / "admissions.json", hypotheses=ARTIFACTS / "hypotheses.json",
            families=ARTIFACTS / "families.json", verdicts=ARTIFACTS / "family-verdicts.json",
            as_of=date(2026, 10, 3), remeasure_months=12)

    def test_nothing_is_authorised_today(self):
        self.assertEqual(self.report["counts"][AUTORIZADO], 0)

    def test_the_covered_call_funds_are_denied_and_not_cleared_by_their_family(self):
        for symbol in ("XYLD", "QYLD"):
            member = next(m for m in self.report["members"] if f"({symbol} " in m["label"])
            self.assertEqual(member["state"], DENEGADO, symbol)
            self.assertFalse(member["family_clearance"]["cleared"], symbol)
            self.assertIn(R3, member["gates_failed"], symbol)

    def test_every_refusal_carries_a_condition_for_reopening_it(self):
        for member in self.report["members"]:
            for gate in member["gates_failed"] + member["gates_not_evaluable"]:
                self.assertTrue(member["reopens_with"].get(gate), (member["label"], gate))


if __name__ == "__main__":
    unittest.main()
