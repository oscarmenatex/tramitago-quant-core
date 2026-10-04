"""The beta decomposition used to judge option B: exact on a known relation, and pinned on the sealed data."""

import contextlib
import importlib.util
import io
import unittest
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "research" / "measure_beta_alternatives.py"


def _load():
    spec = importlib.util.spec_from_file_location("measure_beta_alternatives", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = _load()


def _days(n):
    return [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(n)]


def _quiet(call, *args):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        call(*args)
    return out.getvalue()


class DecompositionTests(unittest.TestCase):
    def test_a_fund_that_is_half_its_underlying_has_beta_one_half_and_no_alpha(self):
        days = _days(300)
        underlying = {d: ((i * 37) % 11 - 5) / 500 for i, d in enumerate(days)}
        fund = {d: 0.5 * v for d, v in underlying.items()}
        text = _quiet(module.decomposition, "FUND", fund, underlying, "BASE")
        self.assertIn("beta 0.500", text)
        self.assertIn("correlation 1.000", text)
        self.assertIn("alpha +0.00%/yr", text)

    def test_an_unrelated_fund_has_no_correlation_with_its_underlying(self):
        days = _days(400)
        underlying = {d: ((i * 37) % 11 - 5) / 500 for i, d in enumerate(days)}
        fund = {d: ((i * 53) % 13 - 6) / 500 for i, d in enumerate(days)}
        text = _quiet(module.decomposition, "FUND", fund, underlying, "BASE")
        self.assertIn("correlation", text)
        correlation = float(text.split("correlation")[1].split()[0])
        self.assertLess(abs(correlation), 0.2)

    def test_the_sharpe_is_annualised_mean_over_deviation(self):
        values = [0.01, -0.005, 0.02, 0.0, 0.01] * 20
        mean, sd = module._stats(values)
        self.assertAlmostEqual(module._sharpe(values), mean / sd * (252 ** 0.5), places=12)


class SealedDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        needed = REPO / "artifacts" / "research" / "datasets" / "variance_premium_xyld" / "dataset.csv"
        if not needed.exists():
            raise unittest.SkipTest("the sealed datasets are not in this checkout")

    def test_the_covered_call_funds_are_diluted_beta_with_no_positive_alpha(self):
        xyld = _quiet(module.part_one)
        beta_xyld = float(xyld.split("XYLD   on SPY")[1].split("beta")[1].split()[0])
        beta_qyld = float(xyld.split("QYLD   on QQQ")[1].split("beta")[1].split()[0])
        self.assertAlmostEqual(beta_xyld, 0.695, places=3)
        self.assertAlmostEqual(beta_qyld, 0.612, places=3)
        for line in xyld.splitlines():
            if "XYLD   on SPY" in line or "QYLD   on QQQ" in line:
                self.assertIn("alpha -", line)                 # negative, and not significant

    def test_the_funds_trail_their_underlying_on_the_days_all_three_share(self):
        text = _quiet(module.part_one)
        line = [l for l in text.splitlines() if "days all three share" in l][0]
        spy = float(line.split("SPY")[1].split()[0])
        xyld = float(line.split("XYLD")[1].split()[0])
        qyld = float(line.split("QYLD")[1].split()[0])
        self.assertGreater(spy, xyld)
        self.assertGreater(spy, qyld)


if __name__ == "__main__":
    unittest.main()
