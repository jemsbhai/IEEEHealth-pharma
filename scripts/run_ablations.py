"""
Ablation Study Runner (EXP-003) — tests each architectural component.

Five ablations, each modifying exactly ONE component of the full model:
    A1: Evidence type removal   — drop each F1–F5 in turn
    A2: Fusion operator swap    — replace cumulative_fuse with averaging_fuse
    A3: Propagation operator    — replace deduce with independent node min-P(ω)
    A4: Per-dimension detection — chain-level only, no per-dim flags
    A5: Evidence weight removal — uniform weights instead of product-specific

Control: full model with all components (matches EXP-002a / run_experiments.py v2.1).

Usage (from project root):
    python scripts/run_ablations.py
"""

from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jsonld_ex.confidence_algebra import (
    Opinion,
    cumulative_fuse,
    averaging_fuse,
    deduce,
)

from simulation.simulator import (
    INSULIN, AMOXICILLIN, MRNA_THERAPY, ALL_PRODUCTS,
    ProductProfile, FailureType, FailureScenario,
    generate_normal_evidence, apply_failure, get_standard_scenarios,
)
from models.drug_integrity_opinion import (
    DrugIntegrityOpinion,
    NodeEvidence,
    RecallStatus,
    EvidenceWeights,
)
from models.chain_integrity import (
    evaluate_path,
    evaluate_node,
    ChainEvaluationResult,
    NodeEvaluationResult,
    COMPROMISED_CONDITIONAL,
)


# ═══════════════════════════════════════════════════════════════════
# CONFIG — matches run_experiments.py v2.1 exactly
# ═══════════════════════════════════════════════════════════════════

N_TRIALS = 1000
SEED = 42
FPR_TARGET = 0.01

EVAL_PATH = [
    "manufacturer", "importer", "central_warehouse",
    "distributor_a", "pharmacy_a",
]

DIM_DISBELIEF_THRESHOLD = 0.02
DIM_UNCERTAINTY_THRESHOLD = 0.50


# ═══════════════════════════════════════════════════════════════════
# RESULT STRUCTURES
# ═══════════════════════════════════════════════════════════════════

@dataclass
class AblationResult:
    ablation: str
    variant: str  # e.g., "drop_serialization" for A1
    scenario: str
    product: str
    n_trials: int
    detection_rate: float
    localization_accuracy: float
    mean_score: float
    std_score: float
    mean_uncertainty: float
    threshold_used: float = 0.0


@dataclass
class AblationSuite:
    results: List[AblationResult] = field(default_factory=list)
    runtime_seconds: float = 0.0

    def to_table(self) -> str:
        header = (f"{'Ablation':<12} {'Variant':<22} {'Scenario':<10} "
                  f"{'Product':<10} {'DR':>6} {'LA':>6} {'Mean':>7} {'U':>6}")
        lines = [header, "-" * len(header)]
        for r in self.results:
            lines.append(
                f"{r.ablation:<12} {r.variant:<22} {r.scenario:<10} "
                f"{r.product:<10} {r.detection_rate:>6.3f} "
                f"{r.localization_accuracy:>6.3f} {r.mean_score:>7.4f} "
                f"{r.mean_uncertainty:>6.4f}"
            )
        return "\n".join(lines)

    def save(self, path: str) -> None:
        data = {
            "runtime_seconds": self.runtime_seconds,
            "results": [asdict(r) for r in self.results],
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


# ═══════════════════════════════════════════════════════════════════
# PER-DIMENSION DETECTION (same as run_experiments.py)
# ═══════════════════════════════════════════════════════════════════

def sl_detect_per_dimension(
    chain_result: ChainEvaluationResult,
    chain_threshold: float,
) -> Tuple[bool, List[str]]:
    """Full model detection: chain-level + per-dimension flags."""
    detected = False
    flagged_nodes: List[str] = []

    if chain_result.integrity_score < chain_threshold:
        detected = True

    for node_id, node_result in chain_result.node_results.items():
        for dim_name, dim_opinion in node_result.evidence_opinions.items():
            if dim_name == "recall":
                continue
            flagged = False
            if dim_opinion.disbelief > DIM_DISBELIEF_THRESHOLD:
                flagged = True
            if dim_opinion.uncertainty > DIM_UNCERTAINTY_THRESHOLD:
                flagged = True
            if flagged:
                detected = True
                if node_id not in flagged_nodes:
                    flagged_nodes.append(node_id)

    if chain_result.uncertainty > 0.3:
        detected = True

    return detected, flagged_nodes


def sl_detect_chain_only(
    chain_result: ChainEvaluationResult,
    chain_threshold: float,
) -> Tuple[bool, List[str]]:
    """A4 ablation: chain-level detection ONLY, no per-dimension flags."""
    detected = chain_result.integrity_score < chain_threshold
    # Localization: just use weakest node
    flagged = [chain_result.weakest_node] if detected else []
    return detected, flagged


# ═══════════════════════════════════════════════════════════════════
# A1 — EVIDENCE TYPE ABLATION
# ═══════════════════════════════════════════════════════════════════

def ablate_evidence(
    evidence: Dict[str, NodeEvidence],
    drop_type: str,
) -> Dict[str, NodeEvidence]:
    """Remove one evidence type from ALL nodes (set to None / NO_RECALL)."""
    result = {}
    for node_id, ev in evidence.items():
        kwargs = {
            "serialization": ev.serialization,
            "cold_chain": ev.cold_chain,
            "shelf_life": ev.shelf_life,
            "documentation": ev.documentation,
            "recall": ev.recall,
        }
        if drop_type == "recall":
            kwargs["recall"] = RecallStatus.NO_RECALL
        else:
            kwargs[drop_type] = None
        result[node_id] = NodeEvidence(**kwargs)
    return result


# ═══════════════════════════════════════════════════════════════════
# A2 — FUSION OPERATOR ABLATION
# ═══════════════════════════════════════════════════════════════════

def evaluate_path_averaging_fuse(
    path: List[str],
    node_evidence: Dict[str, NodeEvidence],
    weights: EvidenceWeights = EvidenceWeights(),
) -> ChainEvaluationResult:
    """Like evaluate_path but uses averaging_fuse instead of cumulative_fuse.

    Within-node: averaging_fuse (Jøsang §12.5) instead of cumulative_fuse (§12.3)
    Cross-node: deduce (unchanged)
    """
    node_results: Dict[str, NodeEvaluationResult] = {}

    for node_id in path:
        ev = node_evidence.get(node_id, NodeEvidence())
        # Use the standard evaluate_node but we need to override the fusion
        # We'll replicate the evaluation with averaging_fuse
        result = _evaluate_node_with_fusion(node_id, ev, weights, averaging_fuse)
        node_results[node_id] = result

    # Cross-node: deduce (same as full model)
    node_opinions = [node_results[nid].fused_opinion for nid in path]
    if len(node_opinions) == 1:
        chain_opinion = node_opinions[0]
    else:
        chain_opinion = node_opinions[-1]
        for i in range(len(node_opinions) - 2, -1, -1):
            chain_opinion = deduce(
                opinion_x=node_opinions[i],
                opinion_y_given_x=chain_opinion,
                opinion_y_given_not_x=COMPROMISED_CONDITIONAL,
            )

    chain_opinion = DrugIntegrityOpinion.from_opinion(chain_opinion)
    weakest = min(path, key=lambda n: node_results[n].fused_opinion.projected_probability())

    return ChainEvaluationResult(
        path=path,
        chain_opinion=chain_opinion,
        node_results=node_results,
        weakest_node=weakest,
        integrity_score=chain_opinion.projected_probability(),
        uncertainty=chain_opinion.uncertainty,
    )


def _evaluate_node_with_fusion(
    node_id: str,
    evidence: NodeEvidence,
    weights: EvidenceWeights,
    fuse_fn,
    base_prior_weight: float = 2.0,
) -> NodeEvaluationResult:
    """Evaluate a node using a specified fusion function."""
    from models.drug_integrity_opinion import (
        form_serialization_opinion,
        form_cold_chain_opinion,
        form_shelf_life_opinion,
        form_documentation_opinion,
        form_recall_opinion,
    )

    opinions: List[Opinion] = []
    evidence_opinions: Dict[str, DrugIntegrityOpinion] = {}
    missing: List[str] = []

    # F1
    if evidence.serialization is not None:
        eff_w = base_prior_weight / weights.serialization
        op = form_serialization_opinion(evidence.serialization, prior_weight=eff_w, base_rate=0.99)
        evidence_opinions["serialization"] = op
    else:
        op = DrugIntegrityOpinion.vacuous(0.99)
        evidence_opinions["serialization"] = op
        missing.append("serialization")
    opinions.append(op)

    # F2
    if evidence.cold_chain is not None:
        eff_w = base_prior_weight / weights.cold_chain
        op = form_cold_chain_opinion(evidence.cold_chain, prior_weight=eff_w, base_rate=0.95)
        evidence_opinions["cold_chain"] = op
    else:
        op = DrugIntegrityOpinion.vacuous(0.95)
        evidence_opinions["cold_chain"] = op
        missing.append("cold_chain")
    opinions.append(op)

    # F3
    if evidence.shelf_life is not None:
        eff_w = base_prior_weight / weights.shelf_life
        op = form_shelf_life_opinion(evidence.shelf_life, prior_weight=eff_w, base_rate=0.90)
        evidence_opinions["shelf_life"] = op
    else:
        op = DrugIntegrityOpinion.vacuous(0.90)
        evidence_opinions["shelf_life"] = op
        missing.append("shelf_life")
    opinions.append(op)

    # F4
    if evidence.documentation is not None:
        eff_w = base_prior_weight / weights.documentation
        op = form_documentation_opinion(evidence.documentation, prior_weight=eff_w, base_rate=0.80)
        evidence_opinions["documentation"] = op
    else:
        op = DrugIntegrityOpinion.vacuous(0.80)
        evidence_opinions["documentation"] = op
        missing.append("documentation")
    opinions.append(op)

    # F5
    op = form_recall_opinion(evidence.recall, base_rate=0.99)
    evidence_opinions["recall"] = op
    opinions.append(op)

    # Fusion with specified operator
    if len(opinions) == 1:
        fused = opinions[0]
    else:
        fused = fuse_fn(*opinions)

    return NodeEvaluationResult(
        node_id=node_id,
        fused_opinion=DrugIntegrityOpinion.from_opinion(fused),
        evidence_opinions=evidence_opinions,
        missing_evidence=missing,
    )


# ═══════════════════════════════════════════════════════════════════
# A3 — PROPAGATION OPERATOR ABLATION
# ═══════════════════════════════════════════════════════════════════

def evaluate_path_independent(
    path: List[str],
    node_evidence: Dict[str, NodeEvidence],
    weights: EvidenceWeights = EvidenceWeights(),
) -> ChainEvaluationResult:
    """Ablation: evaluate nodes independently, chain = min projected probability.

    No cross-node propagation at all. Chain score is simply the minimum
    node-level projected probability (weakest-link model).
    """
    node_results: Dict[str, NodeEvaluationResult] = {}

    for node_id in path:
        ev = node_evidence.get(node_id, NodeEvidence())
        result = evaluate_node(node_id, ev, weights=weights)
        node_results[node_id] = result

    # Chain score: minimum projected probability (no propagation)
    weakest = min(path, key=lambda n: node_results[n].fused_opinion.projected_probability())
    weakest_pp = node_results[weakest].fused_opinion.projected_probability()

    # Construct a synthetic chain opinion from the weakest node
    weakest_op = node_results[weakest].fused_opinion
    chain_opinion = DrugIntegrityOpinion.from_opinion(weakest_op)

    return ChainEvaluationResult(
        path=path,
        chain_opinion=chain_opinion,
        node_results=node_results,
        weakest_node=weakest,
        integrity_score=weakest_pp,
        uncertainty=weakest_op.uncertainty,
    )


# ═══════════════════════════════════════════════════════════════════
# PRODUCT-RELATIVE SCENARIOS (same as run_experiments.py)
# ═══════════════════════════════════════════════════════════════════

def get_product_scenarios(product: ProductProfile) -> Dict[str, FailureScenario]:
    scenarios = get_standard_scenarios()
    scenarios["F5"] = FailureScenario(
        FailureType.F5_EXPIRED_BATCH, "distributor_a",
        {"remaining_days": int(product.shelf_life_min_pharmacy * 0.5)},
    )
    return scenarios


# ═══════════════════════════════════════════════════════════════════
# CALIBRATION (per-ablation adaptive thresholds)
# ═══════════════════════════════════════════════════════════════════

def calibrate_ablation_threshold(
    path: List[str],
    product: ProductProfile,
    n_trials: int,
    seed: int,
    eval_fn,
    weights: EvidenceWeights,
    fpr_target: float = FPR_TARGET,
    evidence_transform=None,
) -> float:
    """Calibrate detection threshold for a specific ablation variant.

    Runs Normal trials, collects scores, returns the fpr_target percentile.
    """
    rng = np.random.default_rng(seed)
    scores = []

    for _ in range(n_trials):
        evidence = {nid: generate_normal_evidence(product, rng=rng) for nid in path}
        if evidence_transform is not None:
            evidence = evidence_transform(evidence)
        result = eval_fn(path, evidence, weights=weights)
        scores.append(result.integrity_score)

    return float(np.percentile(scores, fpr_target * 100))


# ═══════════════════════════════════════════════════════════════════
# UNIFIED ABLATION RUNNER
# ═══════════════════════════════════════════════════════════════════

def run_single_ablation(
    ablation_name: str,
    variant_name: str,
    path: List[str],
    product: ProductProfile,
    scenarios: Dict[str, FailureScenario],
    eval_fn,
    detect_fn,
    weights: EvidenceWeights,
    n_trials: int = N_TRIALS,
    seed: int = SEED,
    evidence_transform=None,
) -> List[AblationResult]:
    """Run one ablation variant across all scenarios for one product."""

    # Calibrate threshold for this variant
    threshold = calibrate_ablation_threshold(
        path, product, n_trials, seed, eval_fn, weights,
        evidence_transform=evidence_transform,
    )

    results: List[AblationResult] = []

    scenario_list: List[Tuple[str, Optional[FailureScenario]]] = [("Normal", None)]
    scenario_list += [(name, sc) for name, sc in scenarios.items()]

    for sc_name, scenario in scenario_list:
        is_failure = scenario is not None
        target_node = scenario.target_node if scenario else None
        rng = np.random.default_rng(seed)

        scores, uncertainties, detections, correct_locs = [], [], [], []

        for _ in range(n_trials):
            evidence = {nid: generate_normal_evidence(product, rng=rng) for nid in path}
            if scenario is not None:
                evidence = apply_failure(evidence, scenario, product, rng)
            if evidence_transform is not None:
                evidence = evidence_transform(evidence)

            chain_result = eval_fn(path, evidence, weights=weights)
            detected, flagged = detect_fn(chain_result, threshold)

            scores.append(chain_result.integrity_score)
            uncertainties.append(chain_result.uncertainty)
            detections.append(detected)

            if detected and target_node:
                correct_locs.append(target_node in flagged)

        dr = sum(detections) / n_trials if is_failure else 0.0
        la = sum(correct_locs) / len(correct_locs) if correct_locs else 0.0

        results.append(AblationResult(
            ablation=ablation_name,
            variant=variant_name,
            scenario=sc_name,
            product=product.name[:8],
            n_trials=n_trials,
            detection_rate=dr,
            localization_accuracy=la,
            mean_score=float(np.mean(scores)),
            std_score=float(np.std(scores)),
            mean_uncertainty=float(np.mean(uncertainties)),
            threshold_used=threshold,
        ))

    return results


# ═══════════════════════════════════════════════════════════════════
# MAIN — RUN ALL ABLATIONS
# ═══════════════════════════════════════════════════════════════════

def run_all_ablations(
    n_trials: int = N_TRIALS,
    seed: int = SEED,
    products: Optional[List[ProductProfile]] = None,
) -> AblationSuite:

    if products is None:
        products = ALL_PRODUCTS

    suite = AblationSuite()
    start = time.time()

    for product in products:
        scenarios = get_product_scenarios(product)
        print(f"\n{'='*70}")
        print(f"Product: {product.name}")
        print(f"{'='*70}")

        # ── CONTROL: Full model ──────────────────────────────────
        print("\n  [CONTROL] Full model")
        suite.results.extend(run_single_ablation(
            "FULL", "full_model", EVAL_PATH, product, scenarios,
            eval_fn=lambda p, e, weights: evaluate_path(p, e, weights=weights),
            detect_fn=sl_detect_per_dimension,
            weights=product.weights,
            n_trials=n_trials, seed=seed,
        ))
        _print_summary(suite.results, "FULL", "full_model", product.name[:8])

        # ── A1: Evidence type ablation ───────────────────────────
        evidence_types = ["serialization", "cold_chain", "shelf_life", "documentation", "recall"]
        for etype in evidence_types:
            variant = f"drop_{etype}"
            print(f"\n  [A1] {variant}")
            transform = lambda ev, et=etype: ablate_evidence(ev, et)
            suite.results.extend(run_single_ablation(
                "A1_EvType", variant, EVAL_PATH, product, scenarios,
                eval_fn=lambda p, e, weights: evaluate_path(p, e, weights=weights),
                detect_fn=sl_detect_per_dimension,
                weights=product.weights,
                n_trials=n_trials, seed=seed,
                evidence_transform=transform,
            ))
            _print_summary(suite.results, "A1_EvType", variant, product.name[:8])

        # ── A2: Fusion operator ablation ─────────────────────────
        print(f"\n  [A2] averaging_fuse")
        suite.results.extend(run_single_ablation(
            "A2_Fusion", "averaging_fuse", EVAL_PATH, product, scenarios,
            eval_fn=evaluate_path_averaging_fuse,
            detect_fn=sl_detect_per_dimension,
            weights=product.weights,
            n_trials=n_trials, seed=seed,
        ))
        _print_summary(suite.results, "A2_Fusion", "averaging_fuse", product.name[:8])

        # ── A3: Propagation operator ablation ────────────────────
        print(f"\n  [A3] independent_nodes")
        suite.results.extend(run_single_ablation(
            "A3_Prop", "independent_min", EVAL_PATH, product, scenarios,
            eval_fn=evaluate_path_independent,
            detect_fn=sl_detect_per_dimension,
            weights=product.weights,
            n_trials=n_trials, seed=seed,
        ))
        _print_summary(suite.results, "A3_Prop", "independent_min", product.name[:8])

        # ── A4: Per-dimension detection ablation ─────────────────
        print(f"\n  [A4] chain_only_detection")
        suite.results.extend(run_single_ablation(
            "A4_Detect", "chain_only", EVAL_PATH, product, scenarios,
            eval_fn=lambda p, e, weights: evaluate_path(p, e, weights=weights),
            detect_fn=sl_detect_chain_only,
            weights=product.weights,
            n_trials=n_trials, seed=seed,
        ))
        _print_summary(suite.results, "A4_Detect", "chain_only", product.name[:8])

        # ── A5: Evidence weight ablation ─────────────────────────
        print(f"\n  [A5] uniform_weights")
        suite.results.extend(run_single_ablation(
            "A5_Weights", "uniform", EVAL_PATH, product, scenarios,
            eval_fn=lambda p, e, weights: evaluate_path(p, e, weights=weights),
            detect_fn=sl_detect_per_dimension,
            weights=EvidenceWeights(),  # all 1.0
            n_trials=n_trials, seed=seed,
        ))
        _print_summary(suite.results, "A5_Weights", "uniform", product.name[:8])

    suite.runtime_seconds = time.time() - start
    return suite


def _print_summary(results: List[AblationResult], ablation: str, variant: str, product: str):
    """Print a compact summary of detection rates for one variant."""
    relevant = [r for r in results
                if r.ablation == ablation and r.variant == variant
                and r.product == product and r.scenario != "Normal"]
    if not relevant:
        return
    drs = {r.scenario: r.detection_rate for r in relevant}
    las = {r.scenario: r.localization_accuracy for r in relevant}
    dr_str = " ".join(f"{sc}={dr:.3f}" for sc, dr in sorted(drs.items()))
    avg_la = sum(las.values()) / len(las) if las else 0.0
    print(f"    DR: {dr_str}")
    print(f"    Mean LA: {avg_la:.3f}")


if __name__ == "__main__":
    print("=" * 70)
    print("Drug Integrity Algebra — Ablation Study (EXP-003)")
    print("  A1: Evidence type | A2: Fusion op | A3: Propagation op")
    print("  A4: Detection strategy | A5: Evidence weights")
    print("=" * 70)
    print(f"Trials: {N_TRIALS} | Seed: {SEED} | FPR target: {FPR_TARGET*100:.0f}%")
    print()

    suite = run_all_ablations()

    print(f"\n{'='*70}")
    print(f"Completed in {suite.runtime_seconds:.1f}s")
    print(f"{'='*70}\n")
    print(suite.to_table())

    out_dir = Path(__file__).resolve().parents[1] / "experiments"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "ablation_results.json"
    suite.save(str(out_path))
    print(f"\nResults saved to {out_path}")
