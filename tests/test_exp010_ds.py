"""
EXP-010 tests: flat vs hierarchical Dempster combination and conflict reporting.
"""

import numpy as np
import pytest

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from baselines.baselines import (
    MassFunction, dempster_combine, dempster_combine_multi, dempster_conflict,
    combine_with_conflict_count, collect_masses, evaluate_dempster_shafer,
    evaluate_dempster_shafer_flat, TOTAL_CONFLICT_K,
)
from simulation.simulator import INSULIN, generate_normal_evidence, apply_failure, get_standard_scenarios
import run_ds_check as dc

PATH = dc.EVAL_PATH


def _random_masses(rng, n):
    out = []
    for _ in range(n):
        p = rng.dirichlet([1.0, 1.0, 1.0])
        out.append(MassFunction(float(p[0]), float(p[1]), float(p[2])))
    return out


class TestAlgebra:

    def test_conflict_formula(self):
        a = MassFunction(0.7, 0.1, 0.2)
        b = MassFunction(0.2, 0.5, 0.3)
        assert dempster_conflict(a, b) == pytest.approx(0.7 * 0.5 + 0.1 * 0.2)

    def test_associativity_on_random_masses(self):
        rng = np.random.default_rng(0)
        for _ in range(200):
            a, b, c = _random_masses(rng, 3)
            left = dempster_combine(dempster_combine(a, b), c)
            right = dempster_combine(a, dempster_combine(b, c))
            assert left.pignistic_safe() == pytest.approx(right.pignistic_safe(), abs=1e-9)

    def test_commutativity(self):
        rng = np.random.default_rng(1)
        a, b = _random_masses(rng, 2)
        assert dempster_combine(a, b).pignistic_safe() == pytest.approx(dempster_combine(b, a).pignistic_safe(), abs=1e-12)

    def test_grouped_equals_flat_on_25_random_masses(self):
        rng = np.random.default_rng(2)
        masses = _random_masses(rng, 25)
        flat = dempster_combine_multi(*masses)
        node_masses = [dempster_combine_multi(*masses[i * 5:(i + 1) * 5]) for i in range(5)]
        hier = dempster_combine_multi(*node_masses)
        assert flat.pignistic_safe() == pytest.approx(hier.pignistic_safe(), abs=1e-9)

    def test_counter_reports_total_conflict(self):
        a = MassFunction(1.0, 0.0, 0.0)
        b = MassFunction(0.0, 1.0, 0.0)
        m, events, max_k = combine_with_conflict_count([a, b])
        assert events == 1 and max_k == pytest.approx(1.0)
        assert (m.m_safe, m.m_compromised, m.m_theta) == (0.0, 0.0, 1.0)   # vacuous fallback
        assert max_k >= TOTAL_CONFLICT_K

    def test_counter_matches_multi_combine_without_conflict(self):
        rng = np.random.default_rng(3)
        masses = _random_masses(rng, 7)
        m, events, _ = combine_with_conflict_count(masses)
        assert events == 0
        assert m.pignistic_safe() == pytest.approx(dempster_combine_multi(*masses).pignistic_safe(), abs=1e-12)


class TestEvaluators:

    @pytest.mark.parametrize("sid", [None, "F2", "F3", "F6", "F7"])
    def test_flat_equals_hierarchical_on_simulated_chains(self, sid):
        scenario = get_standard_scenarios()[sid] if sid else None
        rng = np.random.default_rng(4)
        for _ in range(5):
            ev = {n: generate_normal_evidence(INSULIN, rng=rng) for n in PATH}
            if scenario is not None:
                ev = apply_failure(ev, scenario, INSULIN, rng)
            h = evaluate_dempster_shafer(PATH, ev)
            f = evaluate_dempster_shafer_flat(PATH, ev)
            assert f.chain_score == pytest.approx(h.chain_score, abs=1e-10)
            assert f.node_scores == h.node_scores
            assert f.weakest_node == h.weakest_node
            assert f.method == "B4_DempsterShafer_flat"

    def test_collect_masses_shape(self):
        rng = np.random.default_rng(5)
        ev = {n: generate_normal_evidence(INSULIN, rng=rng) for n in PATH}
        per_node = collect_masses(PATH, ev)
        assert list(per_node) == PATH
        assert all(len(v) == 5 for v in per_node.values())

    def test_compare_groupings_keys_and_cell(self):
        rng = np.random.default_rng(6)
        ev = {n: generate_normal_evidence(INSULIN, rng=rng) for n in PATH}
        c = dc.compare_groupings(ev)
        assert c["abs_diff"] < 1e-10
        assert c["hier_counter"] == pytest.approx(c["hier"], abs=1e-12)
        assert c["flat_counter"] == pytest.approx(c["flat"], abs=1e-12)
        cell = dc.run_cell(INSULIN, None, n_trials=4, seed=6)
        assert cell["n_trials"] == 4 and cell["max_abs_diff"] < 1e-10
