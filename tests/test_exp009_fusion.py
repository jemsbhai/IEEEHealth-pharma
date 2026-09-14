"""
EXP-009 tests for scripts/run_fusion.py.

Validates:
- C1-C3 inject at exactly the listed node and change exactly the listed
  evidence types, and C3's document loss is non-critical
- paired base evidence is reproducible and shared across scenarios
- averaging fusion returns a valid chain with higher uncertainty
- the paired compound check and correlation helpers on synthetic data
- a smoke run of Part A at small n
"""

import numpy as np
import pytest

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import run_fusion as rf
from simulation.simulator import INSULIN, apply_failure
from models.chain_integrity import evaluate_path

PATH = rf.EVAL_PATH


def _changed_fields(a, b):
    return {f for f in ("serialization", "cold_chain", "shelf_life", "documentation", "recall")
            if getattr(a, f) != getattr(b, f)}


class TestCompoundScenarios:

    @pytest.mark.parametrize("cid,node,fields", [
        ("C1", "importer", {"serialization", "documentation"}),
        ("C2", "distributor_a", {"cold_chain", "shelf_life"}),
        ("C3", "central_warehouse", {"serialization", "documentation"}),
    ])
    def test_compound_changes_exactly_the_listed_types_at_one_node(self, cid, node, fields):
        base = rf.base_evidence_sets(INSULIN, 1, seed=5)[0]
        sc = rf.compound_scenarios(INSULIN)[cid]
        inj = apply_failure(base, sc, INSULIN, np.random.default_rng(1))
        for n in PATH:
            changed = _changed_fields(base[n], inj[n])
            assert changed == (fields if n == node else set())
        assert sc.localization_target_set() == (node,)

    def test_c1_removes_certificate_of_analysis_and_injects_counterfeit(self):
        base = rf.base_evidence_sets(INSULIN, 1, seed=5)[0]
        inj = apply_failure(base, rf.compound_scenarios(INSULIN)["C1"], INSULIN, np.random.default_rng(1))
        imp = inj["importer"]
        assert imp.serialization.scanned_failure == 5
        assert "certificate_of_analysis" not in imp.documentation.documents_present

    def test_c3_document_loss_is_non_critical(self):
        base = rf.base_evidence_sets(INSULIN, 1, seed=5)[0]
        inj = apply_failure(base, rf.compound_scenarios(INSULIN)["C3"], INSULIN, np.random.default_rng(1))
        cw = inj["central_warehouse"]
        assert "import_license" not in cw.documentation.documents_present
        assert cw.documentation.documents_critical.issubset(cw.documentation.documents_present)
        scanned = cw.serialization.scanned_success + cw.serialization.scanned_failure
        assert scanned == 20

    def test_c2_uses_product_relative_f5(self):
        sc = rf.compound_scenarios(INSULIN)["C2"]
        f5 = sc.params["sub_scenarios"][1]
        assert f5.params["remaining_days"] == int(INSULIN.shelf_life_min_pharmacy * 0.5)


class TestPairedEvidence:

    def test_base_sets_reproducible(self):
        a = rf.base_evidence_sets(INSULIN, 3, seed=9)
        b = rf.base_evidence_sets(INSULIN, 3, seed=9)
        assert a == b

    def test_injection_leaves_base_untouched_and_is_seeded_per_trial(self):
        base = rf.base_evidence_sets(INSULIN, 2, seed=9)
        sc = rf.all_scenarios(INSULIN)["F1"]
        x = rf.inject(base[0], sc, INSULIN, 0, seed=9)
        y = rf.inject(base[0], sc, INSULIN, 0, seed=9)
        z = rf.inject(base[0], sc, INSULIN, 1, seed=9)
        assert x == y
        assert x != z                       # different trial index, different block position
        assert rf.inject(base[0], None, INSULIN, 0, seed=9) is base[0]


class TestOperators:

    def test_averaging_path_is_valid_and_more_uncertain(self):
        base = rf.base_evidence_sets(INSULIN, 1, seed=3)[0]
        cum = evaluate_path(PATH, base, weights=INSULIN.weights)
        avg = rf.OPERATORS["averaging"](PATH, base, weights=INSULIN.weights)
        assert 0.0 <= avg.integrity_score <= 1.0
        assert avg.uncertainty > cum.uncertainty
        assert set(avg.node_results) == set(PATH)


class TestHelpers:

    def test_paired_compound_check(self):
        scores = {"F3": [0.9, 0.8, 0.7], "F6": [0.95, 0.85, 0.6],
                  "C1": [0.85, 0.8, 0.55], "F1": [1, 1, 1], "F5": [1, 1, 1], "C2": [1, 1, 1],
                  "F4": [1, 1, 1], "F6nc": [1, 1, 1], "C3": [1, 1, 1]}
        out = rf.paired_compound_check(scores)
        assert out["C1"]["fraction_at_most_worse_component"] == pytest.approx(1.0)
        assert out["C1"]["mean_gap"] == pytest.approx(np.mean([0.9 - 0.85, 0.8 - 0.8, 0.6 - 0.55]))
        # a compound that is milder than its worse component fails the check
        scores["C1"] = [0.85, 0.8, 0.65]
        assert rf.paired_compound_check(scores)["C1"]["fraction_at_most_worse_component"] == pytest.approx(2 / 3)

    def test_correlation_matrix_flags_constant_columns(self):
        rng = np.random.default_rng(0)
        x = rng.normal(size=5000)
        cols = {"a": x, "b": rng.normal(size=5000), "c": x * 2.0, "d": np.zeros(5000)}
        c = rf.correlation_matrix(cols)
        assert c["matrix"]["a"]["c"] == pytest.approx(1.0)
        assert abs(c["matrix"]["a"]["b"]) < 0.05
        assert c["matrix"]["a"]["d"] is None
        assert c["constant_types"] == ["d"]
        assert c["max_abs_offdiag_defined"] == pytest.approx(1.0)


class TestSmoke:

    def test_part_a_small(self):
        rows, scores, thresholds = rf.run_part_a(INSULIN, n_trials=8, seed=1, verbose=False)
        assert set(thresholds) == {"cumulative", "averaging"}
        n_sc = len(rf.all_scenarios(INSULIN))
        assert len(rows) == 2 * n_sc
        for r in rows:
            rate = r.false_positive_rate if r.scenario == "Normal" else r.detection_rate
            assert r.rate_ci[0] <= rate <= r.rate_ci[1]
        chk = rf.paired_compound_check(scores["cumulative"])
        assert set(chk) == {"C1", "C2", "C3"}
        ag = rf.operator_agreement(scores)
        assert ag["n_scenarios"] == 11
