"""
Tests for supply chain simulator and failure injection.

Validates:
- Normal evidence generation produces valid, realistic evidence
- Each failure scenario (F1–F7) correctly modifies evidence
- Monte Carlo trials produce reproducible, valid results
- Detection rates and localization behave as expected
"""

import numpy as np
import pytest

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from simulation.simulator import (
    INSULIN, AMOXICILLIN, MRNA_THERAPY, ALL_PRODUCTS,
    FailureType, FailureScenario,
    generate_normal_evidence,
    apply_failure,
    get_standard_scenarios,
    run_trial,
    run_experiment,
)
from models.drug_integrity_opinion import RecallStatus


SIMPLE_PATH = ["manufacturer", "importer", "central_warehouse", "distributor_a", "pharmacy_a"]


class TestNormalEvidenceGeneration:
    """Tests for generate_normal_evidence."""

    def test_produces_valid_evidence(self):
        """Normal evidence has all five components populated."""
        rng = np.random.default_rng(42)
        ev = generate_normal_evidence(INSULIN, rng=rng)

        assert ev.serialization is not None
        assert ev.cold_chain is not None
        assert ev.shelf_life is not None
        assert ev.documentation is not None
        assert ev.recall == RecallStatus.NO_RECALL

    def test_serialization_high_scan_rate(self):
        """Normal ops: scan rate 95–100%, near-zero failures."""
        rng = np.random.default_rng(42)
        ev = generate_normal_evidence(INSULIN, rng=rng)
        total = ev.serialization.total_packages
        scanned = ev.serialization.scanned_success + ev.serialization.scanned_failure
        assert scanned / total >= 0.94  # allow slight rng variation

    def test_cold_chain_in_range(self):
        """Normal ops: all readings within required range."""
        rng = np.random.default_rng(42)
        ev = generate_normal_evidence(INSULIN, rng=rng)
        for temp in ev.cold_chain.readings:
            assert ev.cold_chain.required_min <= temp <= ev.cold_chain.required_max

    def test_shelf_life_above_minimum(self):
        """Normal ops: shelf life above regulatory minimum."""
        rng = np.random.default_rng(42)
        ev = generate_normal_evidence(INSULIN, rng=rng)
        assert ev.shelf_life.remaining_days > ev.shelf_life.regulatory_min

    def test_documentation_complete(self):
        """Normal ops: all documents present."""
        rng = np.random.default_rng(42)
        ev = generate_normal_evidence(INSULIN, rng=rng)
        assert ev.documentation.documents_present == ev.documentation.documents_required

    def test_reproducible_with_seed(self):
        """Same seed → same evidence."""
        ev1 = generate_normal_evidence(INSULIN, rng=np.random.default_rng(123))
        ev2 = generate_normal_evidence(INSULIN, rng=np.random.default_rng(123))
        assert ev1.serialization.scanned_success == ev2.serialization.scanned_success
        assert ev1.cold_chain.readings == ev2.cold_chain.readings

    @pytest.mark.parametrize("product", ALL_PRODUCTS)
    def test_all_products_generate(self, product):
        """All three product categories produce valid evidence."""
        rng = np.random.default_rng(42)
        ev = generate_normal_evidence(product, rng=rng)
        assert ev.serialization is not None
        assert ev.cold_chain is not None


class TestFailureInjection:
    """Tests for apply_failure with each scenario."""

    def _make_base_evidence(self, product=INSULIN):
        rng = np.random.default_rng(42)
        return {
            node: generate_normal_evidence(product, rng=rng)
            for node in SIMPLE_PATH
        }

    def test_f1_mild_excursion(self):
        """F1: Some readings pushed above max."""
        base = self._make_base_evidence()
        scenario = FailureScenario(
            FailureType.F1_MILD_EXCURSION, "distributor_a",
            {"duration_fraction": 0.05, "magnitude": 2.0},
        )
        result = apply_failure(base, scenario, INSULIN, np.random.default_rng(42))

        # Check that some readings are out of range
        readings = result["distributor_a"].cold_chain.readings
        out_of_range = [t for t in readings if t > INSULIN.cold_chain_max]
        assert len(out_of_range) > 0
        # But not all — only a fraction
        assert len(out_of_range) < len(readings) * 0.5

    def test_f2_severe_excursion(self):
        """F2: Many readings far above max."""
        base = self._make_base_evidence()
        scenario = FailureScenario(
            FailureType.F2_SEVERE_EXCURSION, "distributor_a",
            {"duration_fraction": 0.5, "magnitude": 17.0},
        )
        result = apply_failure(base, scenario, INSULIN, np.random.default_rng(42))

        readings = result["distributor_a"].cold_chain.readings
        severe = [t for t in readings if t > INSULIN.cold_chain_max + 10]
        assert len(severe) > 0

    def test_f3_counterfeit(self):
        """F3: Serialization has failures from counterfeit packages."""
        base = self._make_base_evidence()
        scenario = FailureScenario(
            FailureType.F3_COUNTERFEIT, "importer",
            {"counterfeit_fraction": 0.05},
        )
        result = apply_failure(base, scenario, INSULIN, np.random.default_rng(42))
        assert result["importer"].serialization.scanned_failure > 0

    def test_f4_scan_omission(self):
        """F4: Scan coverage drops to specified rate."""
        base = self._make_base_evidence()
        scenario = FailureScenario(
            FailureType.F4_SCAN_OMISSION, "central_warehouse",
            {"scan_rate": 0.20},
        )
        result = apply_failure(base, scenario, INSULIN, np.random.default_rng(42))
        ser = result["central_warehouse"].serialization
        scanned = ser.scanned_success + ser.scanned_failure
        assert scanned / ser.total_packages <= 0.25

    def test_f5_expired_batch(self):
        """F5: Shelf life drops below regulatory minimum."""
        base = self._make_base_evidence()
        scenario = FailureScenario(
            FailureType.F5_EXPIRED_BATCH, "distributor_a",
            {"remaining_days": 90},
        )
        result = apply_failure(base, scenario, INSULIN, np.random.default_rng(42))
        assert result["distributor_a"].shelf_life.remaining_days == 90
        assert result["distributor_a"].shelf_life.remaining_days < result["distributor_a"].shelf_life.regulatory_min

    def test_f6_missing_docs(self):
        """F6: Critical document removed."""
        base = self._make_base_evidence()
        scenario = FailureScenario(
            FailureType.F6_MISSING_DOCS, "importer",
            {"missing": ["certificate_of_analysis"]},
        )
        result = apply_failure(base, scenario, INSULIN, np.random.default_rng(42))
        assert "certificate_of_analysis" not in result["importer"].documentation.documents_present

    def test_f7_cascading(self):
        """F7: Multiple failures at different nodes."""
        base = self._make_base_evidence()
        scenarios = get_standard_scenarios()
        result = apply_failure(base, scenarios["F7"], INSULIN, np.random.default_rng(42))

        # Warehouse should have temperature excursion
        wh_readings = result["central_warehouse"].cold_chain.readings
        wh_out = [t for t in wh_readings if t > INSULIN.cold_chain_max]
        assert len(wh_out) > 0

        # Distributor should have low scan coverage
        dist_ser = result["distributor_a"].serialization
        dist_scanned = dist_ser.scanned_success + dist_ser.scanned_failure
        assert dist_scanned / dist_ser.total_packages <= 0.25

    def test_failure_does_not_mutate_input(self):
        """apply_failure returns new dict, does not mutate original."""
        base = self._make_base_evidence()
        original_readings = base["distributor_a"].cold_chain.readings
        scenario = FailureScenario(
            FailureType.F2_SEVERE_EXCURSION, "distributor_a",
            {"duration_fraction": 0.5, "magnitude": 17.0},
        )
        _ = apply_failure(base, scenario, INSULIN, np.random.default_rng(42))
        assert base["distributor_a"].cold_chain.readings == original_readings


class TestTrialAndExperiment:
    """Tests for run_trial and run_experiment."""

    def test_normal_trial_high_integrity(self):
        """Normal operation trial → high integrity score."""
        trial = run_trial(
            trial_id=0, path=SIMPLE_PATH, product=INSULIN,
            rng=np.random.default_rng(42),
        )
        assert trial.integrity_score > 0.5
        assert trial.scenario is None

    def test_failure_trial_lower_integrity(self):
        """Failure trial → lower integrity than normal."""
        rng_normal = np.random.default_rng(42)
        rng_failure = np.random.default_rng(42)

        normal = run_trial(0, SIMPLE_PATH, INSULIN, rng=rng_normal)
        failure = run_trial(
            0, SIMPLE_PATH, INSULIN,
            scenario=FailureScenario(
                FailureType.F2_SEVERE_EXCURSION, "distributor_a",
                {"duration_fraction": 0.5, "magnitude": 17.0},
            ),
            scenario_name="F2",
            rng=rng_failure,
        )
        assert failure.integrity_score < normal.integrity_score

    def test_experiment_reproducible(self):
        """Same seed → same results."""
        r1 = run_experiment(SIMPLE_PATH, INSULIN, n_trials=10, seed=42)
        r2 = run_experiment(SIMPLE_PATH, INSULIN, n_trials=10, seed=42)
        assert r1.mean_integrity == r2.mean_integrity

    def test_experiment_normal_low_fpr(self):
        """Normal operation experiments should have low false positive rate."""
        result = run_experiment(SIMPLE_PATH, INSULIN, n_trials=100, seed=42)
        assert result.false_positive_rate < 0.1  # <10% FPR

    def test_experiment_severe_failure_high_detection(self):
        """Severe failure should have high detection rate."""
        scenario = FailureScenario(
            FailureType.F2_SEVERE_EXCURSION, "distributor_a",
            {"duration_fraction": 0.5, "magnitude": 17.0},
        )
        result = run_experiment(
            SIMPLE_PATH, INSULIN, n_trials=100, seed=42,
            scenario=scenario, scenario_name="F2",
        )
        assert result.detection_rate > 0.5
