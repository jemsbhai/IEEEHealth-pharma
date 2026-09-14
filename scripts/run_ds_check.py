"""
Flat versus hierarchical Dempster combination (EXP-010).

For every trial, B4's pignistic probability is computed twice: hierarchically
(five evidence masses per node, then five node masses; the shipped code) and
flat (all 25 masses in node-major order). Dempster's rule is associative and
commutative, so the two must agree unless the implementation's total-conflict
branch fires. Both groupings are also run through a conflict counter.

Usage (from project root):
    python scripts/run_ds_check.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from simulation.simulator import (
    ALL_PRODUCTS, INSULIN, ProductProfile, FailureScenario, NOISE_REGIMES,
    generate_normal_evidence, generate_noisy_normal_evidence, apply_failure,
)
from baselines.baselines import (
    evaluate_dempster_shafer, evaluate_dempster_shafer_flat,
    collect_masses, combine_with_conflict_count, dempster_combine_multi,
)
from run_experiments import EVAL_PATH, get_product_scenarios

N_TRIALS = 1000
SEED = 42
NOISE_SEED = 4242


def compare_groupings(evidence: Dict) -> Dict[str, float]:
    """Hierarchical vs flat BetP(safe) for one chain, with conflict statistics."""
    hier = evaluate_dempster_shafer(EVAL_PATH, evidence).chain_score
    flat = evaluate_dempster_shafer_flat(EVAL_PATH, evidence).chain_score

    per_node = collect_masses(EVAL_PATH, evidence)
    # Hierarchical grouping through the counter: within each node, then across nodes
    node_masses, h_events, h_maxk = [], 0, 0.0
    for n in EVAL_PATH:
        m, ev_count, k = combine_with_conflict_count(per_node[n])
        node_masses.append(m); h_events += ev_count; h_maxk = max(h_maxk, k)
    m_chain, ev_count, k = combine_with_conflict_count(node_masses)
    h_events += ev_count; h_maxk = max(h_maxk, k)
    # Flat grouping through the counter
    flat_list = [m for n in EVAL_PATH for m in per_node[n]]
    m_flat, f_events, f_maxk = combine_with_conflict_count(flat_list)

    return {
        "hier": hier, "flat": flat, "abs_diff": abs(hier - flat),
        "hier_counter": m_chain.pignistic_safe(), "flat_counter": m_flat.pignistic_safe(),
        "hier_conflict_events": h_events, "flat_conflict_events": f_events,
        "hier_max_k": h_maxk, "flat_max_k": f_maxk,
    }


def run_cell(product: ProductProfile, scenario: Optional[FailureScenario], n_trials: int,
             seed: int, noise_regime: Optional[str] = None) -> Dict:
    rng = np.random.default_rng(seed)
    diffs, events_h, events_f, maxk_h, maxk_f = [], 0, 0, 0.0, 0.0
    for _ in range(n_trials):
        if noise_regime:
            ev = {nid: generate_noisy_normal_evidence(product, NOISE_REGIMES[noise_regime], rng) for nid in EVAL_PATH}
        else:
            ev = {nid: generate_normal_evidence(product, rng=rng) for nid in EVAL_PATH}
        if scenario is not None:
            ev = apply_failure(ev, scenario, product, rng)
        c = compare_groupings(ev)
        diffs.append(c["abs_diff"])
        events_h += c["hier_conflict_events"]; events_f += c["flat_conflict_events"]
        maxk_h = max(maxk_h, c["hier_max_k"]); maxk_f = max(maxk_f, c["flat_max_k"])
    return {
        "n_trials": n_trials,
        "max_abs_diff": float(np.max(diffs)), "mean_abs_diff": float(np.mean(diffs)),
        "hier_conflict_events": events_h, "flat_conflict_events": events_f,
        "hier_max_k": maxk_h, "flat_max_k": maxk_f,
    }


def run_ds_check(n_trials: int = N_TRIALS, seed: int = SEED,
                 products: Optional[List[ProductProfile]] = None, verbose: bool = True) -> Dict:
    products = products or ALL_PRODUCTS
    start = time.time()
    out: Dict = {"config": {"n_trials": n_trials, "seed": seed, "noise_seed": NOISE_SEED}, "clean": {}, "noisy": {}}
    overall_max = 0.0
    total_events = 0
    for product in products:
        pname = product.name[:8]
        out["clean"][pname] = {}
        scen: Dict[str, Optional[FailureScenario]] = {"Normal": None}
        scen.update(get_product_scenarios(product))
        if verbose:
            print(f"\n{product.name}")
        for name, sc in scen.items():
            cell = run_cell(product, sc, n_trials, seed)
            out["clean"][pname][name] = cell
            overall_max = max(overall_max, cell["max_abs_diff"])
            total_events += cell["hier_conflict_events"] + cell["flat_conflict_events"]
            if verbose:
                print(f"  {name:<7} max|diff|={cell['max_abs_diff']:.2e} mean={cell['mean_abs_diff']:.2e} "
                      f"conflict events hier/flat={cell['hier_conflict_events']}/{cell['flat_conflict_events']} "
                      f"max K hier/flat={cell['hier_max_k']:.4f}/{cell['flat_max_k']:.4f}")
    if verbose:
        print(f"\nNoise regime R2, {INSULIN.name}")
    cell = run_cell(INSULIN, None, n_trials, NOISE_SEED, noise_regime="R2")
    out["noisy"]["R2_Insulin_Normal"] = cell
    overall_max = max(overall_max, cell["max_abs_diff"])
    total_events += cell["hier_conflict_events"] + cell["flat_conflict_events"]
    if verbose:
        print(f"  Normal  max|diff|={cell['max_abs_diff']:.2e} mean={cell['mean_abs_diff']:.2e} "
              f"conflict events hier/flat={cell['hier_conflict_events']}/{cell['flat_conflict_events']} "
              f"max K hier/flat={cell['hier_max_k']:.4f}/{cell['flat_max_k']:.4f}")
    out["overall_max_abs_diff"] = overall_max
    out["total_conflict_events"] = total_events
    out["runtime_seconds"] = time.time() - start
    return out


if __name__ == "__main__":
    print("=" * 64)
    print("Drug Integrity Algebra — Flat vs hierarchical Dempster combination (EXP-010)")
    print("=" * 64)
    res = run_ds_check()
    print(f"\nOverall max |BetP_flat - BetP_hier| = {res['overall_max_abs_diff']:.3e}; "
          f"total-conflict events = {res['total_conflict_events']}")
    print(f"Completed in {res['runtime_seconds']:.1f}s")
    out_dir = Path(__file__).resolve().parents[1] / "experiments"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "ds_check_results.json"
    with open(out_path, "w") as f:
        json.dump(res, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")
