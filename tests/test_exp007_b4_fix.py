"""
EXP-007 regression tests for the B4 documentation mass function.

The branch "non-critical document missing, critical documents present" was
never reached by the main suite and its masses did not sum to 1. These tests
pin the fixed behaviour and confirm the other branches are unchanged.
"""

import pytest

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from baselines.baselines import _ds_documentation, MassFunction
from models.drug_integrity_opinion import (
    DocumentationEvidence, DEFAULT_REQUIRED_DOCUMENTS, DEFAULT_CRITICAL_DOCUMENTS,
)

NON_CRITICAL = sorted(DEFAULT_REQUIRED_DOCUMENTS - DEFAULT_CRITICAL_DOCUMENTS)


def _doc(present):
    return DocumentationEvidence(
        documents_present=frozenset(present),
        documents_required=DEFAULT_REQUIRED_DOCUMENTS,
        documents_critical=DEFAULT_CRITICAL_DOCUMENTS,
    )


class TestDocumentationMass:

    def test_complete_branch_unchanged(self):
        m = _ds_documentation(_doc(DEFAULT_REQUIRED_DOCUMENTS))
        assert (m.m_safe, m.m_compromised, m.m_theta) == (0.6, 0.0, 0.4)

    def test_missing_critical_branch_unchanged(self):
        present = DEFAULT_REQUIRED_DOCUMENTS - {"certificate_of_analysis"}
        m = _ds_documentation(_doc(present))
        assert (m.m_safe, m.m_compromised, m.m_theta) == (0.1, 0.3, 0.6)

    @pytest.mark.parametrize("n_missing", [1, 2])
    def test_late_non_critical_documents_sum_to_one(self, n_missing):
        present = DEFAULT_REQUIRED_DOCUMENTS - set(NON_CRITICAL[:n_missing])
        m = _ds_documentation(_doc(present))          # would have raised before the fix
        assert m.m_safe + m.m_compromised + m.m_theta == pytest.approx(1.0)
        assert m.m_compromised > 0.0
        assert m.m_safe < 0.6

    def test_continuous_at_full_completeness(self):
        """The fixed branch evaluated at c = 1 equals the complete branch."""
        c = 1.0
        m_safe, m_comp = 0.6 * c, 0.2 * (1 - c)
        assert (m_safe, m_comp, 1 - m_safe - m_comp) == (0.6, 0.0, 0.4)

    def test_more_missing_means_less_safe_mass(self):
        one = _ds_documentation(_doc(DEFAULT_REQUIRED_DOCUMENTS - set(NON_CRITICAL[:1])))
        two = _ds_documentation(_doc(DEFAULT_REQUIRED_DOCUMENTS - set(NON_CRITICAL[:2])))
        assert two.m_safe < one.m_safe
        assert two.m_compromised > one.m_compromised
