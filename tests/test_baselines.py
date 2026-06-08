"""
Tests for baseline methods (B1–B4).

Validates that each baseline:
- Produces valid scores
- Detects obvious failures
- Passes normal operations
- Has the expected limitations documented in the paper
"""

import pytest

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from baselines.baselines import (
    evaluate_threshold,
    evaluate_weighted_scoring,
    evaluate_bayesian,
    evaluate_dempster_shafer,
    evaluate_all_baselines,
)
from models.drug_integrity_opinion import (
    SerializationEvidence,
    ColdChainEvidence,
    ShelfLifeEvidence,
    DocumentationEvidence,
    RecallStatus,
    NodeEvidence,
    DEFAULT_REQUIRED_DOCUMENTS,
    DEFAULT_CRITICAL_DOCUMENTS,
)

PATH = ["mfg", "wh", "ph"]


def _good_evidence() -> NodeEvidence:
    return NodeEvidence(
        serialization=SerializationEvidence(100, 0, 100),
        cold_chain=ColdChainEvidence(tuple([5.0] * 100), 2.0, 8.0, 100),
        shelf_life=ShelfLifeEvidence(360, 180),
        documentation=DocumentationEvidence(
            DEFAULT_REQUIRED_DOCUMENTS, DEFAULT_REQUIRED_DOCUMENTS, DEFAULT_CRITICAL_DOCUMENTS,
        ),
        recall=RecallStatus.NO_RECALL,
    )


def _mild_excursion_evidence() -> NodeEvidence:
    """7.9°C on a 2-8°C range — BARELY within threshold."""
    readings = tuple([5.0] * 95 + [7.9] * 5)
    return NodeEvidence(
        serialization=SerializationEvidence(100, 0, 100),
        cold_chain=ColdChainEvidence(readings, 2.0, 8.0, 100),
        shelf_life=ShelfLifeEvidence(360, 180),
        documentation=DocumentationEvidence(
            DEFAULT_REQUIRED_DOCUMENTS, DEFAULT_REQUIRED_DOCUMENTS, DEFAULT_CRITICAL_DOCUMENTS,
        ),
        recall=RecallStatus.NO_RECALL,
    )


def _marginal_scan_evidence() -> NodeEvidence:
    """91% scan rate (above 90% threshold), all pass.

    Threshold says PASS. But 9 out of 100 packages went unscanned —
    the algebra correctly assigns uncertainty proportional to the
    unscanned fraction via trust discounting.
    """
    return NodeEvidence(
        serialization=SerializationEvidence(91, 0, 100),
        cold_chain=ColdChainEvidence(tuple([5.0] * 100), 2.0, 8.0, 100),
        shelf_life=ShelfLifeEvidence(360, 180),
        documentation=DocumentationEvidence(
            DEFAULT_REQUIRED_DOCUMENTS, DEFAULT_REQUIRED_DOCUMENTS, DEFAULT_CRITICAL_DOCUMENTS,
        ),
        recall=RecallStatus.NO_RECALL,
    )


class TestThresholdBaseline:
    """B1: Rule-based threshold."""

    def test_good_evidence_passes(self):
        evidence = {n: _good_evidence() for n in PATH}
        result = evaluate_threshold(PATH, evidence)
        assert result.chain_score == 1.0
        assert not result.detected

    def test_recall_detected(self):
        evidence = {n: _good_evidence() for n in PATH}
        evidence["wh"] = NodeEvidence(
            serialization=SerializationEvidence(100, 0, 100),
            cold_chain=ColdChainEvidence(tuple([5.0] * 100), 2.0, 8.0, 100),
            shelf_life=ShelfLifeEvidence(360, 180),
            documentation=DocumentationEvidence(
                DEFAULT_REQUIRED_DOCUMENTS, DEFAULT_REQUIRED_DOCUMENTS, DEFAULT_CRITICAL_DOCUMENTS,
            ),
            recall=RecallStatus.ACTIVE_RECALL,
        )
        result = evaluate_threshold(PATH, evidence)
        assert result.detected

    def test_borderline_temp_passes_threshold(self):
        """KEY LIMITATION: 7.9°C on 2–8°C range passes threshold.

        This is the paper's main argument: thresholds destroy information.
        The algebra would assign lower belief here than for 5°C.
        """
        evidence = {n: _good_evidence() for n in PATH}
        evidence["wh"] = _mild_excursion_evidence()
        result = evaluate_threshold(PATH, evidence)
        assert not result.detected, "Threshold should pass 7.9°C on 2-8°C range"

    def test_marginal_scan_passes_threshold(self):
        """KEY LIMITATION: 91% scan rate passes the 90% threshold.

        9 out of 100 packages unscanned — threshold says PASS.
        The algebra would correctly assign uncertainty proportional
        to the 9% unscanned fraction via trust discounting.
        """
        evidence = {n: _good_evidence() for n in PATH}
        evidence["wh"] = _marginal_scan_evidence()
        result = evaluate_threshold(PATH, evidence)
        assert not result.detected, "91% scan rate should pass 90% threshold"


class TestWeightedScoringBaseline:
    """B2: Weighted scoring."""

    def test_good_evidence_high_score(self):
        evidence = {n: _good_evidence() for n in PATH}
        result = evaluate_weighted_scoring(PATH, evidence)
        assert result.chain_score > 0.8
        assert not result.detected

    def test_marginal_scan_reduces_score(self):
        """Weighted scoring captures partial scan coverage as a lower score."""
        evidence_good = {n: _good_evidence() for n in PATH}
        evidence_partial = {n: _good_evidence() for n in PATH}
        evidence_partial["wh"] = _marginal_scan_evidence()

        good = evaluate_weighted_scoring(PATH, evidence_good)
        partial = evaluate_weighted_scoring(PATH, evidence_partial)
        assert partial.chain_score < good.chain_score

    def test_missing_data_inflates_score(self):
        """KEY LIMITATION: missing evidence → score 1.0 (assumed good)."""
        evidence = {n: NodeEvidence(recall=RecallStatus.NO_RECALL) for n in PATH}
        result = evaluate_weighted_scoring(PATH, evidence)
        # Missing data produces high scores — a dangerous false sense of security
        assert result.chain_score > 0.8


class TestBayesianBaseline:
    """B3: Bayesian probabilistic."""

    def test_good_evidence_high_probability(self):
        evidence = {n: _good_evidence() for n in PATH}
        result = evaluate_bayesian(PATH, evidence)
        assert result.chain_score > 0.3  # product of probabilities, so lower
        assert not result.detected

    def test_recall_detected(self):
        evidence = {n: _good_evidence() for n in PATH}
        evidence["wh"] = NodeEvidence(
            serialization=SerializationEvidence(100, 0, 100),
            cold_chain=ColdChainEvidence(tuple([5.0] * 100), 2.0, 8.0, 100),
            shelf_life=ShelfLifeEvidence(360, 180),
            documentation=DocumentationEvidence(
                DEFAULT_REQUIRED_DOCUMENTS, DEFAULT_REQUIRED_DOCUMENTS, DEFAULT_CRITICAL_DOCUMENTS,
            ),
            recall=RecallStatus.ACTIVE_RECALL,
        )
        result = evaluate_bayesian(PATH, evidence)
        assert result.detected

    def test_missing_data_produces_0_5(self):
        """KEY LIMITATION: missing data → P=0.5 (uninformative prior).

        Unlike the algebra's vacuous opinion, 0.5 gets multiplied into
        the chain score, potentially pushing it below detection threshold
        even without negative evidence.
        """
        evidence = {n: NodeEvidence(recall=RecallStatus.NO_RECALL) for n in PATH}
        result = evaluate_bayesian(PATH, evidence)
        # Chain score is product of 0.5^5 per node × 3 nodes ≈ very low
        assert result.chain_score < 0.1  # demonstrates the overreaction


class TestUnifiedEvaluator:
    """Test evaluate_all_baselines."""

    def test_returns_all_three(self):
        evidence = {n: _good_evidence() for n in PATH}
        results = evaluate_all_baselines(PATH, evidence)
        assert "B1_Threshold" in results
        assert "B2_WeightedScoring" in results
        assert "B3_Bayesian" in results
        assert "B4_DempsterShafer" in results


class TestDempsterShaferBaseline:
    """Test B4 Dempster-Shafer evidence theory baseline."""

    def test_good_evidence_high_score(self):
        evidence = {n: _good_evidence() for n in PATH}
        result = evaluate_dempster_shafer(PATH, evidence)
        # Good evidence should produce high pignistic probability
        assert result.chain_score > 0.7
        assert result.method == "B4_DempsterShafer"

    def test_recall_zadeh_paradox(self):
        """KEY FINDING: Zadeh's paradox — D-S fails to detect active recall.

        When 4 evidence types strongly support 'safe' and recall strongly
        supports 'compromised', Dempster's rule normalizes away the conflict.
        The chain score remains high (~1.0) despite an active recall.

        This is a known limitation of D-S theory (Zadeh, 1979) and a key
        advantage of the SL algebra, which handles recall as a dogmatic
        override via form_recall_opinion.
        """
        evidence = {n: _good_evidence() for n in PATH}
        evidence["ph"] = NodeEvidence(
            serialization=SerializationEvidence(100, 0, 100),
            cold_chain=ColdChainEvidence(tuple([5.0] * 100), 2.0, 8.0, 100),
            shelf_life=ShelfLifeEvidence(360, 180),
            documentation=DocumentationEvidence(
                DEFAULT_REQUIRED_DOCUMENTS, DEFAULT_REQUIRED_DOCUMENTS,
                DEFAULT_CRITICAL_DOCUMENTS,
            ),
            recall=RecallStatus.ACTIVE_RECALL,
        )
        result = evaluate_dempster_shafer(PATH, evidence)
        # D-S normalizes away the conflict: score stays HIGH despite recall.
        # This is Zadeh's paradox — a known D-S limitation.
        assert result.chain_score > 0.9, (
            "D-S should exhibit Zadeh's paradox: recall overwhelmed by "
            "4 strong safe sources due to conflict normalization"
        )

    def test_missing_data_produces_uncertainty(self):
        evidence = {n: NodeEvidence() for n in PATH}  # all None
        result = evaluate_dempster_shafer(PATH, evidence)
        # Vacuous mass functions → BetP = 0.5 (maximum uncertainty)
        assert abs(result.chain_score - 0.5) < 0.01

    def test_mass_function_sums_to_one(self):
        from baselines.baselines import (
            MassFunction, _ds_serialization, _ds_cold_chain,
            _ds_shelf_life, _ds_documentation, _ds_recall,
            dempster_combine,
        )
        # Check individual mass functions
        ev = _good_evidence()
        for fn in [
            lambda: _ds_serialization(ev.serialization),
            lambda: _ds_cold_chain(ev.cold_chain),
            lambda: _ds_shelf_life(ev.shelf_life),
            lambda: _ds_documentation(ev.documentation),
            lambda: _ds_recall(ev.recall),
        ]:
            m = fn()
            assert abs(m.m_safe + m.m_compromised + m.m_theta - 1.0) < 1e-9

        # Check combined mass function
        m1 = _ds_serialization(ev.serialization)
        m2 = _ds_cold_chain(ev.cold_chain)
        combined = dempster_combine(m1, m2)
        assert abs(combined.m_safe + combined.m_compromised + combined.m_theta - 1.0) < 1e-9

    def test_pignistic_probability_bounds(self):
        from baselines.baselines import MassFunction
        # BetP should always be in [0, 1]
        m = MassFunction(0.3, 0.2, 0.5)
        assert 0.0 <= m.pignistic_safe() <= 1.0
        # Vacuous → 0.5
        m_vac = MassFunction.vacuous()
        assert abs(m_vac.pignistic_safe() - 0.5) < 1e-9
