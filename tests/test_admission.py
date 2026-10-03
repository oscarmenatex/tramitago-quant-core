"""ADMISIÓN — the state between a validated Hypothesis and an operating one.

DOC-011 §5 was amended 2026-09-30: crossing Fase 0 → Fase 1 now runs through the
seven gates of §9, and the lifecycle is Validation → VALIDADA → ADMITIDA. The
platform could produce VALIDATED and could not produce ADMITIDA or ADMISIÓN
DENEGADA, so the deliverable Fase 1 actually requires did not exist.
"""

import json
import tempfile
import unittest
from pathlib import Path

import pipeline as p
from tramitago_quant_core.governance.admission import (
    adverse_bound, evaluate_admission_gates, admission_outcome, constitute_admission,
    load_admission, query_admissions, ADMITTED, ADMISSION_DENIED,
    GATE_PASSED, GATE_FAILED, GATE_NOT_EVALUABLE, SURVIVAL_P1, SURVIVAL_P2,
    GATE_SHARPE, GATE_DRAWDOWN, GATE_SURVIVAL, GATE_MONITORABILITY,
    GATE_CAPACITY, GATE_DECISION_COST,
)

REVISION = "a" * 40
AT = "2026-10-02T00:00:00Z"


def _passing_evidence(**overrides):
    evidence = {
        "net_sharpe": adverse_bound("0.61", is_adverse_bound=True,
                                    source="walk-forward bootstrap"),
        "worst_fold_drawdown": adverse_bound("0.11", is_adverse_bound=True,
                                             source="walk-forward folds"),
        "survival": {
            "form": SURVIVAL_P1,
            "out_of_sample_periods": [
                {"period": "2022", "sign_held": True, "criterion_declared_before": True},
                {"period": "2023", "sign_held": True, "criterion_declared_before": True},
            ],
        },
        "monitor": {
            "variable": "funding_rate", "frequency_seconds": 28800,
            "degradation_threshold": "0", "action": "SUSPEND",
            "detection_latency_days": "2", "expected_daily_loss_if_dead": "0.002",
            "is_pnl_only": False, "observes_adverse_state_representatively": True,
        },
        "capacity": {"ceiling": "250000", "current_tranche": "1000"},
        "decision_cost": {
            "build_cost": "2000", "minimum_declared_life_years": "3",
            "recurring_cost_per_year": "200", "expected_annual_net_return": "8000",
            "surveillance_is_automatable": True,
        },
    }
    evidence.update(overrides)
    return evidence


def _states(evidence):
    return {item["gate"]: item["state"] for item in evaluate_admission_gates(evidence)}


class LifecycleTests(unittest.TestCase):
    def test_only_a_validated_hypothesis_reaches_admission(self):
        # Rejection and denial are different states reached at different points.
        with tempfile.TemporaryDirectory() as tmp:
            for outcome in ("NOT_VALIDATED", "INSUFFICIENT_EVIDENCE"):
                with self.assertRaises(ValueError) as caught:
                    constitute_admission(
                        Path(tmp) / "a.json", hypothesis_id="HYPOTHESIS|x",
                        validation_id="STATISTICAL_VALIDATION|y", validation_outcome=outcome,
                        evidence=_passing_evidence(), decided_at=AT, code_revision=REVISION)
                self.assertIn("different states", str(caught.exception))

    def test_all_gates_passing_admits(self):
        gates = evaluate_admission_gates(_passing_evidence())
        outcome, failed, unevaluable = admission_outcome(gates)
        self.assertEqual(outcome, ADMITTED)
        self.assertEqual((failed, unevaluable), ([], []))

    def test_a_single_failure_denies(self):
        evidence = _passing_evidence(
            net_sharpe=adverse_bound("0.20", is_adverse_bound=True, source="x"))
        self.assertEqual(admission_outcome(evaluate_admission_gates(evidence))[0],
                         ADMISSION_DENIED)


class EveryGateIsEvaluatedTests(unittest.TestCase):
    """A denial exists to record reasons, so it records all of them."""

    def test_nothing_short_circuits(self):
        gates = evaluate_admission_gates({})
        self.assertEqual(len(gates), 6)
        self.assertTrue(all(item["state"] == GATE_NOT_EVALUABLE for item in gates))

    def test_two_failures_are_both_reported(self):
        evidence = _passing_evidence(
            net_sharpe=adverse_bound("0.10", is_adverse_bound=True, source="x"),
            worst_fold_drawdown=adverse_bound("0.40", is_adverse_bound=True, source="x"))
        _, failed, _ = admission_outcome(evaluate_admission_gates(evidence))
        self.assertEqual(sorted(failed), sorted([GATE_SHARPE, GATE_DRAWDOWN]))

    def test_failure_and_absence_are_kept_apart(self):
        # "We measured this and it falls short" and "nobody has measured this"
        # are different facts about a project.
        evidence = _passing_evidence(
            net_sharpe=adverse_bound("0.10", is_adverse_bound=True, source="x"))
        del evidence["capacity"]
        _, failed, unevaluable = admission_outcome(evaluate_admission_gates(evidence))
        self.assertEqual(failed, [GATE_SHARPE])
        self.assertEqual(unevaluable, [GATE_CAPACITY])


class AdverseBoundTests(unittest.TestCase):
    """§8.1 — the project's most expensive lesson, enforced structurally."""

    def test_a_point_estimate_fails_rather_than_passing_quietly(self):
        evidence = _passing_evidence(
            net_sharpe=adverse_bound("9.99", is_adverse_bound=False, source="point estimate"))
        gates = {item["gate"]: item for item in evaluate_admission_gates(evidence)}
        self.assertEqual(gates[GATE_SHARPE]["state"], GATE_FAILED)
        self.assertIn("point estimate", gates[GATE_SHARPE]["detail"])

    def test_a_figure_must_say_whether_it_is_a_bound_and_where_it_came_from(self):
        for kwargs in ({"is_adverse_bound": "yes", "source": "x"},
                       {"is_adverse_bound": True, "source": ""}):
            with self.assertRaises(ValueError):
                adverse_bound("0.5", **kwargs)

    def test_the_thresholds_are_the_declared_ones(self):
        self.assertEqual(_states(_passing_evidence(
            net_sharpe=adverse_bound("0.50", is_adverse_bound=True, source="x")))[GATE_SHARPE],
            GATE_PASSED)
        self.assertEqual(_states(_passing_evidence(
            worst_fold_drawdown=adverse_bound("0.15", is_adverse_bound=True,
                                              source="x")))[GATE_DRAWDOWN], GATE_PASSED)
        self.assertEqual(_states(_passing_evidence(
            worst_fold_drawdown=adverse_bound("0.1501", is_adverse_bound=True,
                                              source="x")))[GATE_DRAWDOWN], GATE_FAILED)


class SurvivalTests(unittest.TestCase):
    def test_one_contrary_sign_refutes(self):
        evidence = _passing_evidence(survival={
            "form": SURVIVAL_P1,
            "out_of_sample_periods": [
                {"period": "2022", "sign_held": True, "criterion_declared_before": True},
                {"period": "2023", "sign_held": False, "criterion_declared_before": True},
            ]})
        self.assertEqual(_states(evidence)[GATE_SURVIVAL], GATE_FAILED)

    def test_one_period_is_not_enough(self):
        evidence = _passing_evidence(survival={
            "form": SURVIVAL_P1,
            "out_of_sample_periods": [
                {"period": "2022", "sign_held": True, "criterion_declared_before": True}]})
        self.assertEqual(_states(evidence)[GATE_SURVIVAL], GATE_NOT_EVALUABLE)

    def test_a_criterion_not_declared_beforehand_does_not_count(self):
        evidence = _passing_evidence(survival={
            "form": SURVIVAL_P1,
            "out_of_sample_periods": [
                {"period": "2022", "sign_held": True, "criterion_declared_before": True},
                {"period": "2023", "sign_held": True, "criterion_declared_before": False}]})
        self.assertEqual(_states(evidence)[GATE_SURVIVAL], GATE_NOT_EVALUABLE)

    def test_p2_needs_all_three_in_writing(self):
        complete = {"form": SURVIVAL_P2, "counterparty": "levered longs paying to stay on",
                    "why_they_accept_losing": "they are buying exposure, not yield",
                    "what_would_end_it": "a venue fee change or sustained negative funding"}
        self.assertEqual(_states(_passing_evidence(survival=complete))[GATE_SURVIVAL],
                         GATE_PASSED)
        for key in ("counterparty", "why_they_accept_losing", "what_would_end_it"):
            partial = dict(complete)
            del partial[key]
            self.assertEqual(_states(_passing_evidence(survival=partial))[GATE_SURVIVAL],
                             GATE_NOT_EVALUABLE, key)


class MonitorabilityTests(unittest.TestCase):
    def test_pnl_only_surveillance_never_qualifies(self):
        evidence = _passing_evidence(
            monitor={**_passing_evidence()["monitor"], "is_pnl_only": True})
        self.assertEqual(_states(evidence)[GATE_MONITORABILITY], GATE_FAILED)

    def test_latency_must_beat_the_drawdown_budget(self):
        slow = _passing_evidence(monitor={**_passing_evidence()["monitor"],
                                          "detection_latency_days": "200"})
        self.assertEqual(_states(slow)[GATE_MONITORABILITY], GATE_FAILED)

    def test_a_monitor_that_cannot_see_its_own_adverse_state_fails(self):
        blind = _passing_evidence(
            monitor={**_passing_evidence()["monitor"],
                     "observes_adverse_state_representatively": False})
        self.assertEqual(_states(blind)[GATE_MONITORABILITY], GATE_FAILED)

    def test_all_four_elements_are_required(self):
        for key in ("variable", "frequency_seconds", "degradation_threshold", "action"):
            monitor = dict(_passing_evidence()["monitor"])
            del monitor[key]
            self.assertEqual(_states(_passing_evidence(monitor=monitor))[GATE_MONITORABILITY],
                             GATE_NOT_EVALUABLE, key)


class CapacityAndCostTests(unittest.TestCase):
    def test_a_tranche_above_the_ceiling_fails(self):
        evidence = _passing_evidence(capacity={"ceiling": "500", "current_tranche": "1000"})
        self.assertEqual(_states(evidence)[GATE_CAPACITY], GATE_FAILED)

    def test_an_undeclared_ceiling_is_not_an_infinite_one(self):
        evidence = _passing_evidence(capacity={"current_tranche": "1000"})
        self.assertEqual(_states(evidence)[GATE_CAPACITY], GATE_NOT_EVALUABLE)

    def test_cost_above_a_quarter_of_the_return_fails(self):
        evidence = _passing_evidence(decision_cost={
            **_passing_evidence()["decision_cost"], "expected_annual_net_return": "1000"})
        self.assertEqual(_states(evidence)[GATE_DECISION_COST], GATE_FAILED)

    def test_surveillance_needing_daily_judgement_fails_whatever_the_number(self):
        evidence = _passing_evidence(decision_cost={
            **_passing_evidence()["decision_cost"], "surveillance_is_automatable": False})
        self.assertEqual(_states(evidence)[GATE_DECISION_COST], GATE_FAILED)


class SealingTests(unittest.TestCase):
    def _seal(self, tmp, evidence=None):
        return constitute_admission(
            Path(tmp) / "admissions.json", hypothesis_id="HYPOTHESIS|x",
            validation_id="STATISTICAL_VALIDATION|y", validation_outcome="VALIDATED",
            evidence=evidence if evidence is not None else _passing_evidence(),
            decided_at=AT, code_revision=REVISION)

    def test_a_decision_round_trips_and_recomputes(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = self._seal(tmp)
            self.assertEqual(load_admission(Path(tmp) / "admissions.json",
                                            record["admission_id"]), record)

    def test_the_same_evidence_decides_the_same_way(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._seal(tmp), self._seal(tmp))
            self.assertEqual(
                len(json.loads((Path(tmp) / "admissions.json").read_bytes())["admissions"]), 1)

    def test_an_edited_outcome_no_longer_reproduces(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = _passing_evidence(
                net_sharpe=adverse_bound("0.10", is_adverse_bound=True, source="x"))
            record = self._seal(tmp, evidence)
            path = Path(tmp) / "admissions.json"
            path.write_text(path.read_text("utf-8").replace(
                f'"outcome": "{ADMISSION_DENIED}"', f'"outcome": "{ADMITTED}"'),
                encoding="utf-8")
            with self.assertRaises(ValueError):
                load_admission(path, record["admission_id"])

    def test_denials_can_be_queried_which_is_the_point_of_recording_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seal(tmp, _passing_evidence(
                net_sharpe=adverse_bound("0.10", is_adverse_bound=True, source="x")))
            path = Path(tmp) / "admissions.json"
            self.assertEqual(len(query_admissions(path, ADMISSION_DENIED)), 1)
            self.assertEqual(query_admissions(path, ADMITTED), [])

    def test_a_missing_registry_queries_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(query_admissions(Path(tmp) / "absent.json"), [])


if __name__ == "__main__":
    unittest.main()
