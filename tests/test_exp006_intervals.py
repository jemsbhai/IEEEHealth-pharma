"""
EXP-006 tests: confidence-interval helpers and the v3.1 harness (H6.2).

Validates:
- clopper_pearson gives exact binomial bounds (known values, edge cases)
- mean_ci_halfwidth matches a direct t-interval computation
- MethodResult rows with interval fields serialize to JSON
- run_full_experiment populates intervals that contain the point estimates
  and scores every method's localization through the shared rule
"""

import json
import math
from dataclasses import asdict

import numpy as np
import pytest
from scipy import stats as sp_stats

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import run_experiments as rx
from simulation.simulator import INSULIN


class TestClopperPearson:

    def test_all_successes_n1000(self):
        lo, hi = rx.clopper_pearson(1000, 1000)
        assert hi == 1.0
        assert lo == pytest.approx(0.025 ** (1 / 1000), rel=1e-6)  # closed form for k = n

    def test_no_successes_n1000(self):
        lo, hi = rx.clopper_pearson(0, 1000)
        assert lo == 0.0
        assert hi == pytest.approx(1 - 0.025 ** (1 / 1000), rel=1e-6)  # closed form for k = 0

    def test_interior_value_matches_beta_quantiles(self):
        k, n = 461, 1000
        lo, hi = rx.clopper_pearson(k, n)
        assert lo == pytest.approx(sp_stats.beta.ppf(0.025, k, n - k + 1))
        assert hi == pytest.approx(sp_stats.beta.ppf(0.975, k + 1, n - k))
        assert lo < k / n < hi

    def test_zero_denominator(self):
        assert rx.clopper_pearson(0, 0) == (0.0, 0.0)

    def test_interval_narrows_with_n(self):
        lo_s, hi_s = rx.clopper_pearson(50, 100)
        lo_l, hi_l = rx.clopper_pearson(500, 1000)
        assert (hi_l - lo_l) < (hi_s - lo_s)


class TestMeanCiHalfwidth:

    def test_constant_values_have_zero_halfwidth(self):
        assert rx.mean_ci_halfwidth([0.7] * 1000) == pytest.approx(0.0, abs=1e-12)

    def test_matches_direct_t_interval(self):
        rng = np.random.default_rng(0)
        vals = list(rng.normal(0.9, 0.03, size=1000))
        n = len(vals)
        expected = sp_stats.t.ppf(0.975, n - 1) * np.std(vals, ddof=1) / math.sqrt(n)
        assert rx.mean_ci_halfwidth(vals) == pytest.approx(expected)

    def test_fewer_than_two_values(self):
        assert rx.mean_ci_halfwidth([0.5]) == 0.0
        assert rx.mean_ci_halfwidth([]) == 0.0


class TestMethodResultSerialization:

    def test_row_with_intervals_round_trips_through_json(self):
        row = rx.MethodResult(
            "SL_Algebra", "F3", "Insulin", 1000, 1.0, 0.0, 1.0, 0.977, 0.0002, 0.01, 0.996,
            detection_rate_ci=(0.9963, 1.0), localization_accuracy_ci=(0.9963, 1.0),
            n_detected=1000, mean_score_ci_halfwidth=0.00001,
        )
        text = json.dumps(asdict(row))
        back = json.loads(text)
        assert back["detection_rate_ci"] == [0.9963, 1.0]
        assert back["n_detected"] == 1000
        assert back["mean_score_ci_halfwidth"] == pytest.approx(0.00001)


@pytest.fixture(scope="module")
def suite():
    return rx.run_full_experiment(n_trials=30, seed=42, products=[INSULIN])


class TestHarnessSmoke:

    def test_row_count(self, suite):
        # 8 scenarios (Normal + F1-F7) x 5 methods for one product
        assert len(suite.results) == 40

    def test_intervals_contain_point_estimates(self, suite):
        for r in suite.results:
            if r.scenario == "Normal":
                lo, hi = r.false_positive_rate_ci
                assert lo <= r.false_positive_rate <= hi
            else:
                lo, hi = r.detection_rate_ci
                assert lo <= r.detection_rate <= hi
                if r.n_detected > 0:
                    lo, hi = r.localization_accuracy_ci
                    assert lo <= r.localization_accuracy <= hi
            assert r.mean_score_ci_halfwidth >= 0.0

    def test_la_is_zero_for_normal_and_bounded_for_failures(self, suite):
        for r in suite.results:
            if r.scenario == "Normal":
                assert r.localization_accuracy == 0.0
            else:
                assert 0.0 <= r.localization_accuracy <= 1.0

    def test_n_detected_consistent_with_rates(self, suite):
        for r in suite.results:
            rate = r.false_positive_rate if r.scenario == "Normal" else r.detection_rate
            assert r.n_detected == pytest.approx(rate * r.n_trials)
