"""
EXP-006 tests for scripts/run_metrics.py (H6.3, H6.4, H6.5 wiring).

Validates:
- excursion_scenario passes parameters apply_failure actually reads
  (regression test for the EXP-004 sweep bug)
- counterfeit_scenario injects the requested fraction
- the five-method sweep helper includes B1 with its fixed 0.5 threshold and
  reproduces B1's rule-derived behaviour at 1% and 2% infiltration
- the pre-registered orderings are well-formed and mapped to the right products
- compute_spearman honours the ordering it is given
"""

import numpy as np
import pytest

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import run_metrics as rm
from simulation.simulator import (
    INSULIN, AMOXICILLIN, MRNA_THERAPY, apply_failure, generate_normal_evidence,
)

PATH = rm.EVAL_PATH


def _normal_evidence(seed=3):
    rng = np.random.default_rng(seed)
    return {n: generate_normal_evidence(INSULIN, rng=rng) for n in PATH}, rng


def _out_of_range(ev):
    cc = ev.cold_chain
    return sum(1 for t in cc.readings if t < cc.required_min or t > cc.required_max)


class TestScenarioFactories:

    @pytest.mark.parametrize("duration,expected", [(0.01, 1), (0.05, 5), (0.20, 20), (0.50, 50)])
    def test_excursion_scenario_changes_the_requested_number_of_readings(self, duration, expected):
        """Regression for EXP-004: the sweep must actually vary the excursion."""
        base, rng = _normal_evidence()
        sc = rm.excursion_scenario(duration)
        injected = apply_failure(base, sc, INSULIN, rng=rng)
        assert _out_of_range(base[sc.target_node]) == 0
        assert _out_of_range(injected[sc.target_node]) == expected

    def test_excursion_scenario_uses_the_standard_f1_node_and_magnitude(self):
        sc = rm.excursion_scenario(0.05)
        assert sc.target_node == "distributor_a"
        assert sc.params == {"duration_fraction": 0.05, "magnitude": 2.0}

    def test_excursion_scenario_rejects_nothing_silently(self):
        """Every param key must be one apply_failure reads for F1."""
        sc = rm.excursion_scenario(0.1)
        assert set(sc.params) <= {"duration_fraction", "magnitude"}

    @pytest.mark.parametrize("fraction,expected", [(0.01, 1), (0.02, 2), (0.05, 5), (0.20, 20)])
    def test_counterfeit_scenario_injects_requested_failures(self, fraction, expected):
        base, rng = _normal_evidence()
        sc = rm.counterfeit_scenario(fraction)
        injected = apply_failure(base, sc, INSULIN, rng=rng)
        ser = injected["importer"].serialization
        assert ser.scanned_failure == expected
        assert ser.scanned_success + ser.scanned_failure == ser.total_packages == 100


class TestSweepHelper:

    def test_five_methods_with_b1_fixed_threshold(self):
        methods = rm.sweep_methods(INSULIN)
        names = [m[0] for m in methods]
        assert names == ["SL", "B1", "B2", "B3", "B4"]
        by_name = {m[0]: m for m in methods}
        assert by_name["B1"][3] == 0.5
        assert by_name["B1"][2] is False
        assert by_name["SL"][2] is True
        for n in ("SL", "B2", "B3", "B4"):
            assert by_name[n][3] is None

    def test_detection_rates_cover_all_methods_and_are_bounded(self):
        rates = rm.detection_rates_for_scenario(INSULIN, rm.counterfeit_scenario(0.05), n_trials=20)
        assert set(rates) == {"SL", "B1", "B2", "B3", "B4"}
        for v in rates.values():
            assert 0.0 <= v <= 1.0

    def test_b1_rule_at_one_and_two_percent(self):
        """B1 passes a failure rate of exactly 0.01 and fails 0.02 (rule-derived)."""
        r1 = rm.detection_rates_for_scenario(INSULIN, rm.counterfeit_scenario(0.01), n_trials=20)
        r2 = rm.detection_rates_for_scenario(INSULIN, rm.counterfeit_scenario(0.02), n_trials=20)
        assert r1["B1"] == 0.0
        assert r2["B1"] == 1.0

    def test_failure_severity_sweeps_shape(self):
        out = rm.failure_severity_sweeps(INSULIN, n_trials=5)
        assert set(out) == {"counterfeit_fraction_sweep", "excursion_duration_sweep"}
        assert list(out["counterfeit_fraction_sweep"]) == [str(f) for f in rm.COUNTERFEIT_FRACTIONS]
        assert list(out["excursion_duration_sweep"]) == [str(d) for d in rm.EXCURSION_DURATIONS]
        for level in out["excursion_duration_sweep"].values():
            assert set(level) == {"SL", "B1", "B2", "B3", "B4"}


class TestOrderings:

    def test_crt_ordering_is_a_permutation_of_the_global_one(self):
        assert set(rm.SEVERITY_ORDER_CRT) == set(rm.SEVERITY_ORDER)
        assert sorted(rm.SEVERITY_ORDER_CRT.values()) == list(range(1, 9))
        assert sorted(rm.SEVERITY_ORDER.values()) == list(range(1, 9))

    def test_crt_ordering_matches_the_logbook_pre_registration(self):
        expected = ["Normal", "F6", "F4", "F1", "F7", "F3", "F5", "F2"]
        got = sorted(rm.SEVERITY_ORDER_CRT, key=rm.SEVERITY_ORDER_CRT.get)
        assert got == expected

    def test_product_mapping(self):
        assert rm.severity_order_for(INSULIN) is rm.SEVERITY_ORDER
        assert rm.severity_order_for(MRNA_THERAPY) is rm.SEVERITY_ORDER
        assert rm.severity_order_for(AMOXICILLIN) is rm.SEVERITY_ORDER_CRT

    def test_product_key_strips_trailing_space(self):
        assert rm.product_key(INSULIN) == "Insulin"
        assert rm.product_key(AMOXICILLIN) == "Amoxicil"
        assert rm.product_key(MRNA_THERAPY) == "mRNA-bas"


class TestSpearmanOrdering:

    @staticmethod
    def _raw_from_scores(scores):
        return {"M": {sc: {"scores": [s, s]} for sc, s in scores.items()}}

    def test_perfect_agreement_with_given_ordering(self):
        # Scores that fall exactly with CRT severity
        scores = {sc: 1.0 - 0.1 * rank for sc, rank in rm.SEVERITY_ORDER_CRT.items()}
        res = rm.compute_spearman(self._raw_from_scores(scores), rm.SEVERITY_ORDER_CRT)
        assert res["M"]["rho"] == pytest.approx(1.0)

    def test_same_scores_differ_under_global_ordering(self):
        scores = {sc: 1.0 - 0.1 * rank for sc, rank in rm.SEVERITY_ORDER_CRT.items()}
        res_global = rm.compute_spearman(self._raw_from_scores(scores))
        assert res_global["M"]["rho"] < 1.0

    def test_reversed_scores_give_minus_one(self):
        scores = {sc: 0.1 * rank for sc, rank in rm.SEVERITY_ORDER.items()}
        res = rm.compute_spearman(self._raw_from_scores(scores))
        assert res["M"]["rho"] == pytest.approx(-1.0)

    def test_default_ordering_is_global(self):
        scores = {sc: 1.0 - 0.1 * rank for sc, rank in rm.SEVERITY_ORDER.items()}
        res = rm.compute_spearman(self._raw_from_scores(scores))
        assert res["M"]["rho"] == pytest.approx(1.0)
