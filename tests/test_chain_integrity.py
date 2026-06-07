"""
Tests for Supply Chain DAG and Chain Integrity Algebra (Definitions 4–7).

Validates:
- DAG construction, cycle detection, path enumeration
- Within-node fusion (Definition 4)
- Cross-node propagation (Definition 5)
- Missing evidence handling (Definition 7)
- Properties P1–P5 at the chain level
"""

import pytest

from jsonld_ex.confidence_algebra import Opinion

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from data.supply_chain_dag import (
    SupplyChainDAG,
    SupplyChainNode,
    SupplyChainEdge,
    NodeType,
)
from models.drug_integrity_opinion import (
    DrugIntegrityOpinion,
    SerializationEvidence,
    ColdChainEvidence,
    ShelfLifeEvidence,
    DocumentationEvidence,
    RecallStatus,
    NodeEvidence,
    EvidenceWeights,
    DEFAULT_REQUIRED_DOCUMENTS,
    DEFAULT_CRITICAL_DOCUMENTS,
)
from models.chain_integrity import (
    evaluate_node,
    evaluate_path,
    evaluate_chain,
    localize_failure,
    NodeEvaluationResult,
    ChainEvaluationResult,
)


# ═══════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════


def _make_simple_dag() -> SupplyChainDAG:
    """Three-node linear chain: manufacturer → warehouse → pharmacy."""
    dag = SupplyChainDAG()
    dag.add_node(SupplyChainNode("mfg", NodeType.MANUFACTURER))
    dag.add_node(SupplyChainNode("wh", NodeType.WAREHOUSE))
    dag.add_node(SupplyChainNode("ph", NodeType.PHARMACY))
    dag.add_edge(SupplyChainEdge("mfg", "wh"))
    dag.add_edge(SupplyChainEdge("wh", "ph"))
    return dag


def _make_branching_dag() -> SupplyChainDAG:
    """Tatmeen-realistic topology from base.yaml."""
    dag = SupplyChainDAG()
    for nid, ntype in [
        ("manufacturer", NodeType.MANUFACTURER),
        ("importer", NodeType.IMPORTER),
        ("central_warehouse", NodeType.WAREHOUSE),
        ("distributor_a", NodeType.DISTRIBUTOR),
        ("distributor_b", NodeType.DISTRIBUTOR),
        ("hospital", NodeType.PHARMACY),
        ("pharmacy_a", NodeType.PHARMACY),
        ("pharmacy_b", NodeType.PHARMACY),
    ]:
        dag.add_node(SupplyChainNode(nid, ntype))

    for src, tgt in [
        ("manufacturer", "importer"),
        ("importer", "central_warehouse"),
        ("central_warehouse", "distributor_a"),
        ("central_warehouse", "distributor_b"),
        ("distributor_a", "hospital"),
        ("distributor_a", "pharmacy_a"),
        ("distributor_b", "pharmacy_b"),
    ]:
        dag.add_edge(SupplyChainEdge(src, tgt))

    return dag


def _good_evidence() -> NodeEvidence:
    """Normal operation evidence: all scanned, in-range temp, good shelf life, full docs."""
    return NodeEvidence(
        serialization=SerializationEvidence(100, 0, 100),
        cold_chain=ColdChainEvidence(
            readings=tuple([5.0] * 100),
            required_min=2.0, required_max=8.0, expected_readings=100,
        ),
        shelf_life=ShelfLifeEvidence(remaining_days=360, regulatory_min=180),
        documentation=DocumentationEvidence(
            documents_present=DEFAULT_REQUIRED_DOCUMENTS,
            documents_required=DEFAULT_REQUIRED_DOCUMENTS,
            documents_critical=DEFAULT_CRITICAL_DOCUMENTS,
        ),
        recall=RecallStatus.NO_RECALL,
    )


def _bad_cold_chain_evidence() -> NodeEvidence:
    """Evidence with severe temperature excursion."""
    return NodeEvidence(
        serialization=SerializationEvidence(100, 0, 100),
        cold_chain=ColdChainEvidence(
            readings=tuple([5.0] * 50 + [25.0] * 50),  # severe excursion
            required_min=2.0, required_max=8.0, expected_readings=100,
        ),
        shelf_life=ShelfLifeEvidence(remaining_days=360, regulatory_min=180),
        documentation=DocumentationEvidence(
            documents_present=DEFAULT_REQUIRED_DOCUMENTS,
            documents_required=DEFAULT_REQUIRED_DOCUMENTS,
            documents_critical=DEFAULT_CRITICAL_DOCUMENTS,
        ),
        recall=RecallStatus.NO_RECALL,
    )


# ═══════════════════════════════════════════════════════════════════
# DAG TESTS
# ═══════════════════════════════════════════════════════════════════


class TestSupplyChainDAG:
    """Tests for DAG construction and queries."""

    def test_simple_dag_construction(self):
        dag = _make_simple_dag()
        assert len(dag.nodes) == 3
        assert len(dag.edges) == 2

    def test_sources_and_sinks(self):
        dag = _make_simple_dag()
        assert dag.sources() == ["mfg"]
        assert dag.sinks() == ["ph"]

    def test_branching_dag_paths(self):
        dag = _make_branching_dag()
        paths = dag.enumerate_paths()
        assert len(paths) == 3  # hospital, pharmacy_a, pharmacy_b

    def test_cycle_detection(self):
        dag = _make_simple_dag()
        with pytest.raises(ValueError, match="cycle"):
            dag.add_edge(SupplyChainEdge("ph", "mfg"))

    def test_topological_sort(self):
        dag = _make_simple_dag()
        order = dag.topological_sort()
        assert order.index("mfg") < order.index("wh")
        assert order.index("wh") < order.index("ph")

    def test_path_to_specific_sink(self):
        dag = _make_branching_dag()
        paths = dag.enumerate_paths(sink="hospital")
        assert len(paths) == 1
        assert paths[0][-1] == "hospital"


# ═══════════════════════════════════════════════════════════════════
# WITHIN-NODE FUSION TESTS (Definition 4)
# ═══════════════════════════════════════════════════════════════════


class TestWithinNodeFusion:
    """Tests for evaluate_node (Definition 4)."""

    def test_good_evidence_high_integrity(self):
        """Normal operation → high integrity score."""
        result = evaluate_node("wh", _good_evidence())
        pp = result.fused_opinion.projected_probability()
        assert pp > 0.8, f"Expected high integrity, got P(ω) = {pp}"

    def test_bad_cold_chain_reduces_integrity(self):
        """Severe temperature excursion → reduced integrity."""
        good_result = evaluate_node("wh", _good_evidence())
        bad_result = evaluate_node("wh", _bad_cold_chain_evidence())

        good_pp = good_result.fused_opinion.projected_probability()
        bad_pp = bad_result.fused_opinion.projected_probability()

        assert bad_pp < good_pp, (
            f"Bad cold chain should reduce integrity: {bad_pp} >= {good_pp}"
        )

    def test_recall_overrides_everything(self):
        """Active recall → chain-level disbelief regardless of other evidence.

        This tests Property P5 (override): the dogmatic fusion dominance
        property of SL ensures that a dogmatic disbelief opinion (from
        active recall) produces a near-zero integrity score even when
        all other evidence is perfect.
        """
        ev = NodeEvidence(
            serialization=SerializationEvidence(100, 0, 100),
            cold_chain=ColdChainEvidence(
                tuple([5.0] * 100), 2.0, 8.0, 100,
            ),
            shelf_life=ShelfLifeEvidence(360, 180),
            documentation=DocumentationEvidence(
                DEFAULT_REQUIRED_DOCUMENTS,
                DEFAULT_REQUIRED_DOCUMENTS,
                DEFAULT_CRITICAL_DOCUMENTS,
            ),
            recall=RecallStatus.ACTIVE_RECALL,  # RECALL
        )
        result = evaluate_node("wh", ev)
        pp = result.fused_opinion.projected_probability()
        assert pp < 0.15, f"Recall should override: P(ω) = {pp}"

    def test_missing_evidence_increases_uncertainty(self):
        """Missing evidence → higher uncertainty (Definition 7)."""
        full_ev = _good_evidence()
        sparse_ev = NodeEvidence(
            serialization=SerializationEvidence(100, 0, 100),
            # cold_chain missing
            # shelf_life missing
            # documentation missing
            recall=RecallStatus.NO_RECALL,
        )

        full_result = evaluate_node("wh", full_ev)
        sparse_result = evaluate_node("wh", sparse_ev)

        assert sparse_result.fused_opinion.uncertainty > full_result.fused_opinion.uncertainty
        assert "cold_chain" in sparse_result.missing_evidence
        assert "shelf_life" in sparse_result.missing_evidence
        assert "documentation" in sparse_result.missing_evidence

    def test_all_evidence_opinions_tracked(self):
        """Node result tracks all five evidence opinions for traceability."""
        result = evaluate_node("wh", _good_evidence())
        assert "serialization" in result.evidence_opinions
        assert "cold_chain" in result.evidence_opinions
        assert "shelf_life" in result.evidence_opinions
        assert "documentation" in result.evidence_opinions
        assert "recall" in result.evidence_opinions

    def test_evidence_weights_affect_fusion(self):
        """Different weight profiles produce different opinions."""
        ev = _bad_cold_chain_evidence()

        biologic_result = evaluate_node("wh", ev, weights=EvidenceWeights.for_biologic())
        conventional_result = evaluate_node("wh", ev, weights=EvidenceWeights.for_conventional())

        bio_pp = biologic_result.fused_opinion.projected_probability()
        conv_pp = conventional_result.fused_opinion.projected_probability()

        # Biologic weights cold chain MORE heavily, so bad cold chain
        # should hurt biologic more than conventional
        assert bio_pp < conv_pp, (
            f"Biologic should be more affected by cold chain failure: "
            f"bio={bio_pp:.4f}, conv={conv_pp:.4f}"
        )

    def test_fused_opinion_valid(self):
        """Fused opinion satisfies b + d + u = 1."""
        result = evaluate_node("wh", _good_evidence())
        op = result.fused_opinion
        total = op.belief + op.disbelief + op.uncertainty
        assert abs(total - 1.0) < 1e-9


# ═══════════════════════════════════════════════════════════════════
# CROSS-NODE PROPAGATION TESTS (Definition 5)
# ═══════════════════════════════════════════════════════════════════


class TestCrossNodePropagation:
    """Tests for evaluate_path and evaluate_chain (Definition 5)."""

    def test_all_good_chain_high_integrity(self):
        """All nodes with good evidence → high chain integrity."""
        path = ["mfg", "wh", "ph"]
        evidence = {nid: _good_evidence() for nid in path}

        result = evaluate_path(path, evidence)
        assert result.integrity_score > 0.7

    def test_one_bad_node_reduces_chain(self):
        """One node with bad evidence → reduced chain integrity.

        This tests the trust discounting propagation: upstream issues
        dilute downstream confidence.
        """
        path = ["mfg", "wh", "ph"]
        good_evidence = {nid: _good_evidence() for nid in path}
        mixed_evidence = {
            "mfg": _good_evidence(),
            "wh": _bad_cold_chain_evidence(),  # bad warehouse
            "ph": _good_evidence(),
        }

        good_result = evaluate_path(path, good_evidence)
        mixed_result = evaluate_path(path, mixed_evidence)

        assert mixed_result.integrity_score < good_result.integrity_score

    def test_upstream_failure_propagates_downstream(self):
        """Failure at upstream node propagates uncertainty downstream.

        Property P2 (compositionality): modifying evidence at node n_i
        affects the chain opinion only through ω_node(n_i) and its
        downstream propagation.
        """
        path = ["mfg", "wh", "ph"]

        # Bad manufacturer
        ev_bad_mfg = {
            "mfg": _bad_cold_chain_evidence(),
            "wh": _good_evidence(),
            "ph": _good_evidence(),
        }

        # Bad pharmacy
        ev_bad_ph = {
            "mfg": _good_evidence(),
            "wh": _good_evidence(),
            "ph": _bad_cold_chain_evidence(),
        }

        result_bad_mfg = evaluate_path(path, ev_bad_mfg)
        result_bad_ph = evaluate_path(path, ev_bad_ph)

        # Both should have reduced integrity
        good_result = evaluate_path(path, {nid: _good_evidence() for nid in path})
        assert result_bad_mfg.integrity_score < good_result.integrity_score
        assert result_bad_ph.integrity_score < good_result.integrity_score

    def test_weakest_node_identified(self):
        """Chain evaluation correctly identifies the weakest node."""
        path = ["mfg", "wh", "ph"]
        evidence = {
            "mfg": _good_evidence(),
            "wh": _bad_cold_chain_evidence(),
            "ph": _good_evidence(),
        }
        result = evaluate_path(path, evidence)
        assert result.weakest_node == "wh"

    def test_chain_opinion_valid(self):
        """Chain opinion satisfies b + d + u = 1."""
        path = ["mfg", "wh", "ph"]
        evidence = {nid: _good_evidence() for nid in path}
        result = evaluate_path(path, evidence)
        op = result.chain_opinion
        total = op.belief + op.disbelief + op.uncertainty
        assert abs(total - 1.0) < 1e-9

    def test_evaluate_full_dag(self):
        """Full DAG evaluation produces results for all sinks."""
        dag = _make_branching_dag()
        evidence = {nid: _good_evidence() for nid in dag.node_ids}

        results = evaluate_chain(dag, evidence)

        # Should have results for all three sinks
        assert "hospital" in results
        assert "pharmacy_a" in results
        assert "pharmacy_b" in results

    def test_dag_failure_localization(self):
        """Localization correctly flags the problematic node."""
        dag = _make_branching_dag()
        evidence = {nid: _good_evidence() for nid in dag.node_ids}
        evidence["central_warehouse"] = _bad_cold_chain_evidence()

        results = evaluate_chain(dag, evidence)

        # Check localization for hospital path
        hospital_result = results["hospital"]
        flagged = localize_failure(hospital_result)
        assert "central_warehouse" in flagged


# ═══════════════════════════════════════════════════════════════════
# CHAIN-LEVEL PROPERTY TESTS
# ═══════════════════════════════════════════════════════════════════


class TestChainLevelProperties:
    """Tests for chain-level algebraic properties P1–P5."""

    def test_p1_monotonicity(self):
        """P1: Adding positive evidence can only increase belief or decrease u."""
        path = ["mfg", "wh", "ph"]

        # Sparse evidence (some missing)
        sparse = {
            "mfg": NodeEvidence(
                serialization=SerializationEvidence(50, 0, 100),
                recall=RecallStatus.NO_RECALL,
            ),
            "wh": NodeEvidence(recall=RecallStatus.NO_RECALL),
            "ph": NodeEvidence(recall=RecallStatus.NO_RECALL),
        }

        # Rich evidence (all present, all good)
        rich = {nid: _good_evidence() for nid in path}

        sparse_result = evaluate_path(path, sparse)
        rich_result = evaluate_path(path, rich)

        # More evidence → higher belief or lower uncertainty
        assert rich_result.integrity_score >= sparse_result.integrity_score - 1e-9

    def test_p4_uncertainty_conservation(self):
        """P4: Missing evidence produces quantifiable uncertainty."""
        path = ["mfg", "wh", "ph"]

        # All evidence present
        full = {nid: _good_evidence() for nid in path}
        # All evidence missing
        empty = {nid: NodeEvidence(recall=RecallStatus.NO_RECALL) for nid in path}

        full_result = evaluate_path(path, full)
        empty_result = evaluate_path(path, empty)

        # Missing evidence → higher uncertainty
        assert empty_result.uncertainty > full_result.uncertainty

    def test_p5_recall_override_chain_level(self):
        """P5: Active recall at ANY node produces chain-level disbelief."""
        path = ["mfg", "wh", "ph"]
        evidence = {
            "mfg": _good_evidence(),
            "wh": NodeEvidence(
                serialization=SerializationEvidence(100, 0, 100),
                cold_chain=ColdChainEvidence(
                    tuple([5.0] * 100), 2.0, 8.0, 100,
                ),
                shelf_life=ShelfLifeEvidence(360, 180),
                documentation=DocumentationEvidence(
                    DEFAULT_REQUIRED_DOCUMENTS,
                    DEFAULT_REQUIRED_DOCUMENTS,
                    DEFAULT_CRITICAL_DOCUMENTS,
                ),
                recall=RecallStatus.ACTIVE_RECALL,  # RECALL at warehouse
            ),
            "ph": _good_evidence(),
        }
        result = evaluate_path(path, evidence)
        # Recall should severely reduce chain integrity
        assert result.integrity_score < 0.2, (
            f"Chain with recall should have low integrity: {result.integrity_score}"
        )
