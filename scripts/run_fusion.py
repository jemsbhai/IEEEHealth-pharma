"""
Fusion Operator under Compound Failures and Evidence Volume (EXP-009).

Pre-registered in LOGBOOK.md (EXP-009).

Part A (N = 100, clean, all products): Normal, F1-F7 and the compound
scenarios C1-C3 under cumulative and averaging within-node fusion, with the
main-suite detection rule and a chain threshold calibrated per operator.
Base evidence is pre-generated once per product so that component and
compound scenarios are evaluated on the same evidence (paired trials);
each trial's injection uses its own seed.

Part B (N = 1000, clean, insulin): Normal, F1, F3, F5, F6 under both
operators, scores only (evidence-volume dilution).

Part C: pairwise Pearson correlation of per-dimension disbelief between
evidence types at the same node under noisy Normal operation (R1, R2).

Usage (from project root):
    python scripts/run_fusion.py
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import stats as sp_stats

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from simulation.simulator import (
    ALL_PRODUCTS, INSULIN, ProductProfile, FailureScenario, FailureType,
    NOISE_REGIMES, generate_normal_evidence, generate_noisy_normal_evidence,
    apply_failure, localization_accuracy_from_trials,
)
from models.drug_integrity_opinion import NodeEvidence
from models.chain_integrity import evaluate_path, ChainEvaluationResult
from run_ablations import evaluate_path_averaging_fuse
from run_experiments import (
    EVAL_PATH, clopper_pearson, mean_ci_halfwidth, get_product_scenarios,
    sl_detect_per_dimension,
)
from run_stress_test import EVIDENCE_TYPES

# ═══════════════════════════════════════════════════════════════════
# CONFIG (pre-registered)
# ═══════════════════════════════════════════════════════════════════

N_TRIALS = 1000
SEED = 42
CORR_SEED = 4242
N_CORR = 2000
FPR_TARGET = 0.01
INJECTION_SEED_OFFSET = 1_000_000

OPERATORS: Dict[str, Callable] = {
    "cumulative": evaluate_path,
    "averaging": evaluate_path_averaging_fuse,
}
PART_A_SCENARIOS = ["Normal", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "C1", "C2", "C3"]
PART_B_SCENARIOS = ["Normal", "F1", "F3", "F5", "F6"]
COMPONENTS = {"C1": ("F3", "F6"), "C2": ("F1", "F5"), "C3": ("F4", "F6nc")}


# ═══════════════════════════════════════════════════════════════════
# SCENARIOS
# ═══════════════════════════════════════════════════════════════════

def compound_scenarios(product: ProductProfile) -> Dict[str, FailureScenario]:
    """C1-C3 as pre-registered, built from the product-relative standard set."""
    std = get_product_scenarios(product)
    f6_non_critical = FailureScenario(
        FailureType.F6_MISSING_DOCS, "central_warehouse", {"missing": ["import_license"]},
    )
    return {
        "C1": FailureScenario(
            FailureType.F7_CASCADING, "importer",
            {"sub_scenarios": [std["F3"], std["F6"]]},
            localization_targets=("importer",),
        ),
        "C2": FailureScenario(
            FailureType.F7_CASCADING, "distributor_a",
            {"sub_scenarios": [std["F1"], std["F5"]]},
            localization_targets=("distributor_a",),
        ),
        "C3": FailureScenario(
            FailureType.F7_CASCADING, "central_warehouse",
            {"sub_scenarios": [std["F4"], f6_non_critical]},
            localization_targets=("central_warehouse",),
        ),
        "F6nc": f6_non_critical,   # component of C3, evaluated for the paired comparison
    }


def all_scenarios(product: ProductProfile) -> Dict[str, Optional[FailureScenario]]:
    out: Dict[str, Optional[FailureScenario]] = {"Normal": None}
    out.update(get_product_scenarios(product))
    out.update(compound_scenarios(product))
    return out


# ═══════════════════════════════════════════════════════════════════
# PAIRED EVIDENCE
# ═══════════════════════════════════════════════════════════════════

def base_evidence_sets(product: ProductProfile, n_trials: int, seed: int,
                       n: int = 100) -> List[Dict[str, NodeEvidence]]:
    """Pre-generate n_trials clean chains so every scenario sees the same base
    evidence (paired trials)."""
    rng = np.random.default_rng(seed)
    return [
        {nid: generate_normal_evidence(product, n_packages=n, n_readings=n, rng=rng) for nid in EVAL_PATH}
        for _ in range(n_trials)
    ]


def inject(base: Dict[str, NodeEvidence], scenario: Optional[FailureScenario],
           product: ProductProfile, trial_index: int, seed: int) -> Dict[str, NodeEvidence]:
    if scenario is None:
        return base
    rng = np.random.default_rng(seed + INJECTION_SEED_OFFSET + trial_index)
    return apply_failure(base, scenario, product, rng)


# ═══════════════════════════════════════════════════════════════════
# PART A
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FusionRow:
    operator: str
    product: str
    scenario: str
    n: int
    n_trials: int
    detection_rate: float
    false_positive_rate: float
    localization_accuracy: float
    mean_score: float
    std_score: float
    mean_uncertainty: float
    n_detected: int
    rate_ci: Tuple[float, float]
    localization_accuracy_ci: Tuple[float, float]
    mean_score_ci_halfwidth: float
    chain_threshold: float


def run_part_a(product: ProductProfile, n_trials: int = N_TRIALS, seed: int = SEED,
               verbose: bool = True) -> Tuple[List[FusionRow], Dict[str, Dict[str, List[float]]], Dict[str, float]]:
    """Returns rows, per-operator per-scenario score lists (paired), thresholds."""
    bases = base_evidence_sets(product, n_trials, seed)
    scenarios = all_scenarios(product)
    rows: List[FusionRow] = []
    scores: Dict[str, Dict[str, List[float]]] = {op: {} for op in OPERATORS}
    thresholds: Dict[str, float] = {}

    for op_name, op_fn in OPERATORS.items():
        # Calibrate the chain threshold on this operator's Normal scores (in-sample, main-suite protocol)
        normal_scores = [op_fn(EVAL_PATH, b, weights=product.weights).integrity_score for b in bases]
        thr = float(np.percentile(normal_scores, 100 * FPR_TARGET))
        thresholds[op_name] = thr
        if verbose:
            print(f"  [{op_name}] chain threshold {thr:.5f}")

        for sc_name, sc in scenarios.items():
            is_failure = sc is not None
            targets = sc.localization_target_set() if sc else ()
            sc_scores, sc_u, dets, flags = [], [], [], []
            for t, base in enumerate(bases):
                ev = inject(base, sc, product, t, seed)
                chain = op_fn(EVAL_PATH, ev, weights=product.weights)
                det, flagged = sl_detect_per_dimension(chain, thr)
                sc_scores.append(chain.integrity_score); sc_u.append(chain.uncertainty)
                dets.append(det); flags.append(flagged)
            n_det = int(sum(dets))
            rate = n_det / n_trials
            la = localization_accuracy_from_trials(dets, flags, targets) if (is_failure and targets) else 0.0
            scores[op_name][sc_name] = sc_scores
            rows.append(FusionRow(
                operator=op_name, product=product.name[:8], scenario=sc_name, n=100, n_trials=n_trials,
                detection_rate=rate if is_failure else 0.0,
                false_positive_rate=rate if not is_failure else 0.0,
                localization_accuracy=la,
                mean_score=float(np.mean(sc_scores)), std_score=float(np.std(sc_scores)),
                mean_uncertainty=float(np.mean(sc_u)), n_detected=n_det,
                rate_ci=clopper_pearson(n_det, n_trials),
                localization_accuracy_ci=clopper_pearson(int(round(la * n_det)), n_det) if is_failure else (0.0, 0.0),
                mean_score_ci_halfwidth=mean_ci_halfwidth(sc_scores),
                chain_threshold=thr,
            ))
            if verbose:
                label = "FPR" if not is_failure else "DR "
                print(f"    {sc_name:<6} {label}={rate:.3f} LA={la:.3f} "
                      f"score={np.mean(sc_scores):.4f}+/-{np.std(sc_scores):.4f} u={np.mean(sc_u):.4f}")
    return rows, scores, thresholds


def paired_compound_check(scores: Dict[str, List[float]], tol: float = 0.005) -> Dict[str, Dict[str, float]]:
    """Fraction of paired trials in which compound score <= min(component scores) + tol."""
    out = {}
    for comp, (a, b) in COMPONENTS.items():
        s_c = np.asarray(scores[comp]); s_a = np.asarray(scores[a]); s_b = np.asarray(scores[b])
        ok = s_c <= np.minimum(s_a, s_b) + tol
        out[comp] = {
            "fraction_at_most_worse_component": float(np.mean(ok)),
            "mean_compound": float(np.mean(s_c)),
            "mean_component_min": float(np.mean(np.minimum(s_a, s_b))),
            "mean_gap": float(np.mean(np.minimum(s_a, s_b) - s_c)),
        }
    return out


def operator_agreement(scores: Dict[str, Dict[str, List[float]]]) -> Dict[str, float]:
    names = [s for s in PART_A_SCENARIOS if s in scores["cumulative"]]
    mc = [float(np.mean(scores["cumulative"][s])) for s in names]
    ma = [float(np.mean(scores["averaging"][s])) for s in names]
    rho, p = sp_stats.spearmanr(mc, ma)
    return {"spearman_between_operators": float(rho), "pvalue": float(p), "n_scenarios": len(names)}


# ═══════════════════════════════════════════════════════════════════
# PART B (evidence volume)
# ═══════════════════════════════════════════════════════════════════

def run_part_b(product: ProductProfile = INSULIN, n: int = 1000, n_trials: int = N_TRIALS,
               seed: int = SEED, verbose: bool = True) -> Dict[str, Dict[str, Dict[str, float]]]:
    bases = base_evidence_sets(product, n_trials, seed, n=n)
    scen = all_scenarios(product)
    out: Dict[str, Dict[str, Dict[str, float]]] = {}
    for op_name, op_fn in OPERATORS.items():
        out[op_name] = {}
        for sc_name in PART_B_SCENARIOS:
            sc = scen[sc_name]
            vals, us = [], []
            for t, base in enumerate(bases):
                ev = inject(base, sc, product, t, seed)
                chain = op_fn(EVAL_PATH, ev, weights=product.weights)
                vals.append(chain.integrity_score); us.append(chain.uncertainty)
            out[op_name][sc_name] = {
                "mean_score": float(np.mean(vals)), "std_score": float(np.std(vals)),
                "mean_uncertainty": float(np.mean(us)),
            }
            if verbose:
                print(f"  [{op_name}] N={n} {sc_name:<6} score={np.mean(vals):.4f}+/-{np.std(vals):.4f} u={np.mean(us):.4f}")
    return out


# ═══════════════════════════════════════════════════════════════════
# PART C (independence of per-dimension disbelief under noise)
# ═══════════════════════════════════════════════════════════════════

def disbelief_correlations(product: ProductProfile, regime: str, n_trials: int = N_CORR,
                           seed: int = CORR_SEED) -> Dict:
    noise = NOISE_REGIMES[regime]
    rng = np.random.default_rng(seed)
    cols = {t: [] for t in EVIDENCE_TYPES}
    for _ in range(n_trials):
        ev = {nid: generate_noisy_normal_evidence(product, noise, rng) for nid in EVAL_PATH}
        chain = evaluate_path(EVAL_PATH, ev, weights=product.weights)
        for nid in EVAL_PATH:
            ops = chain.node_results[nid].evidence_opinions
            for t in EVIDENCE_TYPES:
                cols[t].append(ops[t].disbelief)
    return correlation_matrix({t: np.asarray(v) for t, v in cols.items()})


def correlation_matrix(cols: Dict[str, np.ndarray]) -> Dict:
    """Pearson correlations between columns; constant columns are reported as
    undefined (None) rather than NaN."""
    names = list(cols)
    mat: Dict[str, Dict[str, Optional[float]]] = {a: {} for a in names}
    for a in names:
        for b in names:
            x, y = cols[a], cols[b]
            if np.std(x) == 0 or np.std(y) == 0:
                mat[a][b] = None if a != b else 1.0
            else:
                mat[a][b] = float(np.corrcoef(x, y)[0, 1])
    defined = [abs(mat[a][b]) for a in names for b in names if a < b and mat[a][b] is not None]
    return {
        "types": names,
        "matrix": mat,
        "max_abs_offdiag_defined": max(defined) if defined else 0.0,
        "constant_types": [a for a in names if np.std(cols[a]) == 0],
        "n_rows": int(len(next(iter(cols.values())))),
    }


# ═══════════════════════════════════════════════════════════════════
# DRIVER
# ═══════════════════════════════════════════════════════════════════

def run_fusion_experiment(n_trials: int = N_TRIALS, seed: int = SEED,
                          products: Optional[List[ProductProfile]] = None,
                          run_b: bool = True, run_c: bool = True,
                          n_corr: int = N_CORR, verbose: bool = True) -> Dict:
    products = products or ALL_PRODUCTS
    start = time.time()
    rows: List[FusionRow] = []
    part_a: Dict[str, Dict] = {}
    for product in products:
        pname = product.name[:8]
        if verbose:
            print(f"\n{'='*64}\nPart A | {product.name}\n{'='*64}")
        p_rows, scores, thresholds = run_part_a(product, n_trials, seed, verbose)
        rows.extend(p_rows)
        part_a[pname] = {
            "thresholds": thresholds,
            "paired_compound_check": {op: paired_compound_check(scores[op]) for op in OPERATORS},
            "operator_agreement": operator_agreement(scores),
            "uncertainty_ratio_normal": (
                float(np.mean([r.mean_uncertainty for r in p_rows if r.operator == "averaging" and r.scenario == "Normal"]))
                / max(1e-12, float(np.mean([r.mean_uncertainty for r in p_rows if r.operator == "cumulative" and r.scenario == "Normal"])))
            ),
        }
        if verbose:
            for op in OPERATORS:
                for comp, v in part_a[pname]["paired_compound_check"][op].items():
                    print(f"  [{op}] {comp}: compound<=min(component)+0.005 in "
                          f"{v['fraction_at_most_worse_component']:.3f} of trials; "
                          f"mean gap {v['mean_gap']:+.4f}")
            ag = part_a[pname]["operator_agreement"]
            print(f"  Spearman between operators over {ag['n_scenarios']} scenarios: {ag['spearman_between_operators']:.3f}")
            print(f"  Averaging/cumulative uncertainty on Normal: {part_a[pname]['uncertainty_ratio_normal']:.2f}x")

    part_b = None
    if run_b:
        if verbose:
            print(f"\n{'='*64}\nPart B | evidence volume N = 1000 | {INSULIN.name}\n{'='*64}")
        part_b = run_part_b(INSULIN, 1000, n_trials, seed, verbose)

    part_c = None
    if run_c:
        part_c = {}
        if verbose:
            print(f"\n{'='*64}\nPart C | per-dimension disbelief correlations under noise\n{'='*64}")
        for regime in ("R1", "R2"):
            part_c[regime] = disbelief_correlations(INSULIN, regime, n_corr, CORR_SEED)
            if verbose:
                c = part_c[regime]
                print(f"  {regime}: max |r| over defined pairs = {c['max_abs_offdiag_defined']:.4f}; "
                      f"constant types: {c['constant_types']}")
                for a in c["types"]:
                    print("    " + a[:6].ljust(7) + " ".join(
                        ("  n/a " if c["matrix"][a][b] is None else f"{c['matrix'][a][b]:+.3f}") for b in c["types"]))

    return {
        "config": {"n_trials": n_trials, "seed": seed, "corr_seed": CORR_SEED, "n_corr": n_corr,
                   "fpr_target": FPR_TARGET, "operators": list(OPERATORS), "components": COMPONENTS},
        "part_a_rows": [asdict(r) for r in rows],
        "part_a_summary": part_a,
        "part_b": part_b,
        "part_c": part_c,
        "runtime_seconds": time.time() - start,
    }


if __name__ == "__main__":
    print("=" * 64)
    print("Drug Integrity Algebra — Fusion Operator Study (EXP-009)")
    print(f"  cumulative vs averaging | trials {N_TRIALS}@{SEED} | corr {N_CORR}@{CORR_SEED}")
    print("=" * 64)
    out = run_fusion_experiment()
    print(f"\n{'='*64}\nCompleted in {out['runtime_seconds']:.1f}s\n{'='*64}")
    out_dir = Path(__file__).resolve().parents[1] / "experiments"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "fusion_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")
