"""
EXP-007 tests for scripts/run_stress_test.py.

Validates:
- sl_detect with the fixed thresholds reproduces the main-suite rule
  (run_experiments.sl_detect_per_dimension) on clean and failed chains
- calibration under R0 yields the degenerate thresholds the clean regime
  implies, and under R1 yields positive tolerances bounded by the regime
- B1 per-trial statistics are the chain-level maxima/minima
- the driver produces one row per regime x scenario x method with valid
  intervals and ranking entries
- the counterfeit sweep has the expected shape
"""

import math

import numpy as np
import pytest

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import run_stress_test as st
import run_experiments as rx
from simulation.simulator import (
    INSULIN, NOISE_R0, NOISE_R1, NOISE_R2,
    generate_normal_evidence, generate_noisy_normal_evidence,
    apply_failure, get_standard_scenarios,
)
from models.chain_integrity import evaluate_path

PATH = st.EVAL_PATH


def _chain(seed, scenario=None, noise=NOISE_R0):
    rng = np.random.default_rng(seed)
    ev = {n: generate_noisy_normal_evidence(INSULIN, noise, rng) for n in PATH}
    if scenario is not None:
        ev = apply_failure(ev, scenario, INSULIN, rng)
    return evaluate_path(PATH, ev, weights=INSULIN.weights), ev


class TestSlDetectMatchesMainSuite:

    @pytest.mark.parametrize("sid", [None, "F3", "F4", "F2", "F6"])
    def test_fixed_thresholds_reproduce_sl_detect_per_dimension(self, sid):
        scenario = get_standard_scenarios()[sid] if sid else None
        for seed in range(5):
            chain, _ = _chain(seed, scenario)
            chain_thr = 0.9962  # insulin main-suite threshold, order of magnitude
            det_ref, flagged_ref = rx.sl_detect_per_dimension(chain, chain_thr)
            thr = st.SLThresholds(score=chain_thr, chain_u=st.CHAIN_U_THRESHOLD,
                                  dim_d=rx.DIM_DISBELIEF_THRESHOLD, dim_u=rx.DIM_UNCERTAINTY_THRESHOLD)
            det, flagged = st.sl_detect(st.sl_features(chain), thr)
            assert det == det_ref
            assert flagged == flagged_ref

    def test_features_exclude_recall_and_are_bounded(self):
        chain, _ = _chain(0)
        f = st.sl_features(chain)
        assert 0.0 <= f.d_max <= 1.0 and 0.0 <= f.u_max <= 1.0
        assert set(f.per_node) == set(PATH)
        # recall is vacuous (u = 1) under no-recall; excluding it keeps u_max well below 1
        assert f.u_max < 0.5


class TestB1Statistics:

    def test_clean_chain_statistics(self):
        rng = np.random.default_rng(3)
        ev = {n: generate_normal_evidence(INSULIN, rng=rng) for n in PATH}
        max_fr, min_sr, max_exc = st.b1_trial_statistics(ev)
        assert max_fr == 0.0
        assert 0.94 <= min_sr <= 1.0
        assert max_exc == 0.0

    def test_noisy_chain_statistics_reflect_regime(self):
        rng = np.random.default_rng(3)
        ev = {n: generate_noisy_normal_evidence(INSULIN, NOISE_R2, rng) for n in PATH}
        max_fr, min_sr, max_exc = st.b1_trial_statistics(ev)
        assert 0.0 <= max_fr <= 1.0
        assert 0.0 <= max_exc <= 2.0
        assert 0.94 <= min_sr <= 1.0


class TestCalibration:

    def test_r0_calibration_is_degenerate_where_the_clean_regime_implies(self):
        cal = st.calibrate(INSULIN, NOISE_R0, n_calib=60, seed=1)
        assert cal.sl_cal.dim_d == 0.0                 # no disbelief anywhere under clean Normal
        assert cal.b1_tuned["max_failure_rate"] == 0.0
        assert cal.b1_tuned["temp_tolerance"] == 0.0
        assert cal.b1_tuned["min_scan_rate"] >= 0.94
        assert cal.sl_cal.score <= cal.sl_fixed.score  # 0.25th percentile <= 1st percentile
        assert cal.sl_fixed.dim_d == rx.DIM_DISBELIEF_THRESHOLD
        assert cal.sl_fixed.dim_u == rx.DIM_UNCERTAINTY_THRESHOLD

    def test_r1_calibration_is_bounded_by_the_regime(self):
        cal = st.calibrate(INSULIN, NOISE_R1, n_calib=80, seed=1)
        assert 0.0 < cal.b1_tuned["temp_tolerance"] <= 1.0
        assert 0.0 <= cal.b1_tuned["max_failure_rate"] <= 0.1
        assert cal.sl_cal.dim_d > 0.0
        assert 0.0 < cal.sl_cal.dim_u < 0.5
        for k in ("B2", "B3", "B4"):
            assert 0.0 <= cal.chain_thresholds[k] <= 1.0

    def test_calibration_serializes(self):
        cal = st.calibrate(INSULIN, NOISE_R1, n_calib=20, seed=1)
        d = cal.to_dict()
        assert set(d) == {"regime", "product", "n_calib", "sl_cal", "sl_fixed", "b1_tuned", "chain_thresholds"}
        assert d["regime"] == "R1"


@pytest.fixture(scope="module")
def smoke():
    return st.run_stress_test(n_trials=15, n_calib=40, products=[INSULIN],
                              regimes=["R0", "R2"], sweep_regimes=[], verbose=False)


class TestDriver:

    def test_row_count(self, smoke):
        # 2 regimes x 8 scenarios x 7 method variants
        assert len(smoke["results"]) == 2 * 8 * len(st.METHODS)

    def test_rates_bounded_and_intervals_contain_them(self, smoke):
        for r in smoke["results"]:
            rate = r["false_positive_rate"] if r["scenario"] == "Normal" else r["detection_rate"]
            lo, hi = r["rate_ci"]
            assert 0.0 <= rate <= 1.0 and lo <= rate <= hi
            assert 0.0 <= r["localization_accuracy"] <= 1.0
            assert r["n_detected"] == pytest.approx(rate * r["n_trials"])

    def test_calibrations_and_ranking_present(self, smoke):
        assert set(smoke["calibrations"]) == {"R0", "R2"}
        assert set(smoke["ranking"]) == {"R0", "R2"}
        for regime in ("R0", "R2"):
            entry = smoke["ranking"][regime]["Insulin "]
            assert set(entry["spearman"]) == set(st.METHODS)
            assert set(entry["entropy_bits"]) == set(st.METHODS)
            for m in st.METHODS:
                rho = entry["spearman"][m]["rho"]
                if entry["spearman"][m]["constant_output"]:
                    assert math.isnan(rho)
                else:
                    assert -1.0 <= rho <= 1.0
                assert entry["entropy_bits"][m] >= 0.0

    def test_no_sweep_when_disabled(self, smoke):
        assert smoke["counterfeit_sweep"] == {}

    def test_config_records_regimes(self, smoke):
        assert set(smoke["config"]["regimes"]) == {"R0", "R2"}
        assert smoke["config"]["regimes"]["R2"]["blip_prob"] == 0.05


class TestSweepShape:

    def test_counterfeit_sweep_keys(self):
        cal = st.calibrate(INSULIN, NOISE_R1, n_calib=20, seed=1)
        sw = st.counterfeit_sweep(INSULIN, NOISE_R1, cal, n_trials=4, seed=1)
        assert list(sw) == [str(f) for f in st.COUNTERFEIT_FRACTIONS]
        for vals in sw.values():
            assert set(vals) == set(st.METHODS)
