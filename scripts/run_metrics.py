"""
Richer Metrics Suite (EXP-004, extended in EXP-006) — ROC/AUC, Spearman, Entropy, Calibration, Sensitivity.

Computes five metric families that go beyond detection rate to characterize
the quality, calibration, and robustness of the drug integrity algebra
relative to baselines.

v3.1 changes (EXP-006, see LOGBOOK.md):
- B1 is included in the failure-severity sweeps (fixed threshold 0.5, as in
  run_experiments.py); all five methods share one sweep helper.
- The excursion sweep passes the parameters apply_failure actually reads
  (duration_fraction, magnitude); the EXP-004 version passed unknown keys
  and silently ran the default F1 at every point.
- Failure-severity sweeps run for all three products.
- Spearman is computed under the global ordering and under the pre-registered
  product-specific orderings (amoxicillin uses a CRT-specific ordering).
- Saves to metrics_results_v3_1.json; metrics_results.json is left untouched.

Usage (from project root):
    python scripts/run_metrics.py
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
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
from models.drug_integrity_opinion import (
    NodeEvidence, RecallStatus, EvidenceWeights,
)
from models.chain_integrity import evaluate_path, ChainEvaluationResult
from baselines.baselines import (
    evaluate_threshold, evaluate_weighted_scoring, evaluate_bayesian,
    evaluate_dempster_shafer,
)

# ═══════════════════════════════════════════════════════════════════
# CONFIG — matches EXP-002b
# ═══════════════════════════════════════════════════════════════════

N_TRIALS = 1000
SEED = 42

EVAL_PATH = [
    "manufacturer", "importer", "central_warehouse",
    "distributor_a", "pharmacy_a",
]

DIM_DISBELIEF_THRESHOLD = 0.02
DIM_UNCERTAINTY_THRESHOLD = 0.50

# Ground-truth severity ordering (1 = safest, 8 = most severe)
# Based on physical impact to drug integrity:
#   Normal: safe | F6: admin gap | F4: evidence gap | F3: 5% counterfeit
#   F5: expired | F1: mild temp | F7: cascading | F2: severe temp
# This is the GLOBAL ordering of EXP-004, applied to all products.
SEVERITY_ORDER = {
    "Normal": 1, "F6": 2, "F4": 3, "F3": 4,
    "F5": 5, "F1": 6, "F7": 7, "F2": 8,
}

# EXP-006 pre-registered product-specific orderings (LOGBOOK.md, EXP-006).
# Insulin and mRNA: cold chain is the dominant physical risk -> global ordering.
# Amoxicillin (CRT solid oral dosage form): a +2 C excursion above 25 C on 5%
# of readings is a minor stability risk; counterfeit packages and sub-minimum
# shelf life are direct patient-safety and compliance failures; F2 (whole batch
# near 42 C for half the window) remains the most severe.
SEVERITY_ORDER_CRT = {
    "Normal": 1, "F6": 2, "F4": 3, "F1": 4,
    "F7": 5, "F3": 6, "F5": 7, "F2": 8,
}

PRODUCT_SEVERITY_ORDER = {
    "Insulin": SEVERITY_ORDER,
    "Amoxicil": SEVERITY_ORDER_CRT,
    "mRNA-bas": SEVERITY_ORDER,
}


def product_key(product: ProductProfile) -> str:
    """Stable short product key (name[:8], stripped of trailing spaces)."""
    return product.name[:8].strip()


def severity_order_for(product: ProductProfile) -> Dict[str, int]:
    """Pre-registered product-specific ordering (falls back to the global one)."""
    return PRODUCT_SEVERITY_ORDER.get(product_key(product), SEVERITY_ORDER)


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
# RAW TRIAL COLLECTION — collect per-trial scores for all methods
# ═══════════════════════════════════════════════════════════════════

def collect_raw_scores(
    product: ProductProfile,
    n_trials: int = N_TRIALS,
    seed: int = SEED,
) -> Dict[str, Dict[str, Dict[str, list]]]:
    """Collect raw per-trial scores for all methods × scenarios.

    Returns: {method: {scenario: {"scores": [...], "uncertainties": [...]}}}
    """
    scenarios = get_product_scenarios(product)
    scenario_list = [("Normal", None)] + [(n, s) for n, s in scenarios.items()]

    results: Dict[str, Dict[str, Dict[str, list]]] = {
        "SL_Algebra": {}, "B1_Threshold": {},
        "B2_WeightedScoring": {}, "B3_Bayesian": {},
        "B4_DempsterShafer": {},
    }

    for sc_name, scenario in scenario_list:
        rng = np.random.default_rng(seed)

        sl_scores, sl_u = [], []
        b1_scores, b2_scores, b3_scores, b4_scores = [], [], [], []

        for _ in range(n_trials):
            evidence = {nid: generate_normal_evidence(product, rng=rng) for nid in EVAL_PATH}
            if scenario is not None:
                evidence = apply_failure(evidence, scenario, product, rng)

            sl = evaluate_path(EVAL_PATH, evidence, weights=product.weights)
            sl_scores.append(sl.integrity_score)
            sl_u.append(sl.uncertainty)

            b1_scores.append(evaluate_threshold(EVAL_PATH, evidence).chain_score)
            b2_scores.append(evaluate_weighted_scoring(EVAL_PATH, evidence).chain_score)
            b3_scores.append(evaluate_bayesian(EVAL_PATH, evidence).chain_score)
            b4_scores.append(evaluate_dempster_shafer(EVAL_PATH, evidence).chain_score)

        results["SL_Algebra"][sc_name] = {"scores": sl_scores, "uncertainties": sl_u}
        results["B1_Threshold"][sc_name] = {"scores": b1_scores, "uncertainties": [0.0]*n_trials}
        results["B2_WeightedScoring"][sc_name] = {"scores": b2_scores, "uncertainties": [0.0]*n_trials}
        results["B3_Bayesian"][sc_name] = {"scores": b3_scores, "uncertainties": [0.0]*n_trials}
        results["B4_DempsterShafer"][sc_name] = {"scores": b4_scores, "uncertainties": [0.0]*n_trials}

    return results


# ═══════════════════════════════════════════════════════════════════
# METRIC 1: ROC CURVES + AUC
# ═══════════════════════════════════════════════════════════════════

def compute_roc_auc(
    normal_scores: List[float],
    failure_scores: List[float],
    n_thresholds: int = 500,
) -> Dict:
    """Compute ROC curve and AUC for a single method × scenario.

    Convention: LOWER score = more likely to be flagged (failure).
    AUC computed via Mann-Whitney U statistic (exact, no binning artifacts).
    ROC curve computed via threshold sweep for plotting.
    """
    normal_arr = np.array(normal_scores)
    failure_arr = np.array(failure_scores)
    n_neg = len(normal_arr)
    n_pos = len(failure_arr)

    # ── AUC via Mann-Whitney U (exact) ────────────────────────
    # AUC = P(failure < normal) + 0.5 * P(failure == normal)
    # This is the correct, numerically stable AUC computation.
    if n_neg == 0 or n_pos == 0:
        auc = 0.5
    else:
        # Vectorized pairwise comparison
        less_count = 0
        equal_count = 0
        for f_score in failure_arr:
            less_count += np.sum(f_score < normal_arr)
            equal_count += np.sum(f_score == normal_arr)
        auc = float((less_count + 0.5 * equal_count) / (n_neg * n_pos))

    # ── ROC curve for plotting ────────────────────────────────
    all_scores = np.concatenate([normal_arr, failure_arr])
    # Use unique score values as thresholds + endpoints
    unique_scores = np.unique(all_scores)
    thresholds = np.concatenate([
        [unique_scores[-1] + 0.01],  # nothing flagged
        unique_scores[::-1],          # sweep from high to low
        [unique_scores[0] - 0.01],   # everything flagged
    ])

    fprs, tprs = [], []
    for t in thresholds:
        fp = np.sum(normal_arr < t)
        tp = np.sum(failure_arr < t)
        fprs.append(fp / n_neg)
        tprs.append(tp / n_pos)

    # Downsample for JSON (keep ~50 points)
    step = max(1, len(fprs) // 50)
    roc_points = [
        {"fpr": float(fprs[i]), "tpr": float(tprs[i])}
        for i in range(0, len(fprs), step)
    ]
    # Always include endpoints
    if roc_points[0]["fpr"] != 0.0 or roc_points[0]["tpr"] != 0.0:
        roc_points.insert(0, {"fpr": 0.0, "tpr": 0.0})
    if roc_points[-1]["fpr"] != 1.0 or roc_points[-1]["tpr"] != 1.0:
        roc_points.append({"fpr": 1.0, "tpr": 1.0})

    return {"auc": auc, "roc_curve": roc_points}


def compute_all_roc(raw: Dict) -> Dict:
    """Compute ROC/AUC for all methods × failure scenarios."""
    results = {}
    for method, scenarios in raw.items():
        results[method] = {}
        normal = scenarios["Normal"]["scores"]
        for sc_name, sc_data in scenarios.items():
            if sc_name == "Normal":
                continue
            roc = compute_roc_auc(normal, sc_data["scores"])
            results[method][sc_name] = roc
    return results


# ═══════════════════════════════════════════════════════════════════
# METRIC 2: SPEARMAN RANK CORRELATION
# ═══════════════════════════════════════════════════════════════════

def compute_spearman(raw: Dict, order: Optional[Dict[str, int]] = None) -> Dict:
    """Compute Spearman ρ between a severity ordering and method mean scores.

    Args:
        raw:   {method: {scenario: {"scores": [...]}}} as returned by
               collect_raw_scores.
        order: {scenario: rank} with 1 = safest. Defaults to the global
               SEVERITY_ORDER; pass severity_order_for(product) for the
               pre-registered product-specific ordering (EXP-006).
    """
    if order is None:
        order = SEVERITY_ORDER
    results = {}
    for method, scenarios in raw.items():
        # Get mean scores per scenario
        mean_scores = {}
        for sc_name, sc_data in scenarios.items():
            mean_scores[sc_name] = float(np.mean(sc_data["scores"]))

        # Build paired arrays (ground-truth rank, method mean score)
        gt_ranks = []
        method_scores = []
        for sc_name in order:
            if sc_name in mean_scores:
                gt_ranks.append(order[sc_name])
                method_scores.append(mean_scores[sc_name])

        # Spearman: ground-truth rank is ascending severity (1=safest),
        # method score is descending safety (high=safe). So we expect
        # NEGATIVE correlation: higher severity → lower score.
        # We negate so positive ρ means correct ordering.
        rho, pvalue = sp_stats.spearmanr(gt_ranks, [-s for s in method_scores])

        results[method] = {
            "rho": float(rho),
            "pvalue": float(pvalue),
            "mean_scores": mean_scores,
        }
    return results


# ═══════════════════════════════════════════════════════════════════
# METRIC 3: SHANNON ENTROPY OF OUTPUT DISTRIBUTIONS
# ═══════════════════════════════════════════════════════════════════

def compute_entropy(scores: List[float], n_bins: int = 50) -> float:
    """Compute Shannon entropy of a discretized score distribution."""
    hist, _ = np.histogram(scores, bins=n_bins, range=(0.0, 1.0), density=False)
    # Normalize to probability distribution
    total = np.sum(hist)
    if total == 0:
        return 0.0
    probs = hist / total
    # Remove zeros (0 * log(0) = 0 by convention)
    probs = probs[probs > 0]
    return float(-np.sum(probs * np.log2(probs)))


def compute_all_entropy(raw: Dict) -> Dict:
    """Compute Shannon entropy for each method across all scenarios."""
    results = {}
    for method, scenarios in raw.items():
        results[method] = {}
        for sc_name, sc_data in scenarios.items():
            h = compute_entropy(sc_data["scores"])
            results[method][sc_name] = {
                "entropy_bits": h,
                "score_std": float(np.std(sc_data["scores"])),
            }
        # Also compute pooled entropy across ALL scenarios
        all_scores = []
        for sc_data in scenarios.values():
            all_scores.extend(sc_data["scores"])
        results[method]["_pooled"] = {
            "entropy_bits": compute_entropy(all_scores),
            "score_std": float(np.std(all_scores)),
        }
    return results


# ═══════════════════════════════════════════════════════════════════
# METRIC 4: CALIBRATION (SL ALGEBRA ONLY)
# ═══════════════════════════════════════════════════════════════════

def compute_calibration(raw: Dict, n_bins: int = 10) -> Dict:
    """Compute calibration metrics for the SL algebra.

    Three calibration perspectives:
    1. Pooled: all scenarios mixed (classical reliability diagram)
    2. Normal-only: when drugs ARE safe, is P(ω) well-calibrated?
    3. Evidence-quality: does uncertainty u predict detection difficulty?

    Note: P(ω) is an EVIDENCE-QUALITY score, not P(drug is safe). It measures
    how much the observed evidence supports integrity. A drug with subtle
    counterfeiting (F3, 5%) will score high because the EVIDENCE looks clean
    for 95% of packages. This is correct behavior — the score reflects what
    the evidence says, not omniscient ground truth.
    """
    sl_data = raw["SL_Algebra"]

    # ── Pooled calibration (classical) ─────────────────────────
    all_scores, all_labels = [], []
    for sc_name, sc_data in sl_data.items():
        is_safe = 1 if sc_name == "Normal" else 0
        for score in sc_data["scores"]:
            all_scores.append(score)
            all_labels.append(is_safe)

    all_scores = np.array(all_scores)
    all_labels = np.array(all_labels)
    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    pooled_bins = []
    ece = 0.0
    total = len(all_scores)

    for i in range(n_bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        mask = (all_scores >= lo) & (all_scores < hi)
        if i == n_bins - 1:
            mask = (all_scores >= lo) & (all_scores <= hi)
        n_in_bin = int(np.sum(mask))
        if n_in_bin == 0:
            pooled_bins.append({
                "bin_lo": float(lo), "bin_hi": float(hi),
                "n_samples": 0, "predicted_integrity": None, "actual_integrity": None,
            })
            continue
        pred_mean = float(np.mean(all_scores[mask]))
        actual_safe = float(np.mean(all_labels[mask]))
        pooled_bins.append({
            "bin_lo": float(lo), "bin_hi": float(hi),
            "n_samples": n_in_bin,
            "predicted_integrity": pred_mean,
            "actual_integrity": actual_safe,
        })
        ece += (n_in_bin / total) * abs(actual_safe - pred_mean)

    # ── Per-scenario score summary ────────────────────────────
    per_scenario = {}
    for sc_name, sc_data in sl_data.items():
        scores = np.array(sc_data["scores"])
        uncertainties = np.array(sc_data["uncertainties"])
        per_scenario[sc_name] = {
            "mean_score": float(np.mean(scores)),
            "std_score": float(np.std(scores)),
            "mean_uncertainty": float(np.mean(uncertainties)),
            "std_uncertainty": float(np.std(uncertainties)),
            "is_safe": sc_name == "Normal",
            "score_range": [float(np.min(scores)), float(np.max(scores))],
        }

    # ── Uncertainty-accuracy analysis ─────────────────────────
    # For each failure scenario: does higher uncertainty correlate with
    # larger score deviation from Normal?
    normal_mean = float(np.mean(sl_data["Normal"]["scores"]))
    uncertainty_vs_deviation = {}
    for sc_name, sc_data in sl_data.items():
        if sc_name == "Normal":
            continue
        scores = np.array(sc_data["scores"])
        uncertainties = np.array(sc_data["uncertainties"])
        deviations = normal_mean - scores  # positive = score below normal
        if len(deviations) > 2:
            corr, p = sp_stats.spearmanr(uncertainties, deviations)
        else:
            corr, p = 0.0, 1.0
        uncertainty_vs_deviation[sc_name] = {
            "mean_deviation": float(np.mean(deviations)),
            "mean_uncertainty": float(np.mean(uncertainties)),
            "u_deviation_correlation": float(corr),
            "p_value": float(p),
        }

    return {
        "pooled_bins": pooled_bins,
        "ece": float(ece),
        "n_total": total,
        "per_scenario": per_scenario,
        "uncertainty_vs_deviation": uncertainty_vs_deviation,
        "interpretation": (
            "ECE is high because P(omega) measures EVIDENCE QUALITY, not P(safe). "
            "Subtle failures (F3, F4, F6) produce high P(omega) because the "
            "available evidence looks clean. This is correct: the score reflects "
            "what the evidence says. Detection of these subtle failures relies "
            "on per-dimension monitoring, not the aggregate score."
        ),
    }


# ═══════════════════════════════════════════════════════════════════
# METRIC 5: SENSITIVITY ANALYSIS
# ═══════════════════════════════════════════════════════════════════

def sl_detect_with_params(
    chain_result: ChainEvaluationResult,
    chain_threshold: float,
    dim_d_thresh: float = DIM_DISBELIEF_THRESHOLD,
    dim_u_thresh: float = DIM_UNCERTAINTY_THRESHOLD,
) -> bool:
    """Detection with configurable per-dimension thresholds."""
    if chain_result.integrity_score < chain_threshold:
        return True
    for node_result in chain_result.node_results.values():
        for dim_name, dim_op in node_result.evidence_opinions.items():
            if dim_name == "recall":
                continue
            if dim_op.disbelief > dim_d_thresh:
                return True
            if dim_op.uncertainty > dim_u_thresh:
                return True
    if chain_result.uncertainty > 0.3:
        return True
    return False


# ═══════════════════════════════════════════════════════════════════
# FAILURE-SEVERITY SWEEPS (EXP-006): all five methods, shared helper
# ═══════════════════════════════════════════════════════════════════

COUNTERFEIT_FRACTIONS = [0.01, 0.02, 0.03, 0.05, 0.10, 0.15, 0.20]
EXCURSION_DURATIONS = [0.01, 0.02, 0.05, 0.10, 0.20, 0.30, 0.50]


def counterfeit_scenario(fraction: float) -> FailureScenario:
    """F3 at the standard importer node with the given counterfeit fraction."""
    return FailureScenario(
        FailureType.F3_COUNTERFEIT, "importer",
        {"counterfeit_fraction": fraction},
    )


def excursion_scenario(duration_fraction: float, magnitude: float = 2.0) -> FailureScenario:
    """F1-type excursion at the standard F1 node (distributor_a).

    Uses the parameter names apply_failure reads (duration_fraction,
    magnitude). The EXP-004 sweep passed excursion_fraction / temp_deviation,
    which apply_failure ignores, so it ran the default F1 at every point.
    """
    return FailureScenario(
        FailureType.F1_MILD_EXCURSION, "distributor_a",
        {"duration_fraction": duration_fraction, "magnitude": magnitude},
    )


def sweep_methods(product: ProductProfile) -> List[Tuple[str, object, bool, Optional[float]]]:
    """(name, evaluator, is_sl, fixed_threshold) for the five methods.

    fixed_threshold None means the chain threshold is calibrated at the 1st
    percentile of that method's Normal distribution, as in run_experiments.py.
    B1's binary output is thresholded at 0.5, exactly as in run_experiments.py.
    """
    return [
        ("SL", lambda ev: evaluate_path(EVAL_PATH, ev, weights=product.weights), True, None),
        ("B1", lambda ev: evaluate_threshold(EVAL_PATH, ev), False, 0.5),
        ("B2", lambda ev: evaluate_weighted_scoring(EVAL_PATH, ev), False, None),
        ("B3", lambda ev: evaluate_bayesian(EVAL_PATH, ev), False, None),
        ("B4", lambda ev: evaluate_dempster_shafer(EVAL_PATH, ev), False, None),
    ]


def detection_rates_for_scenario(
    product: ProductProfile,
    scenario: FailureScenario,
    n_trials: int = N_TRIALS,
    seed: int = SEED,
) -> Dict[str, float]:
    """Detection rate of every method on one scenario (same protocol as the
    main suite: per-method 1st-percentile calibration on Normal, B1 at 0.5,
    SL with the per-dimension rule)."""
    rates: Dict[str, float] = {}
    for name, eval_fn, is_sl, fixed_thr in sweep_methods(product):
        if fixed_thr is None:
            rng = np.random.default_rng(seed)
            normal_scores = []
            for _ in range(n_trials):
                ev = {nid: generate_normal_evidence(product, rng=rng) for nid in EVAL_PATH}
                r = eval_fn(ev)
                normal_scores.append(r.integrity_score if is_sl else r.chain_score)
            thr = float(np.percentile(normal_scores, 1.0))
        else:
            thr = fixed_thr

        rng = np.random.default_rng(seed)
        detections = 0
        for _ in range(n_trials):
            ev = {nid: generate_normal_evidence(product, rng=rng) for nid in EVAL_PATH}
            ev = apply_failure(ev, scenario, product, rng)
            r = eval_fn(ev)
            det = sl_detect_with_params(r, thr) if is_sl else (r.chain_score < thr)
            if det:
                detections += 1
        rates[name] = detections / n_trials
    return rates


def failure_severity_sweeps(
    product: ProductProfile,
    n_trials: int = N_TRIALS,
    seed: int = SEED,
) -> Dict[str, Dict[str, Dict[str, float]]]:
    """Counterfeit-fraction and excursion-duration sweeps for one product."""
    cf = {
        str(f): detection_rates_for_scenario(product, counterfeit_scenario(f), n_trials, seed)
        for f in COUNTERFEIT_FRACTIONS
    }
    ef = {
        str(d): detection_rates_for_scenario(product, excursion_scenario(d), n_trials, seed)
        for d in EXCURSION_DURATIONS
    }
    return {"counterfeit_fraction_sweep": cf, "excursion_duration_sweep": ef}


def sensitivity_sweep(
    product: ProductProfile,
    n_trials: int = N_TRIALS,
    seed: int = SEED,
) -> Dict:
    """Sweep key parameters and measure DR on each failure scenario."""
    scenarios = get_product_scenarios(product)
    results = {}

    # Pre-generate all evidence (Normal + failures) once for consistency
    def generate_all_evidence(rng):
        normal_ev = {nid: generate_normal_evidence(product, rng=rng) for nid in EVAL_PATH}
        return normal_ev

    # ── Sweep 1: DIM_DISBELIEF_THRESHOLD ──────────────────────
    d_values = [0.005, 0.01, 0.02, 0.05, 0.10]
    d_results = {}

    for d_thresh in d_values:
        d_results[str(d_thresh)] = {}
        for sc_name, scenario in scenarios.items():
            rng = np.random.default_rng(seed)

            # Calibrate chain threshold at this d_thresh
            normal_scores = []
            for _ in range(n_trials):
                ev = generate_all_evidence(rng)
                r = evaluate_path(EVAL_PATH, ev, weights=product.weights)
                normal_scores.append(r.integrity_score)
            chain_thresh = float(np.percentile(normal_scores, 1.0))

            # Run failure trials
            rng = np.random.default_rng(seed)
            detections = 0
            for _ in range(n_trials):
                ev = generate_all_evidence(rng)
                ev = apply_failure(ev, scenario, product, rng)
                r = evaluate_path(EVAL_PATH, ev, weights=product.weights)
                if sl_detect_with_params(r, chain_thresh, dim_d_thresh=d_thresh):
                    detections += 1

            d_results[str(d_thresh)][sc_name] = detections / n_trials

    results["dim_disbelief_threshold"] = d_results

    # ── Sweep 2: DIM_UNCERTAINTY_THRESHOLD ────────────────────
    u_values = [0.20, 0.35, 0.50, 0.65, 0.80]
    u_results = {}

    for u_thresh in u_values:
        u_results[str(u_thresh)] = {}
        for sc_name, scenario in scenarios.items():
            rng = np.random.default_rng(seed)

            normal_scores = []
            for _ in range(n_trials):
                ev = generate_all_evidence(rng)
                r = evaluate_path(EVAL_PATH, ev, weights=product.weights)
                normal_scores.append(r.integrity_score)
            chain_thresh = float(np.percentile(normal_scores, 1.0))

            rng = np.random.default_rng(seed)
            detections = 0
            for _ in range(n_trials):
                ev = generate_all_evidence(rng)
                ev = apply_failure(ev, scenario, product, rng)
                r = evaluate_path(EVAL_PATH, ev, weights=product.weights)
                if sl_detect_with_params(r, chain_thresh, dim_u_thresh=u_thresh):
                    detections += 1

            u_results[str(u_thresh)][sc_name] = detections / n_trials

    results["dim_uncertainty_threshold"] = u_results

    # ── Sweep 3: cold_chain_weight ────────────────────────────
    cc_values = [0.6, 1.0, 1.4, 1.8]
    cc_results = {}

    for cc_w in cc_values:
        cc_results[str(cc_w)] = {}
        weights = EvidenceWeights(
            serialization=product.weights.serialization,
            cold_chain=cc_w,
            shelf_life=product.weights.shelf_life,
            documentation=product.weights.documentation,
            recall=product.weights.recall,
        )

        for sc_name, scenario in scenarios.items():
            rng = np.random.default_rng(seed)

            # Calibrate with these weights
            normal_scores = []
            for _ in range(n_trials):
                ev = generate_all_evidence(rng)
                r = evaluate_path(EVAL_PATH, ev, weights=weights)
                normal_scores.append(r.integrity_score)
            chain_thresh = float(np.percentile(normal_scores, 1.0))

            rng = np.random.default_rng(seed)
            detections = 0
            for _ in range(n_trials):
                ev = generate_all_evidence(rng)
                ev = apply_failure(ev, scenario, product, rng)
                r = evaluate_path(EVAL_PATH, ev, weights=weights)
                if sl_detect_with_params(r, chain_thresh):
                    detections += 1

            cc_results[str(cc_w)][sc_name] = detections / n_trials

    results["cold_chain_weight"] = cc_results

    # ── Sweeps 4 and 5: failure severity (F3 counterfeit fraction, F1
    #    excursion duration), all five methods, via the shared helper ──
    severity = failure_severity_sweeps(product, n_trials=n_trials, seed=seed)
    results["counterfeit_fraction_sweep"] = severity["counterfeit_fraction_sweep"]
    results["excursion_duration_sweep"] = severity["excursion_duration_sweep"]

    return results


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def run_all_metrics() -> Dict:
    start = time.time()
    all_results = {}

    for product in ALL_PRODUCTS:
        pname = product.name[:8]
        print(f"\n{'='*60}")
        print(f"Product: {product.name}")
        print(f"{'='*60}")

        print("  Collecting raw scores...")
        raw = collect_raw_scores(product)

        # Metric 1: ROC/AUC
        print("  Computing ROC/AUC...")
        roc = compute_all_roc(raw)
        print("    AUC summary:")
        for method in ["SL_Algebra", "B1_Threshold", "B2_WeightedScoring", "B3_Bayesian", "B4_DempsterShafer"]:
            aucs = [roc[method][sc]["auc"] for sc in roc[method]]
            print(f"      {method:<22} mean AUC = {np.mean(aucs):.4f}  "
                  f"(min={np.min(aucs):.4f} on {min(roc[method], key=lambda s: roc[method][s]['auc'])})")

        # Metric 2: Spearman (global ordering, EXP-004)
        print("  Computing Spearman rank correlation (global ordering)...")
        spearman = compute_spearman(raw)
        for method, res in spearman.items():
            print(f"    {method:<22} ρ = {res['rho']:.4f}  (p = {res['pvalue']:.2e})")

        # Metric 2b: Spearman under the pre-registered product-specific ordering (EXP-006)
        ps_order = severity_order_for(product)
        print("  Computing Spearman rank correlation (product-specific ordering)...")
        spearman_ps = compute_spearman(raw, ps_order)
        for method, res in spearman_ps.items():
            print(f"    {method:<22} ρ = {res['rho']:.4f}  (p = {res['pvalue']:.2e})")

        # Metric 3: Entropy
        print("  Computing Shannon entropy...")
        entropy = compute_all_entropy(raw)
        for method in ["SL_Algebra", "B1_Threshold", "B2_WeightedScoring", "B3_Bayesian", "B4_DempsterShafer"]:
            h = entropy[method]["_pooled"]["entropy_bits"]
            print(f"    {method:<22} pooled H = {h:.4f} bits")

        # Metric 4: Calibration (SL only)
        print("  Computing calibration (SL only)...")
        calibration = compute_calibration(raw)
        print(f"    ECE = {calibration['ece']:.4f} (high by design — see interpretation)")
        active_bins = [b for b in calibration["pooled_bins"] if b["n_samples"] and b["n_samples"] > 0]
        for b in active_bins:
            act = f"{b['actual_integrity']:.3f}" if b["actual_integrity"] is not None else "N/A"
            print(f"    Bin [{b['bin_lo']:.1f}, {b['bin_hi']:.1f}): "
                  f"n={b['n_samples']:>5}  pred={b['predicted_integrity']:.4f}  "
                  f"actual={act}")
        print("    Per-scenario scores:")
        for sc_name in ["Normal", "F1", "F2", "F3", "F4", "F5", "F6", "F7"]:
            if sc_name in calibration["per_scenario"]:
                ps = calibration["per_scenario"][sc_name]
                safe_str = "SAFE" if ps["is_safe"] else "FAIL"
                print(f"      {sc_name:>6} [{safe_str}]: P(ω)={ps['mean_score']:.4f}±{ps['std_score']:.4f}  "
                      f"u={ps['mean_uncertainty']:.4f}")
        print("    Uncertainty vs deviation:")
        for sc_name in ["F1", "F2", "F3", "F4", "F5", "F6", "F7"]:
            if sc_name in calibration["uncertainty_vs_deviation"]:
                uv = calibration["uncertainty_vs_deviation"][sc_name]
                print(f"      {sc_name}: dev={uv['mean_deviation']:.4f}  "
                      f"u={uv['mean_uncertainty']:.4f}  "
                      f"corr={uv['u_deviation_correlation']:.3f}")

        all_results[pname] = {
            "roc_auc": roc,
            "spearman": spearman,
            "spearman_product_specific": spearman_ps,
            "severity_ordering": {"global": SEVERITY_ORDER, "product_specific": ps_order},
            "entropy": entropy,
            "calibration": calibration,
        }

    # Metric 5: Sensitivity (Insulin only — representative biologic)
    print(f"\n{'='*60}")
    print("Sensitivity Analysis (Insulin)")
    print(f"{'='*60}")
    sensitivity = sensitivity_sweep(INSULIN)

    print("\n  DIM_DISBELIEF_THRESHOLD sweep:")
    for d_val, sc_drs in sensitivity["dim_disbelief_threshold"].items():
        dr_str = " ".join(f"{sc}={dr:.3f}" for sc, dr in sorted(sc_drs.items()))
        print(f"    d_thresh={d_val}: {dr_str}")

    print("\n  DIM_UNCERTAINTY_THRESHOLD sweep:")
    for u_val, sc_drs in sensitivity["dim_uncertainty_threshold"].items():
        dr_str = " ".join(f"{sc}={dr:.3f}" for sc, dr in sorted(sc_drs.items()))
        print(f"    u_thresh={u_val}: {dr_str}")

    print("\n  cold_chain_weight sweep:")
    for cc_val, sc_drs in sensitivity["cold_chain_weight"].items():
        dr_str = " ".join(f"{sc}={dr:.3f}" for sc, dr in sorted(sc_drs.items()))
        print(f"    cc_weight={cc_val}: {dr_str}")

    print("\n  F3 counterfeit_fraction sweep (SL, B1, B2, B3, B4):")
    for cf_val, methods in sensitivity["counterfeit_fraction_sweep"].items():
        m_str = "  ".join(f"{m}={dr:.3f}" for m, dr in sorted(methods.items()))
        print(f"    cf={cf_val}: {m_str}")

    print("\n  F1 excursion_duration sweep (SL, B1, B2, B3, B4):")
    for ef_val, methods in sensitivity["excursion_duration_sweep"].items():
        m_str = "  ".join(f"{m}={dr:.3f}" for m, dr in sorted(methods.items()))
        print(f"    duration={ef_val}: {m_str}")

    all_results["sensitivity_insulin"] = sensitivity

    # Failure-severity sweeps for the other products (EXP-006). Insulin's
    # sweeps are the ones computed above; they are referenced, not recomputed.
    print(f"\n{'='*60}")
    print("Failure-severity sweeps, all products (EXP-006)")
    print(f"{'='*60}")
    severity_sweeps = {}
    for product in ALL_PRODUCTS:
        pname = product.name[:8]
        if product is INSULIN:
            severity_sweeps[pname] = {
                k: sensitivity[k]
                for k in ("counterfeit_fraction_sweep", "excursion_duration_sweep")
            }
        else:
            print(f"  {product.name}")
            severity_sweeps[pname] = failure_severity_sweeps(product)
        for key in ("counterfeit_fraction_sweep", "excursion_duration_sweep"):
            print(f"    {pname.strip()} {key}:")
            for level, methods in severity_sweeps[pname][key].items():
                m_str = "  ".join(f"{m}={dr:.3f}" for m, dr in sorted(methods.items()))
                print(f"      {level}: {m_str}")
    all_results["severity_sweeps"] = severity_sweeps
    all_results["runtime_seconds"] = time.time() - start

    return all_results


if __name__ == "__main__":
    print("=" * 60)
    print("Drug Integrity Algebra — Richer Metrics v3.1 (EXP-004 + EXP-006)")
    print("  ROC/AUC | Spearman (global + product-specific) | Entropy | Calibration | Sensitivity")
    print("=" * 60)

    results = run_all_metrics()

    print(f"\n{'='*60}")
    print(f"Completed in {results['runtime_seconds']:.1f}s")
    print(f"{'='*60}")

    out_dir = Path(__file__).resolve().parents[1] / "experiments"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "metrics_results_v3_1.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")
