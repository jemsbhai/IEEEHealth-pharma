"""
EXP-008 tests: FormationParams threading and the sensitivity harness.

Validates:
- default FormationParams equal the previously hard-coded values and
  evaluate_path with explicit defaults equals evaluate_path without them
- each parameter reaches its formation function (changes the intended
  per-dimension opinion and nothing else)
- the grid is well-formed with the default first
- a smoke run reproduces the main-suite structure
"""

import numpy as np
import pytest

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import run_sensitivity as rs
from models.chain_integrity import (
    evaluate_path, evaluate_node, FormationParams, DEFAULT_FORMATION, COMPROMISED_CONDITIONAL,
)
from simulation.simulator import INSULIN, generate_normal_evidence, apply_failure, get_standard_scenarios

PATH = rs.EVAL_PATH


def _chain_evidence(seed=2, scenario=None):
    rng = np.random.default_rng(seed)
    ev = {n: generate_normal_evidence(INSULIN, rng=rng) for n in PATH}
    if scenario is not None:
        ev = apply_failure(ev, scenario, INSULIN, rng)
    return ev


def _opinion_tuple(op):
    return (op.belief, op.disbelief, op.uncertainty, op.base_rate)


class TestDefaults:

    def test_defaults_match_previous_hard_coded_values(self):
        d = DEFAULT_FORMATION
        assert (d.base_rate_serialization, d.base_rate_cold_chain, d.base_rate_shelf_life,
                d.base_rate_documentation, d.base_rate_recall) == (0.99, 0.95, 0.90, 0.80, 0.99)
        assert (d.critical_weight, d.shelf_life_scale, d.severity_cap) == (3.0, 10.0, 5.0)

    @pytest.mark.parametrize("sid", [None, "F2", "F3", "F5", "F6"])
    def test_explicit_defaults_equal_implicit(self, sid):
        scenario = get_standard_scenarios()[sid] if sid else None
        ev = _chain_evidence(3, scenario)
        a = evaluate_path(PATH, ev, weights=INSULIN.weights)
        b = evaluate_path(PATH, ev, weights=INSULIN.weights, formation=FormationParams())
        assert a.integrity_score == b.integrity_score
        assert a.uncertainty == b.uncertainty
        for n in PATH:
            for t, op in a.node_results[n].evidence_opinions.items():
                assert _opinion_tuple(op) == _opinion_tuple(b.node_results[n].evidence_opinions[t])


class TestParametersReachFunctions:

    def _node(self, ev, node, **kw):
        return evaluate_node(node, ev[node], weights=INSULIN.weights, formation=FormationParams(**kw))

    def test_critical_weight_changes_only_documentation(self):
        ev = _chain_evidence(4, get_standard_scenarios()["F6"])   # missing CoA at importer
        base = self._node(ev, "importer")
        low = self._node(ev, "importer", critical_weight=1.0)
        assert low.evidence_opinions["documentation"].disbelief < base.evidence_opinions["documentation"].disbelief
        for t in ("serialization", "cold_chain", "shelf_life", "recall"):
            assert _opinion_tuple(low.evidence_opinions[t]) == _opinion_tuple(base.evidence_opinions[t])

    def test_shelf_life_scale_changes_only_shelf_life(self):
        ev = _chain_evidence(5)
        base = self._node(ev, "importer")
        big = self._node(ev, "importer", shelf_life_scale=20.0)
        assert big.evidence_opinions["shelf_life"].uncertainty < base.evidence_opinions["shelf_life"].uncertainty
        for t in ("serialization", "cold_chain", "documentation", "recall"):
            assert _opinion_tuple(big.evidence_opinions[t]) == _opinion_tuple(base.evidence_opinions[t])

    def test_severity_cap_changes_only_cold_chain_under_severe_excursion(self):
        ev = _chain_evidence(6, get_standard_scenarios()["F2"])   # +17 C at distributor_a
        base = self._node(ev, "distributor_a")
        capped = self._node(ev, "distributor_a", severity_cap=2.0)
        assert capped.evidence_opinions["cold_chain"].disbelief < base.evidence_opinions["cold_chain"].disbelief
        for t in ("serialization", "shelf_life", "documentation", "recall"):
            assert _opinion_tuple(capped.evidence_opinions[t]) == _opinion_tuple(base.evidence_opinions[t])

    def test_base_rates_change_projection_not_disbelief(self):
        ev = _chain_evidence(7, get_standard_scenarios()["F3"])
        base = self._node(ev, "importer")
        flat = self._node(ev, "importer", base_rate_serialization=0.5, base_rate_cold_chain=0.5,
                          base_rate_shelf_life=0.5, base_rate_documentation=0.5, base_rate_recall=0.5)
        for t in ("serialization", "cold_chain", "shelf_life", "documentation"):
            assert flat.evidence_opinions[t].disbelief == pytest.approx(base.evidence_opinions[t].disbelief)
            assert flat.evidence_opinions[t].base_rate == 0.5
        assert flat.fused_opinion.projected_probability() < base.fused_opinion.projected_probability()

    def test_conditional_reaches_propagation(self):
        ev = _chain_evidence(8, get_standard_scenarios()["F2"])
        strict = evaluate_path(PATH, ev, weights=INSULIN.weights)
        relaxed = evaluate_path(PATH, ev, weights=INSULIN.weights,
                                compromised_conditional=rs.Opinion(0.0, 0.8, 0.2, 0.01))
        assert relaxed.integrity_score != strict.integrity_score


class TestGrid:

    def test_grid_shape(self):
        grid = rs.build_grid()
        names = [s.name for s in grid]
        assert names[0] == "default"
        assert len(names) == len(set(names)) == 16
        groups = {s.group for s in grid}
        assert groups == {"default", "conditional_base_rate", "conditional_disbelief", "critical_weight",
                          "shelf_life_scale", "prior_weight", "base_rates", "severity_cap"}

    def test_default_setting_is_the_published_configuration(self):
        s = rs.build_grid()[0]
        assert s.formation == DEFAULT_FORMATION
        assert _opinion_tuple(s.conditional) == _opinion_tuple(COMPROMISED_CONDITIONAL)
        assert s.base_prior_weight == 2.0

    def test_setting_serializes(self):
        d = rs.setting_to_dict(rs.build_grid()[1])
        assert d["conditional"]["a"] == 0.001 and d["conditional"]["d"] == 1.0


class TestSmoke:

    def test_evaluate_setting_small(self):
        rows, scores = rs.evaluate_setting(rs.build_grid()[0], INSULIN, n_trials=10, seed=1)
        assert [r.scenario for r in rows] == rs.SCENARIOS
        s = rs.summarize(rows, scores)
        assert set(s["mean_scores"]) == set(rs.SCENARIOS)
        assert 0.0 <= s["fpr"] <= 1.0 and 0.0 <= s["min_dr"] <= 1.0
