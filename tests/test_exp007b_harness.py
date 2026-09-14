"""
EXP-007b tests: per-evidence-type detection, mutual information, evidence
volume plumbing, and the b-variant driver.
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
from simulation.simulator import (
    INSULIN, NOISE_R0, NOISE_R1, NOISE_R2,
    generate_noisy_normal_evidence, apply_failure, get_standard_scenarios,
)
from models.chain_integrity import evaluate_path

PATH = st.EVAL_PATH


def _feats(seed, scenario=None, noise=NOISE_R1, n=100):
    rng = np.random.default_rng(seed)
    ev = {k: generate_noisy_normal_evidence(INSULIN, noise, rng, n_packages=n, n_readings=n) for k in PATH}
    if scenario is not None:
        ev = apply_failure(ev, scenario, INSULIN, rng)
    return st.sl_features(evaluate_path(PATH, ev, weights=INSULIN.weights))


class TestPerTypeDetection:

    @pytest.mark.parametrize("sid", [None, "F1", "F3", "F4", "F6"])
    def test_equals_pooled_rule_when_all_type_thresholds_are_equal(self, sid):
        scenario = get_standard_scenarios()[sid] if sid else None
        for seed in range(6):
            f = _feats(seed, scenario)
            pooled = st.SLThresholds(score=0.9, chain_u=0.05, dim_d=0.05, dim_u=0.45)
            per_type = st.SLTypeThresholds(
                score=0.9, chain_u=0.05,
                dim_d={t: 0.05 for t in st.EVIDENCE_TYPES},
                dim_u={t: 0.45 for t in st.EVIDENCE_TYPES},
            )
            assert st.sl_detect(f, pooled) == st.sl_detect_per_type(f, per_type)

    def test_type_maxima_are_consistent_with_pooled_maxima(self):
        f = _feats(1, get_standard_scenarios()["F3"])
        assert max(d for d, _ in f.type_max.values()) == pytest.approx(f.d_max)
        assert max(u for _, u in f.type_max.values()) == pytest.approx(f.u_max)
        assert set(f.type_max) == set(st.EVIDENCE_TYPES)
        for node in PATH:
            assert set(f.per_node_type[node]) == set(st.EVIDENCE_TYPES)

    def test_only_the_offending_type_needs_a_low_threshold(self):
        """F3 raises serialization disbelief; a loose serialization threshold
        with tight others must not detect it, a tight one must."""
        f = _feats(2, get_standard_scenarios()["F3"], noise=NOISE_R0)
        loose = {t: 1.0 for t in st.EVIDENCE_TYPES}
        tight_ser = dict(loose, serialization=0.02)
        never_u = {t: 1.0 for t in st.EVIDENCE_TYPES}
        thr_loose = st.SLTypeThresholds(score=0.0, chain_u=1.0, dim_d=loose, dim_u=never_u)
        thr_tight = st.SLTypeThresholds(score=0.0, chain_u=1.0, dim_d=tight_ser, dim_u=never_u)
        assert st.sl_detect_per_type(f, thr_loose)[0] is False
        det, flagged = st.sl_detect_per_type(f, thr_tight)
        assert det is True and flagged == ["importer"]


class TestMutualInformation:

    def test_independent_scores_give_zero(self):
        same = list(np.linspace(0.1, 0.9, 200))
        scores = {f"S{i}": same for i in range(8)}
        assert st.mutual_information(scores) == pytest.approx(0.0, abs=1e-12)

    def test_perfectly_separated_scores_give_three_bits(self):
        scores = {f"S{i}": [(i + 0.5) / 8] * 100 for i in range(8)}
        assert st.mutual_information(scores) == pytest.approx(3.0, abs=1e-9)

    def test_binary_output_ceiling(self):
        """Normal scores 1, all seven failures score 0: 3 - (7/8) log2 7 bits."""
        scores = {"Normal": [1.0] * 100}
        scores.update({f"F{i}": [0.0] * 100 for i in range(1, 8)})
        expected = 3.0 - (7 / 8) * math.log2(7)
        assert st.mutual_information(scores) == pytest.approx(expected, abs=1e-9)

    def test_empty_input(self):
        assert st.mutual_information({}) == 0.0


class TestEvidenceVolume:

    def test_n_plumbing_changes_evidence_sizes(self):
        rng = np.random.default_rng(4)
        ev = generate_noisy_normal_evidence(INSULIN, NOISE_R2, rng, n_packages=1000, n_readings=1000)
        assert ev.serialization.total_packages == 1000
        assert ev.cold_chain.expected_readings == 1000
        assert 700 <= len(ev.cold_chain.readings) <= 1000

    def test_failure_fractions_scale_with_n(self):
        rng = np.random.default_rng(4)
        ev = {k: generate_noisy_normal_evidence(INSULIN, NOISE_R0, rng, n_packages=1000, n_readings=1000) for k in PATH}
        f3 = get_standard_scenarios()["F3"]
        inj = apply_failure(ev, f3, INSULIN, rng)
        assert inj["importer"].serialization.scanned_failure == 50

    def test_evaluate_scenario_b_runs_at_large_n(self):
        cal = st.calibrate_per_type(INSULIN, NOISE_R1, n=1000, n_calib=8, seed=1)
        rows, scores = st.evaluate_scenario_b(INSULIN, NOISE_R1, cal,
                                              get_standard_scenarios()["F3"], "F3", n_trials=3, seed=1)
        assert len(rows) == len(st.METHODS_B)
        assert all(r.n == 1000 for r in rows)
        assert set(scores) == set(st.METHODS_B)


class TestCalibrationB:

    def test_r0_calibration_degenerate_and_ordered(self):
        cal = st.calibrate_per_type(INSULIN, NOISE_R0, n=100, n_calib=60, seed=1)
        assert all(v == 0.0 for v in cal.sl_cal_pt.dim_d.values())
        assert cal.sl_cal_pt.score <= cal.sl_cal.score      # 0.1st percentile <= 0.25th
        assert cal.b1_tuned["temp_tolerance"] == 0.0
        assert set(cal.sl_cal_pt.dim_u) == set(st.EVIDENCE_TYPES)

    def test_r1_per_type_thresholds_are_type_specific(self):
        cal = st.calibrate_per_type(INSULIN, NOISE_R1, n=100, n_calib=120, seed=1)
        d = cal.sl_cal_pt.dim_d
        assert d["shelf_life"] == 0.0                       # noise never touches shelf life
        assert d["documentation"] > 0.0 and d["cold_chain"] > 0.0
        assert d["serialization"] >= 0.0
        assert len({round(v, 6) for v in d.values()}) > 1  # thresholds differ across types

    def test_serialization(self):
        cal = st.calibrate_per_type(INSULIN, NOISE_R1, n=100, n_calib=20, seed=1)
        d = cal.to_dict()
        assert d["n"] == 100 and set(d["sl_cal_pt"]) == {"score", "chain_u", "dim_d", "dim_u"}


@pytest.fixture(scope="module")
def smoke_b():
    return st.run_stress_test_b(n_trials=12, n_calib=30, products=[INSULIN],
                                regimes=["R0", "R2"], n_levels=[100], sweep_regimes=[], verbose=False)


class TestDriverB:

    def test_row_count(self, smoke_b):
        assert len(smoke_b["results"]) == 2 * 8 * len(st.METHODS_B)

    def test_rows_carry_n_and_valid_intervals(self, smoke_b):
        for r in smoke_b["results"]:
            assert r["n"] == 100
            rate = r["false_positive_rate"] if r["scenario"] == "Normal" else r["detection_rate"]
            lo, hi = r["rate_ci"]
            assert lo <= rate <= hi

    def test_ranking_has_mutual_information(self, smoke_b):
        entry = smoke_b["ranking"]["N100"]["R2"]["Insulin "]
        assert set(entry["mutual_information_bits"]) == set(st.METHODS_B)
        for m in st.METHODS_B:
            assert 0.0 <= entry["mutual_information_bits"][m] <= 3.0

    def test_config(self, smoke_b):
        assert smoke_b["config"]["variant"] == "b"
        assert smoke_b["config"]["sl_pt_criteria"] == 10
        assert smoke_b["counterfeit_sweep"] == {"N100": {}}
