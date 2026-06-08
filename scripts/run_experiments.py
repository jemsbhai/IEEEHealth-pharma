"""
Full Experiment Suite v3.0 — with B4 Dempster-Shafer baseline.

v3.0 changes:
- Added B4 (Dempster-Shafer evidence theory) baseline alongside B1-B3.
- Saves to full_results_v3.json to preserve v2 results.

Prior fixes (v2.1):
1. Per-dimension detection checks BOTH disbelief > 2% AND uncertainty > 50%.
2. F5 (expired batch) is product-relative: remaining = 50% of regulatory minimum.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import stats as sp_stats

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simulation.simulator import (
    INSULIN, AMOXICILLIN, MRNA_THERAPY, ALL_PRODUCTS,
    ProductProfile, FailureType, FailureScenario,
    generate_normal_evidence, apply_failure, get_standard_scenarios,
)
from models.drug_integrity_opinion import NodeEvidence, RecallStatus
from models.chain_integrity import (
    evaluate_path, ChainEvaluationResult,
)
from baselines.baselines import (
    evaluate_threshold, evaluate_weighted_scoring, evaluate_bayesian,
    evaluate_dempster_shafer,
)

# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════

N_TRIALS = 1000
SEED = 42
FPR_TARGET = 0.01

EVAL_PATH = [
    "manufacturer", "importer", "central_warehouse",
    "distributor_a", "pharmacy_a",
]

DIM_DISBELIEF_THRESHOLD = 0.02
DIM_UNCERTAINTY_THRESHOLD = 0.50  # flag if per-dimension uncertainty exceeds 50%

# ═══════════════════════════════════════════════════════════════════
# RESULT STRUCTURES
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MethodResult:
    method: str
    scenario: str
    product: str
    n_trials: int
    detection_rate: float
    false_positive_rate: float
    localization_accuracy: float
    mean_score: float
    std_score: float
    mean_uncertainty: float
    threshold_used: float = 0.0

@dataclass
class ExperimentSuite:
    results: List[MethodResult] = field(default_factory=list)
    runtime_seconds: float = 0.0
    adaptive_thresholds: Dict[str, float] = field(default_factory=dict)

    def to_table(self) -> str:
        header = (f"{'Method':<22} {'Scenario':<10} {'Product':<10} "
                  f"{'DR':>6} {'FPR':>6} {'LA':>6} {'Mean':>7} {'Std':>6} {'U':>6}")
        lines = [header, "-" * len(header)]
        for r in self.results:
            lines.append(
                f"{r.method:<22} {r.scenario:<10} {r.product:<10} "
                f"{r.detection_rate:>6.3f} {r.false_positive_rate:>6.3f} "
                f"{r.localization_accuracy:>6.3f} {r.mean_score:>7.4f} "
                f"{r.std_score:>6.4f} {r.mean_uncertainty:>6.4f}"
            )
        return "\n".join(lines)

    def save(self, path: str) -> None:
        data = {
            "runtime_seconds": self.runtime_seconds,
            "adaptive_thresholds": self.adaptive_thresholds,
            "results": [asdict(r) for r in self.results],
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

# ═══════════════════════════════════════════════════════════════════
# SL ALGEBRA — PER-DIMENSION DETECTION (DISBELIEF + UNCERTAINTY)
# ═══════════════════════════════════════════════════════════════════

def sl_detect_per_dimension(
    chain_result: ChainEvaluationResult,
    chain_threshold: float,
    dim_disbelief_threshold: float = DIM_DISBELIEF_THRESHOLD,
    dim_uncertainty_threshold: float = DIM_UNCERTAINTY_THRESHOLD,
) -> Tuple[bool, List[str]]:
    """Detect failures using chain-level score AND per-dimension opinions.

    Detection fires if ANY of:
    1. Chain-level P(ω) < chain_threshold
    2. Any evidence dimension at any node has disbelief > dim_disbelief_threshold
    3. Any evidence dimension at any node has uncertainty > dim_uncertainty_threshold
    4. Chain-level uncertainty > 0.3

    Level 3 is critical for F4 (scan omission): when only 20% of packages
    are scanned, the serialization dimension has u≈0.8 — high uncertainty
    about the unscanned 80%. This is actionable intelligence that baselines
    miss (B2 scores it as 0.2 × 1.0 = 0.2 coverage score, B1 fails it
    entirely, but neither EXPLAINS why).
    """
    detected = False
    flagged_nodes: List[str] = []

    # Level 1: chain-level score
    if chain_result.integrity_score < chain_threshold:
        detected = True

    # Level 2 & 3: per-dimension disbelief and uncertainty at each node
    for node_id, node_result in chain_result.node_results.items():
        for dim_name, dim_opinion in node_result.evidence_opinions.items():
            if dim_name == "recall":
                continue  # recall handled via override semantics
            flagged = False
            if dim_opinion.disbelief > dim_disbelief_threshold:
                flagged = True
            if dim_opinion.uncertainty > dim_uncertainty_threshold:
                flagged = True
            if flagged:
                detected = True
                if node_id not in flagged_nodes:
                    flagged_nodes.append(node_id)

    # Level 4: chain-level high uncertainty
    if chain_result.uncertainty > 0.3:
        detected = True

    return detected, flagged_nodes

# ═══════════════════════════════════════════════════════════════════
# CALIBRATION PHASE
# ═══════════════════════════════════════════════════════════════════

def calibrate_thresholds(
    path: List[str], product: ProductProfile, n_trials: int, seed: int,
    fpr_target: float = FPR_TARGET,
) -> Dict[str, float]:
    rng = np.random.default_rng(seed)
    sl_scores, b1_scores, b2_scores, b3_scores, b4_scores = [], [], [], [], []

    for _ in range(n_trials):
        evidence = {nid: generate_normal_evidence(product, rng=rng) for nid in path}
        sl_scores.append(evaluate_path(path, evidence, weights=product.weights).integrity_score)
        b1_scores.append(evaluate_threshold(path, evidence).chain_score)
        b2_scores.append(evaluate_weighted_scoring(path, evidence).chain_score)
        b3_scores.append(evaluate_bayesian(path, evidence).chain_score)
        b4_scores.append(evaluate_dempster_shafer(path, evidence).chain_score)

    pct = fpr_target * 100
    thresholds = {
        "SL_Algebra": float(np.percentile(sl_scores, pct)),
        "B1_Threshold": 0.5,  # binary, only sensible choice
        "B2_WeightedScoring": float(np.percentile(b2_scores, pct)),
        "B3_Bayesian": float(np.percentile(b3_scores, pct)),
        "B4_DempsterShafer": float(np.percentile(b4_scores, pct)),
    }
    return thresholds

# ═══════════════════════════════════════════════════════════════════
# PRODUCT-RELATIVE SCENARIO ADJUSTMENT
# ═══════════════════════════════════════════════════════════════════

def get_product_scenarios(product: ProductProfile) -> Dict[str, FailureScenario]:
    """Get standard scenarios with F5 adjusted for product's regulatory minimum.

    F5 uses remaining_days = 50% of the product's regulatory minimum,
    which is always below threshold regardless of product category.
    """
    scenarios = get_standard_scenarios()
    # Override F5 to be product-relative
    scenarios["F5"] = FailureScenario(
        FailureType.F5_EXPIRED_BATCH, "distributor_a",
        {"remaining_days": int(product.shelf_life_min_pharmacy * 0.5)},
    )
    return scenarios

# ═══════════════════════════════════════════════════════════════════
# EXPERIMENT RUNNER
# ═══════════════════════════════════════════════════════════════════

def run_full_experiment(
    n_trials: int = N_TRIALS, seed: int = SEED,
    products: Optional[List[ProductProfile]] = None,
) -> ExperimentSuite:

    if products is None:
        products = ALL_PRODUCTS

    suite = ExperimentSuite()
    start = time.time()

    for product in products:
        scenarios = get_product_scenarios(product)
        scenario_list: List[Tuple[str, Optional[FailureScenario]]] = [("Normal", None)]
        scenario_list += [(name, sc) for name, sc in scenarios.items()]

        total_per_product = len(scenario_list) * 5
        done = 0

        print(f"\n{'='*60}")
        print(f"Product: {product.name}")
        print(f"{'='*60}")
        print("Calibrating adaptive thresholds...")
        thresholds = calibrate_thresholds(EVAL_PATH, product, n_trials, seed)
        suite.adaptive_thresholds[product.name[:8]] = thresholds
        for m, t in thresholds.items():
            print(f"  {m}: {t:.6f}")
        print()

        for sc_name, scenario in scenario_list:
            is_failure = scenario is not None
            target_node = scenario.target_node if scenario else None
            rng = np.random.default_rng(seed)

            sl_list, b1_list, b2_list, b3_list, b4_list = [], [], [], [], []

            for _ in range(n_trials):
                evidence = {nid: generate_normal_evidence(product, rng=rng) for nid in EVAL_PATH}
                if scenario is not None:
                    evidence = apply_failure(evidence, scenario, product, rng)

                sl_chain = evaluate_path(EVAL_PATH, evidence, weights=product.weights)
                sl_det, sl_flag = sl_detect_per_dimension(sl_chain, thresholds["SL_Algebra"])
                sl_list.append((sl_chain, sl_det, sl_flag))

                b1_list.append(evaluate_threshold(EVAL_PATH, evidence, detection_threshold=thresholds["B1_Threshold"]))
                b2_list.append(evaluate_weighted_scoring(EVAL_PATH, evidence, detection_threshold=thresholds["B2_WeightedScoring"]))
                b3_list.append(evaluate_bayesian(EVAL_PATH, evidence, detection_threshold=thresholds["B3_Bayesian"]))
                b4_list.append(evaluate_dempster_shafer(EVAL_PATH, evidence, detection_threshold=thresholds["B4_DempsterShafer"]))

            # ── SL Algebra aggregation ──
            sl_scores = [r[0].integrity_score for r in sl_list]
            sl_u = [r[0].uncertainty for r in sl_list]
            sl_dets = [r[1] for r in sl_list]
            sl_dr = sum(sl_dets) / n_trials if is_failure else 0.0
            sl_fpr = sum(sl_dets) / n_trials if not is_failure else 0.0
            sl_la = 0.0
            if is_failure and target_node:
                det_loc = [(r[0], r[2]) for r in sl_list if r[1]]
                if det_loc:
                    sl_la = sum(1 for _, f in det_loc if target_node in f) / len(det_loc)

            suite.results.append(MethodResult(
                "SL_Algebra", sc_name, product.name[:8], n_trials,
                sl_dr, sl_fpr, sl_la,
                float(np.mean(sl_scores)), float(np.std(sl_scores)),
                float(np.mean(sl_u)), thresholds["SL_Algebra"],
            ))
            done += 1
            print(f"  [{done}/{total_per_product}] SL_Algebra × {sc_name:<8} DR={sl_dr:.3f} FPR={sl_fpr:.3f} LA={sl_la:.3f}")

            # ── Baseline aggregation ──
            for bl_name, bl_list, thr_key in [
                ("B1_Threshold", b1_list, "B1_Threshold"),
                ("B2_WeightedScoring", b2_list, "B2_WeightedScoring"),
                ("B3_Bayesian", b3_list, "B3_Bayesian"),
                ("B4_DempsterShafer", b4_list, "B4_DempsterShafer"),
            ]:
                bl_scores = [r.chain_score for r in bl_list]
                bl_dets = [r.detected for r in bl_list]
                bl_dr = sum(bl_dets) / n_trials if is_failure else 0.0
                bl_fpr = sum(bl_dets) / n_trials if not is_failure else 0.0
                bl_la = 0.0
                if is_failure and target_node:
                    det_bl = [r for r in bl_list if r.detected]
                    if det_bl:
                        bl_la = sum(1 for r in det_bl if r.weakest_node == target_node) / len(det_bl)

                suite.results.append(MethodResult(
                    bl_name, sc_name, product.name[:8], n_trials,
                    bl_dr, bl_fpr, bl_la,
                    float(np.mean(bl_scores)), float(np.std(bl_scores)),
                    0.0, thresholds[thr_key],
                ))
                done += 1
                print(f"  [{done}/{total_per_product}] {bl_name:<22} × {sc_name:<8} DR={bl_dr:.3f} FPR={bl_fpr:.3f}")

    suite.runtime_seconds = time.time() - start
    return suite

if __name__ == "__main__":
    print("=" * 70)
    print("Drug Integrity Algebra — Experiment Suite v3.0")
    print("  SL + B1-B4 | Adaptive thresholds | Per-dim d+u | Product-rel F5")
    print("=" * 70)
    print(f"Trials: {N_TRIALS} | Seed: {SEED} | FPR target: {FPR_TARGET*100:.0f}%")
    print()

    suite = run_full_experiment()

    print(f"\n{'='*70}")
    print(f"Completed in {suite.runtime_seconds:.1f}s")
    print(f"{'='*70}\n")
    print(suite.to_table())

    out_dir = Path(__file__).resolve().parents[1] / "experiments"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "full_results_v3.json"
    suite.save(str(out_path))
    print(f"\nResults saved to {out_path}")
