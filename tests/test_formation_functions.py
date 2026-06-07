"""
Tests for Drug Integrity Algebra formation functions (F1–F5).

Validates the mathematical properties stated in PAPER_PLAN.md §3.3.
Each test verifies both correctness and the specific algebraic properties
that the paper claims are provable.

Organized by formation function, with property tests clearly labeled
to match the paper's proof obligations.
"""

import math
import pytest

# Adjust import path as needed when jsonld-ex is installed
from jsonld_ex.confidence_algebra import Opinion

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from models.drug_integrity_opinion import (
    DrugIntegrityOpinion,
    SerializationEvidence,
    ColdChainEvidence,
    ShelfLifeEvidence,
    DocumentationEvidence,
    RecallStatus,
    EvidenceWeights,
    form_serialization_opinion,
    form_cold_chain_opinion,
    form_shelf_life_opinion,
    form_documentation_opinion,
    form_recall_opinion,
    DEFAULT_CRITICAL_DOCUMENTS,
    DEFAULT_REQUIRED_DOCUMENTS,
)


# ═══════════════════════════════════════════════════════════════════
# F1 — SERIALIZATION TESTS
# ═══════════════════════════════════════════════════════════════════


class TestSerializationOpinion:
    """Tests for form_serialization_opinion (F1)."""

    def test_full_scan_all_pass(self):
        """P1: Full scan, all pass → near-dogmatic integrity."""
        ev = SerializationEvidence(scanned_success=100, scanned_failure=0, total_packages=100)
        omega = form_serialization_opinion(ev)

        assert omega.belief > 0.9, f"Expected high belief, got {omega.belief}"
        assert omega.disbelief < 0.01, f"Expected near-zero disbelief, got {omega.disbelief}"
        assert omega.uncertainty < 0.1, f"Expected low uncertainty, got {omega.uncertainty}"
        assert isinstance(omega, DrugIntegrityOpinion)

    def test_full_scan_some_failures(self):
        """P2: Full scan with failures → d proportional to failure rate."""
        ev_5pct = SerializationEvidence(scanned_success=95, scanned_failure=5, total_packages=100)
        ev_20pct = SerializationEvidence(scanned_success=80, scanned_failure=20, total_packages=100)

        omega_5 = form_serialization_opinion(ev_5pct)
        omega_20 = form_serialization_opinion(ev_20pct)

        # More failures → more disbelief
        assert omega_20.disbelief > omega_5.disbelief
        # Both should have disbelief > 0
        assert omega_5.disbelief > 0
        assert omega_20.disbelief > 0

    def test_partial_scan_all_pass(self):
        """P3: Partial scan, all pass → u proportional to unscanned fraction."""
        ev_full = SerializationEvidence(scanned_success=100, scanned_failure=0, total_packages=100)
        ev_half = SerializationEvidence(scanned_success=50, scanned_failure=0, total_packages=100)
        ev_tenth = SerializationEvidence(scanned_success=10, scanned_failure=0, total_packages=100)

        omega_full = form_serialization_opinion(ev_full)
        omega_half = form_serialization_opinion(ev_half)
        omega_tenth = form_serialization_opinion(ev_tenth)

        # Lower coverage → higher uncertainty
        assert omega_tenth.uncertainty > omega_half.uncertainty > omega_full.uncertainty

    def test_no_scan_is_vacuous(self):
        """P4: No scan → vacuous opinion (maximal uncertainty)."""
        ev = SerializationEvidence(scanned_success=0, scanned_failure=0, total_packages=100)
        omega = form_serialization_opinion(ev)

        assert omega.belief == 0.0
        assert omega.disbelief == 0.0
        assert omega.uncertainty == 1.0

    def test_monotonic_in_coverage(self):
        """P5: Increasing scan rate can only decrease uncertainty."""
        base_u = None
        for scan_count in [10, 20, 30, 50, 70, 100]:
            ev = SerializationEvidence(
                scanned_success=scan_count, scanned_failure=0, total_packages=100
            )
            omega = form_serialization_opinion(ev)
            if base_u is not None:
                assert omega.uncertainty <= base_u + 1e-9, (
                    f"Uncertainty increased from {base_u} to {omega.uncertainty} "
                    f"when scan count went from {scan_count - 10} to {scan_count}"
                )
            base_u = omega.uncertainty

    def test_additivity_constraint(self):
        """b + d + u = 1 for all valid inputs."""
        cases = [
            SerializationEvidence(100, 0, 100),
            SerializationEvidence(50, 5, 100),
            SerializationEvidence(10, 0, 100),
            SerializationEvidence(0, 0, 100),
            SerializationEvidence(1, 1, 100),
        ]
        for ev in cases:
            omega = form_serialization_opinion(ev)
            total = omega.belief + omega.disbelief + omega.uncertainty
            assert abs(total - 1.0) < 1e-9, f"b+d+u = {total} for {ev}"

    def test_isinstance_opinion(self):
        """DrugIntegrityOpinion is accepted by all SL operators."""
        ev = SerializationEvidence(80, 2, 100)
        omega = form_serialization_opinion(ev)
        assert isinstance(omega, Opinion)
        assert isinstance(omega, DrugIntegrityOpinion)

    def test_validation_rejects_invalid(self):
        """Invalid evidence inputs raise ValueError."""
        with pytest.raises(ValueError):
            SerializationEvidence(-1, 0, 100)
        with pytest.raises(ValueError):
            SerializationEvidence(0, 0, 0)
        with pytest.raises(ValueError):
            SerializationEvidence(50, 60, 100)  # scanned > total


# ═══════════════════════════════════════════════════════════════════
# F2 — COLD CHAIN TESTS
# ═══════════════════════════════════════════════════════════════════


class TestColdChainOpinion:
    """Tests for form_cold_chain_opinion (F2)."""

    def test_all_in_range_full_coverage(self):
        """P1: All in-range, full coverage → near-dogmatic integrity."""
        readings = tuple([5.0] * 100)  # all at 5°C for 2-8°C range
        ev = ColdChainEvidence(readings=readings, required_min=2.0, required_max=8.0, expected_readings=100)
        omega = form_cold_chain_opinion(ev)

        assert omega.belief > 0.9
        assert omega.disbelief < 0.05
        assert omega.uncertainty < 0.1

    def test_mild_excursion_increases_disbelief(self):
        """P2: Mild excursion → d increases proportional to severity."""
        readings_clean = tuple([5.0] * 100)
        readings_mild = tuple([5.0] * 95 + [9.0] * 5)  # 5 readings at 9°C (1°C above max)

        ev_clean = ColdChainEvidence(readings=readings_clean, required_min=2.0, required_max=8.0, expected_readings=100)
        ev_mild = ColdChainEvidence(readings=readings_mild, required_min=2.0, required_max=8.0, expected_readings=100)

        omega_clean = form_cold_chain_opinion(ev_clean)
        omega_mild = form_cold_chain_opinion(ev_mild)

        assert omega_mild.disbelief > omega_clean.disbelief

    def test_severe_excursion_dominates(self):
        """P3: Severe excursion → d dominates proportional to severity."""
        readings_mild = tuple([5.0] * 95 + [9.0] * 5)    # 1°C above max
        readings_severe = tuple([5.0] * 95 + [25.0] * 5)  # 17°C above max

        ev_mild = ColdChainEvidence(readings=readings_mild, required_min=2.0, required_max=8.0, expected_readings=100)
        ev_severe = ColdChainEvidence(readings=readings_severe, required_min=2.0, required_max=8.0, expected_readings=100)

        omega_mild = form_cold_chain_opinion(ev_mild)
        omega_severe = form_cold_chain_opinion(ev_severe)

        assert omega_severe.disbelief > omega_mild.disbelief

    def test_sensor_gap_increases_uncertainty(self):
        """P4: Sensor offline → u increases proportional to gap."""
        readings = tuple([5.0] * 50)  # only 50 of 100 expected readings

        ev_full = ColdChainEvidence(readings=tuple([5.0] * 100), required_min=2.0, required_max=8.0, expected_readings=100)
        ev_gap = ColdChainEvidence(readings=readings, required_min=2.0, required_max=8.0, expected_readings=100)

        omega_full = form_cold_chain_opinion(ev_full)
        omega_gap = form_cold_chain_opinion(ev_gap)

        assert omega_gap.uncertainty > omega_full.uncertainty

    def test_no_readings_is_vacuous(self):
        """P5: No readings → vacuous opinion."""
        ev = ColdChainEvidence(readings=(), required_min=2.0, required_max=8.0, expected_readings=100)
        omega = form_cold_chain_opinion(ev)

        assert omega.belief == 0.0
        assert omega.disbelief == 0.0
        assert omega.uncertainty == 1.0

    def test_additivity_constraint(self):
        """b + d + u = 1 for all valid inputs."""
        cases = [
            ColdChainEvidence(tuple([5.0] * 100), 2.0, 8.0, 100),
            ColdChainEvidence(tuple([5.0] * 50 + [25.0] * 50), 2.0, 8.0, 100),
            ColdChainEvidence(tuple([5.0] * 30), 2.0, 8.0, 100),
            ColdChainEvidence((), 2.0, 8.0, 100),
        ]
        for ev in cases:
            omega = form_cold_chain_opinion(ev)
            total = omega.belief + omega.disbelief + omega.uncertainty
            assert abs(total - 1.0) < 1e-9, f"b+d+u = {total}"


# ═══════════════════════════════════════════════════════════════════
# F3 — SHELF LIFE TESTS
# ═══════════════════════════════════════════════════════════════════


class TestShelfLifeOpinion:
    """Tests for form_shelf_life_opinion (F3)."""

    def test_ample_margin_high_belief(self):
        """P1: ratio >> 1 → near-dogmatic integrity."""
        ev = ShelfLifeEvidence(remaining_days=360, regulatory_min=180)
        omega = form_shelf_life_opinion(ev)

        assert omega.belief > 0.8
        assert omega.disbelief < 0.01

    def test_at_threshold_maximum_uncertainty(self):
        """P2: ratio = 1.0 → vacuous (maximum uncertainty).

        THIS IS THE KEY PROPERTY: at the regulatory threshold,
        the opinion is maximally uncertain. This is the correct
        behavior — the threshold is where the decision is hardest.
        """
        ev = ShelfLifeEvidence(remaining_days=180, regulatory_min=180)
        omega = form_shelf_life_opinion(ev)

        # At ratio = 1.0, r = 0 and w = 0, so opinion is vacuous
        assert omega.belief == 0.0
        assert omega.disbelief == 0.0
        assert omega.uncertainty == 1.0

    def test_expired_high_disbelief(self):
        """P3: ratio << 1 → near-dogmatic degradation."""
        ev = ShelfLifeEvidence(remaining_days=30, regulatory_min=180)
        omega = form_shelf_life_opinion(ev)

        assert omega.disbelief > 0.7
        assert omega.belief < 0.01

    def test_monotonically_degrading(self):
        """P4: As remaining_days decreases, belief decreases monotonically."""
        prev_belief = 1.0
        prev_disbelief = 0.0
        for days in [360, 270, 180, 120, 90, 30, 1]:
            ev = ShelfLifeEvidence(remaining_days=days, regulatory_min=180)
            omega = form_shelf_life_opinion(ev)
            assert omega.belief <= prev_belief + 1e-9, (
                f"Belief increased at {days} days: {prev_belief} -> {omega.belief}"
            )
            assert omega.disbelief >= prev_disbelief - 1e-9, (
                f"Disbelief decreased at {days} days: {prev_disbelief} -> {omega.disbelief}"
            )
            prev_belief = omega.belief
            prev_disbelief = omega.disbelief

    def test_continuity(self):
        """P5: Small changes in ratio produce small changes in opinion."""
        ev1 = ShelfLifeEvidence(remaining_days=181, regulatory_min=180)
        ev2 = ShelfLifeEvidence(remaining_days=179, regulatory_min=180)

        omega1 = form_shelf_life_opinion(ev1)
        omega2 = form_shelf_life_opinion(ev2)

        # 2-day difference should not produce a discontinuous jump
        assert abs(omega1.belief - omega2.belief) < 0.15
        assert abs(omega1.disbelief - omega2.disbelief) < 0.15


# ═══════════════════════════════════════════════════════════════════
# F4 — DOCUMENTATION TESTS
# ═══════════════════════════════════════════════════════════════════


class TestDocumentationOpinion:
    """Tests for form_documentation_opinion (F4)."""

    def test_all_present_high_belief(self):
        """P1: All documents present → high belief."""
        ev = DocumentationEvidence(
            documents_present=DEFAULT_REQUIRED_DOCUMENTS,
            documents_required=DEFAULT_REQUIRED_DOCUMENTS,
            documents_critical=DEFAULT_CRITICAL_DOCUMENTS,
        )
        omega = form_documentation_opinion(ev)

        assert omega.belief > 0.7
        assert omega.disbelief == 0.0

    def test_all_missing_critical_strong_disbelief(self):
        """P2: All missing and critical → strong disbelief."""
        ev = DocumentationEvidence(
            documents_present=frozenset(),
            documents_required=DEFAULT_REQUIRED_DOCUMENTS,
            documents_critical=DEFAULT_CRITICAL_DOCUMENTS,
        )
        omega = form_documentation_opinion(ev)

        assert omega.disbelief > 0.5

    def test_missing_critical_worse_than_noncritical(self):
        """P4: Missing critical doc produces more disbelief than non-critical."""
        # Missing CoA (critical)
        present_no_coa = DEFAULT_REQUIRED_DOCUMENTS - {"certificate_of_analysis"}
        ev_missing_crit = DocumentationEvidence(
            documents_present=frozenset(present_no_coa),
            documents_required=DEFAULT_REQUIRED_DOCUMENTS,
            documents_critical=DEFAULT_CRITICAL_DOCUMENTS,
        )

        # Missing import_license (non-critical)
        present_no_license = DEFAULT_REQUIRED_DOCUMENTS - {"import_license"}
        ev_missing_noncrit = DocumentationEvidence(
            documents_present=frozenset(present_no_license),
            documents_required=DEFAULT_REQUIRED_DOCUMENTS,
            documents_critical=DEFAULT_CRITICAL_DOCUMENTS,
        )

        omega_crit = form_documentation_opinion(ev_missing_crit)
        omega_noncrit = form_documentation_opinion(ev_missing_noncrit)

        assert omega_crit.disbelief > omega_noncrit.disbelief

    def test_empty_required_set_is_vacuous(self):
        """No required documents → vacuous opinion."""
        ev = DocumentationEvidence(
            documents_present=frozenset(),
            documents_required=frozenset(),
            documents_critical=frozenset(),
        )
        omega = form_documentation_opinion(ev)
        assert omega.uncertainty == 1.0


# ═══════════════════════════════════════════════════════════════════
# F5 — RECALL TESTS
# ═══════════════════════════════════════════════════════════════════


class TestRecallOpinion:
    """Tests for form_recall_opinion (F5)."""

    def test_active_recall_dogmatic_disbelief(self):
        """P1: Active recall → dogmatic disbelief (override)."""
        omega = form_recall_opinion(RecallStatus.ACTIVE_RECALL)

        assert omega.belief == 0.0
        assert omega.disbelief == 1.0
        assert omega.uncertainty == 0.0

    def test_no_recall_vacuous(self):
        """P2: No recall → vacuous (no information)."""
        omega = form_recall_opinion(RecallStatus.NO_RECALL)

        assert omega.belief == 0.0
        assert omega.disbelief == 0.0
        assert omega.uncertainty == 1.0

    def test_recall_isinstance(self):
        """Recall opinions are proper DrugIntegrityOpinions."""
        for status in RecallStatus:
            omega = form_recall_opinion(status)
            assert isinstance(omega, DrugIntegrityOpinion)
            assert isinstance(omega, Opinion)


# ═══════════════════════════════════════════════════════════════════
# CROSS-CUTTING PROPERTY TESTS
# ═══════════════════════════════════════════════════════════════════


class TestCrossCuttingProperties:
    """Properties that must hold across ALL formation functions."""

    def test_all_opinions_are_valid_sl_opinions(self):
        """Every formation function produces valid SL opinions (b+d+u=1)."""
        opinions = [
            form_serialization_opinion(
                SerializationEvidence(80, 2, 100)
            ),
            form_cold_chain_opinion(
                ColdChainEvidence(tuple([5.0] * 80 + [10.0] * 20), 2.0, 8.0, 100)
            ),
            form_shelf_life_opinion(
                ShelfLifeEvidence(200, 180)
            ),
            form_documentation_opinion(
                DocumentationEvidence(
                    frozenset({"gmp_certificate", "certificate_of_analysis"}),
                    DEFAULT_REQUIRED_DOCUMENTS,
                    DEFAULT_CRITICAL_DOCUMENTS,
                )
            ),
            form_recall_opinion(RecallStatus.NO_RECALL),
            form_recall_opinion(RecallStatus.ACTIVE_RECALL),
        ]

        for omega in opinions:
            total = omega.belief + omega.disbelief + omega.uncertainty
            assert abs(total - 1.0) < 1e-9, f"b+d+u = {total} for {omega}"
            assert 0 <= omega.belief <= 1
            assert 0 <= omega.disbelief <= 1
            assert 0 <= omega.uncertainty <= 1
            assert 0 <= omega.base_rate <= 1

    def test_all_opinions_are_drug_integrity_opinions(self):
        """Every formation function returns DrugIntegrityOpinion."""
        opinions = [
            form_serialization_opinion(SerializationEvidence(50, 0, 100)),
            form_cold_chain_opinion(ColdChainEvidence(tuple([5.0] * 50), 2.0, 8.0, 100)),
            form_shelf_life_opinion(ShelfLifeEvidence(200, 180)),
            form_documentation_opinion(DocumentationEvidence(
                frozenset(), DEFAULT_REQUIRED_DOCUMENTS, DEFAULT_CRITICAL_DOCUMENTS
            )),
            form_recall_opinion(RecallStatus.NO_RECALL),
        ]
        for omega in opinions:
            assert isinstance(omega, DrugIntegrityOpinion)
            assert isinstance(omega, Opinion)
            # Domain aliases work
            assert omega.integrity == omega.belief
            assert omega.degradation == omega.disbelief
