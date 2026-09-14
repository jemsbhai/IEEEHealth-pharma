"""
Operational Stress Test (EXP-007) — detection and false alarms under benign noise.

Pre-registered in LOGBOOK.md (EXP-007). For each noise regime (R0 reference,
R1 moderate, R2 heavy) and product:

  1. Calibrate every method on N_CALIB noisy Normal trials (CALIB_SEED):
       SL-cal    : chain score, chain uncertainty, max per-dimension disbelief
                   and max per-dimension uncertainty, each at the 0.25%/99.75%
                   point (equal split of the 1% budget over four criteria)
       SL-fixed  : chain score at the 1st percentile; per-dimension d > 0.02,
                   u > 0.50 and chain u > 0.30 fixed (the main-suite rule)
       B1-fixed  : rules as deployed
       B1-tuned  : max failure rate / temperature tolerance at the 99.75th
                   percentile of the per-trial maximum over nodes, minimum scan
                   rate at the 0.25th percentile of the per-trial minimum
       B2, B3, B4: chain score at the 1st percentile
  2. Evaluate on N_TRIALS fresh trials (SEED) for Normal and F1-F7, with the
     failure injected on top of the noisy Normal evidence.
  3. Severity ranking (Spearman, global ordering) and pooled entropy per
     product and regime; counterfeit sweep on insulin under R1 and R2.

Usage (from project root):
    python scripts/run_stress_test.py
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from simulation.simulator import (
    ALL_PRODUCTS, INSULIN, ProductProfile, FailureScenario,
    NoiseProfile, NOISE_REGIMES,
    generate_noisy_normal_evidence, apply_failure,
    localization_accuracy_from_trials,
)
from models.chain_integrity import evaluate_path, ChainEvaluationResult
from baselines.baselines import (
    evaluate_threshold, evaluate_weighted_scoring, evaluate_bayesian,
    evaluate_dempster_shafer,
)
from run_experiments import (
    EVAL_PATH, DIM_DISBELIEF_THRESHOLD, DIM_UNCERTAINTY_THRESHOLD,
    clopper_pearson, mean_ci_halfwidth, get_product_scenarios,
)
from run_metrics import (
    SEVERITY_ORDER, compute_spearman, compute_all_entropy,
    counterfeit_scenario, COUNTERFEIT_FRACTIONS,
)

# ═══════════════════════════════════════════════════════════════════
# CONFIG (pre-registered)
# ═══════════════════════════════════════════════════════════════════

N_TRIALS = 1000
N_CALIB = 5000
SEED = 42
CALIB_SEED = 4242
FPR_TARGET = 0.01
CHAIN_U_THRESHOLD = 0.30          # main-suite fixed chain-uncertainty criterion
SL_CRITERIA = 4                   # criteria sharing the 1% budget in SL-cal
REGIMES = ["R0", "R1", "R2"]
METHODS = ["SL-fixed", "SL-cal", "B1-fixed", "B1-tuned", "B2", "B3", "B4"]
SWEEP_REGIMES = ["R1", "R2"]


# ═══════════════════════════════════════════════════════════════════
# SL FEATURES AND DETECTION
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SLThresholds:
    """Detection fires if score < score, or chain_u > chain_u, or any
    per-dimension disbelief > dim_d, or any per-dimension uncertainty > dim_u."""
    score: float
    chain_u: float
    dim_d: float
    dim_u: float


@dataclass
class SLFeatures:
    score: float
    chain_u: float
    d_max: float
    u_max: float
    per_node: Dict[str, Tuple[float, float]]   # node -> (max d, max u) over evidence types
    # EXP-007b: per-evidence-type maxima over nodes, and per-node per-type values
    type_max: Dict[str, Tuple[float, float]] = field(default_factory=dict)      # type -> (max d, max u)
    per_node_type: Dict[str, Dict[str, Tuple[float, float]]] = field(default_factory=dict)


EVIDENCE_TYPES = ["serialization", "cold_chain", "shelf_life", "documentation"]


def sl_features(chain: ChainEvaluationResult) -> SLFeatures:
    """Chain score, chain uncertainty and per-node maxima of per-dimension
    disbelief/uncertainty (recall excluded, as in the main suite), plus the
    per-evidence-type maxima over nodes used by EXP-007b."""
    per_node: Dict[str, Tuple[float, float]] = {}
    per_node_type: Dict[str, Dict[str, Tuple[float, float]]] = {}
    type_max: Dict[str, Tuple[float, float]] = {t: (0.0, 0.0) for t in EVIDENCE_TYPES}
    d_max = 0.0
    u_max = 0.0
    for node_id, nr in chain.node_results.items():
        nd = 0.0
        nu = 0.0
        per_node_type[node_id] = {}
        for dim_name, op in nr.evidence_opinions.items():
            if dim_name == "recall":
                continue
            nd = max(nd, op.disbelief)
            nu = max(nu, op.uncertainty)
            per_node_type[node_id][dim_name] = (op.disbelief, op.uncertainty)
            if dim_name in type_max:
                td, tu = type_max[dim_name]
                type_max[dim_name] = (max(td, op.disbelief), max(tu, op.uncertainty))
        per_node[node_id] = (nd, nu)
        d_max = max(d_max, nd)
        u_max = max(u_max, nu)
    return SLFeatures(chain.integrity_score, chain.uncertainty, d_max, u_max, per_node,
                      type_max=type_max, per_node_type=per_node_type)


def sl_detect(feats: SLFeatures, thr: SLThresholds) -> Tuple[bool, List[str]]:
    """Same four-criterion rule as run_experiments.sl_detect_per_dimension,
    with every threshold explicit. Flagged nodes come from the per-dimension
    criteria only, as in the main suite."""
    detected = (
        feats.score < thr.score
        or feats.chain_u > thr.chain_u
        or feats.d_max > thr.dim_d
        or feats.u_max > thr.dim_u
    )
    flagged = [n for n, (nd, nu) in feats.per_node.items() if nd > thr.dim_d or nu > thr.dim_u]
    return detected, flagged


# ═══════════════════════════════════════════════════════════════════
# CALIBRATION
# ═══════════════════════════════════════════════════════════════════

def pct(values: Sequence[float], q: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=float), q))


def b1_trial_statistics(evidence: Dict) -> Tuple[float, float, float]:
    """Per-trial (max failure rate, min scan rate, max excursion) over nodes."""
    max_fr = 0.0
    min_sr = 1.0
    max_exc = 0.0
    for nid in EVAL_PATH:
        e = evidence[nid]
        s = e.serialization
        if s is not None:
            scanned = s.scanned_success + s.scanned_failure
            fr = s.scanned_failure / scanned if scanned else 0.0
            sr = scanned / s.total_packages if s.total_packages else 1.0
            max_fr = max(max_fr, fr)
            min_sr = min(min_sr, sr)
        cc = e.cold_chain
        if cc is not None and cc.readings:
            exc = max(max(t - cc.required_max, cc.required_min - t, 0.0) for t in cc.readings)
            max_exc = max(max_exc, exc)
    return max_fr, min_sr, max_exc


@dataclass
class Calibration:
    regime: str
    product: str
    n_calib: int
    sl_cal: SLThresholds
    sl_fixed: SLThresholds
    b1_tuned: Dict[str, float]
    chain_thresholds: Dict[str, float]     # B2, B3, B4

    def to_dict(self) -> Dict:
        return {
            "regime": self.regime, "product": self.product, "n_calib": self.n_calib,
            "sl_cal": asdict(self.sl_cal), "sl_fixed": asdict(self.sl_fixed),
            "b1_tuned": self.b1_tuned, "chain_thresholds": self.chain_thresholds,
        }


def calibrate(product: ProductProfile, noise: NoiseProfile,
              n_calib: int = N_CALIB, seed: int = CALIB_SEED) -> Calibration:
    rng = np.random.default_rng(seed)
    scores, chain_us, d_maxes, u_maxes = [], [], [], []
    b2s, b3s, b4s = [], [], []
    frs, srs, excs = [], [], []
    for _ in range(n_calib):
        ev = {nid: generate_noisy_normal_evidence(product, noise, rng) for nid in EVAL_PATH}
        f = sl_features(evaluate_path(EVAL_PATH, ev, weights=product.weights))
        scores.append(f.score); chain_us.append(f.chain_u)
        d_maxes.append(f.d_max); u_maxes.append(f.u_max)
        b2s.append(evaluate_weighted_scoring(EVAL_PATH, ev).chain_score)
        b3s.append(evaluate_bayesian(EVAL_PATH, ev).chain_score)
        b4s.append(evaluate_dempster_shafer(EVAL_PATH, ev).chain_score)
        fr, sr, exc = b1_trial_statistics(ev)
        frs.append(fr); srs.append(sr); excs.append(exc)

    a = FPR_TARGET / SL_CRITERIA          # 0.0025 per criterion
    sl_cal = SLThresholds(
        score=pct(scores, 100 * a),
        chain_u=pct(chain_us, 100 * (1 - a)),
        dim_d=pct(d_maxes, 100 * (1 - a)),
        dim_u=pct(u_maxes, 100 * (1 - a)),
    )
    sl_fixed = SLThresholds(
        score=pct(scores, 100 * FPR_TARGET),
        chain_u=CHAIN_U_THRESHOLD,
        dim_d=DIM_DISBELIEF_THRESHOLD,
        dim_u=DIM_UNCERTAINTY_THRESHOLD,
    )
    b1_tuned = {
        "max_failure_rate": pct(frs, 100 * (1 - a)),
        "min_scan_rate": pct(srs, 100 * a),
        "temp_tolerance": pct(excs, 100 * (1 - a)),
    }
    chain_thresholds = {
        "B2": pct(b2s, 100 * FPR_TARGET),
        "B3": pct(b3s, 100 * FPR_TARGET),
        "B4": pct(b4s, 100 * FPR_TARGET),
    }
    return Calibration(noise.name, product.name[:8], n_calib, sl_cal, sl_fixed,
                       b1_tuned, chain_thresholds)


# ═══════════════════════════════════════════════════════════════════
# EVALUATION
# ═══════════════════════════════════════════════════════════════════

@dataclass
class StressRow:
    regime: str
    product: str
    method: str
    scenario: str
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


def evaluate_scenario(
    product: ProductProfile, noise: NoiseProfile, cal: Calibration,
    scenario: Optional[FailureScenario], scenario_name: str,
    n_trials: int = N_TRIALS, seed: int = SEED,
) -> Tuple[List[StressRow], Dict[str, List[float]]]:
    """Run one scenario under one regime; return rows for all seven method
    variants and the per-method score lists (for ranking/entropy)."""
    rng = np.random.default_rng(seed)
    is_failure = scenario is not None
    targets = scenario.localization_target_set() if scenario else ()

    scores: Dict[str, List[float]] = {m: [] for m in METHODS}
    dets: Dict[str, List[bool]] = {m: [] for m in METHODS}
    locs: Dict[str, List[List[str]]] = {m: [] for m in METHODS}
    sl_u: List[float] = []

    for _ in range(n_trials):
        ev = {nid: generate_noisy_normal_evidence(product, noise, rng) for nid in EVAL_PATH}
        if scenario is not None:
            ev = apply_failure(ev, scenario, product, rng)

        chain = evaluate_path(EVAL_PATH, ev, weights=product.weights)
        f = sl_features(chain)
        sl_u.append(f.chain_u)
        for name, thr in (("SL-fixed", cal.sl_fixed), ("SL-cal", cal.sl_cal)):
            det, flagged = sl_detect(f, thr)
            scores[name].append(f.score); dets[name].append(det); locs[name].append(flagged)

        r = evaluate_threshold(EVAL_PATH, ev)
        scores["B1-fixed"].append(r.chain_score); dets["B1-fixed"].append(r.detected)
        locs["B1-fixed"].append([r.weakest_node])

        r = evaluate_threshold(EVAL_PATH, ev, **cal.b1_tuned)
        scores["B1-tuned"].append(r.chain_score); dets["B1-tuned"].append(r.detected)
        locs["B1-tuned"].append([r.weakest_node])

        for name, fn in (("B2", evaluate_weighted_scoring), ("B3", evaluate_bayesian),
                         ("B4", evaluate_dempster_shafer)):
            r = fn(EVAL_PATH, ev)
            thr = cal.chain_thresholds[name]
            scores[name].append(r.chain_score); dets[name].append(r.chain_score < thr)
            locs[name].append([r.weakest_node])

    rows: List[StressRow] = []
    for m in METHODS:
        n_det = int(sum(dets[m]))
        rate = n_det / n_trials
        la = localization_accuracy_from_trials(dets[m], locs[m], targets) if (is_failure and targets) else 0.0
        rows.append(StressRow(
            regime=noise.name, product=product.name[:8], method=m, scenario=scenario_name,
            n_trials=n_trials,
            detection_rate=rate if is_failure else 0.0,
            false_positive_rate=rate if not is_failure else 0.0,
            localization_accuracy=la,
            mean_score=float(np.mean(scores[m])), std_score=float(np.std(scores[m])),
            mean_uncertainty=float(np.mean(sl_u)) if m.startswith("SL") else 0.0,
            n_detected=n_det,
            rate_ci=clopper_pearson(n_det, n_trials),
            localization_accuracy_ci=clopper_pearson(int(round(la * n_det)), n_det) if is_failure else (0.0, 0.0),
            mean_score_ci_halfwidth=mean_ci_halfwidth(scores[m]),
        ))
    return rows, scores


def counterfeit_sweep(product: ProductProfile, noise: NoiseProfile, cal: Calibration,
                      n_trials: int = N_TRIALS, seed: int = SEED) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for frac in COUNTERFEIT_FRACTIONS:
        rows, _ = evaluate_scenario(product, noise, cal, counterfeit_scenario(frac),
                                    f"F3@{frac}", n_trials, seed)
        out[str(frac)] = {r.method: r.detection_rate for r in rows}
    return out


# ═══════════════════════════════════════════════════════════════════
# DRIVER
# ═══════════════════════════════════════════════════════════════════

def run_stress_test(
    n_trials: int = N_TRIALS, n_calib: int = N_CALIB,
    seed: int = SEED, calib_seed: int = CALIB_SEED,
    products: Optional[List[ProductProfile]] = None,
    regimes: Optional[List[str]] = None,
    sweep_regimes: Optional[List[str]] = None,
    verbose: bool = True,
) -> Dict:
    products = products or ALL_PRODUCTS
    regimes = regimes or REGIMES
    sweep_regimes = SWEEP_REGIMES if sweep_regimes is None else sweep_regimes
    start = time.time()

    results: List[StressRow] = []
    calibrations: Dict[str, Dict[str, Dict]] = {}
    ranking: Dict[str, Dict[str, Dict]] = {}
    sweeps: Dict[str, Dict[str, Dict[str, float]]] = {}

    for regime in regimes:
        noise = NOISE_REGIMES[regime]
        calibrations[regime] = {}
        ranking[regime] = {}
        for product in products:
            pname = product.name[:8]
            if verbose:
                print(f"\n{'='*64}\nRegime {regime} | {product.name}\n{'='*64}")
                print(f"  Calibrating on {n_calib} noisy Normal trials (seed {calib_seed})...")
            cal = calibrate(product, noise, n_calib, calib_seed)
            calibrations[regime][pname] = cal.to_dict()
            if verbose:
                print(f"    SL-cal   score<{cal.sl_cal.score:.5f} chain_u>{cal.sl_cal.chain_u:.4f} "
                      f"dim_d>{cal.sl_cal.dim_d:.4f} dim_u>{cal.sl_cal.dim_u:.4f}")
                print(f"    SL-fixed score<{cal.sl_fixed.score:.5f} chain_u>0.30 dim_d>0.02 dim_u>0.50")
                print(f"    B1-tuned max_fr={cal.b1_tuned['max_failure_rate']:.4f} "
                      f"min_sr={cal.b1_tuned['min_scan_rate']:.4f} "
                      f"temp_tol={cal.b1_tuned['temp_tolerance']:.3f}")
                print(f"    B2<{cal.chain_thresholds['B2']:.5f} B3<{cal.chain_thresholds['B3']:.5f} "
                      f"B4<{cal.chain_thresholds['B4']:.5f}")

            scenarios = get_product_scenarios(product)
            scenario_list: List[Tuple[str, Optional[FailureScenario]]] = [("Normal", None)]
            scenario_list += list(scenarios.items())
            raw: Dict[str, Dict[str, Dict[str, List[float]]]] = {m: {} for m in METHODS}
            for sc_name, sc in scenario_list:
                rows, scores = evaluate_scenario(product, noise, cal, sc, sc_name, n_trials, seed)
                results.extend(rows)
                for m in METHODS:
                    raw[m][sc_name] = {"scores": scores[m]}
                if verbose:
                    label = "FPR" if sc_name == "Normal" else "DR "
                    vals = " ".join(
                        f"{r.method}={(r.false_positive_rate if sc_name == 'Normal' else r.detection_rate):.3f}"
                        for r in rows)
                    print(f"  {sc_name:<7} {label} {vals}")

            sp = compute_spearman(raw, SEVERITY_ORDER)
            en = compute_all_entropy(raw)
            ranking[regime][pname] = {
                "spearman": {m: {"rho": sp[m]["rho"], "pvalue": sp[m]["pvalue"],
                                 "constant_output": sp[m]["constant_output"]} for m in METHODS},
                "entropy_bits": {m: en[m]["_pooled"]["entropy_bits"] for m in METHODS},
                "mean_scores": {m: sp[m]["mean_scores"] for m in METHODS},
            }
            if verbose:
                print("  Spearman (global ordering): " + "  ".join(
                    f"{m}={sp[m]['rho']:.3f}(p={sp[m]['pvalue']:.3f})" for m in METHODS))
                print("  Pooled entropy (bits):      " + "  ".join(
                    f"{m}={en[m]['_pooled']['entropy_bits']:.2f}" for m in METHODS))

            if regime in sweep_regimes and product is INSULIN:
                if verbose:
                    print("  Counterfeit sweep (insulin):")
                sw = counterfeit_sweep(product, noise, cal, n_trials, seed)
                sweeps[regime] = sw
                if verbose:
                    for frac, vals in sw.items():
                        print(f"    cf={frac}: " + " ".join(f"{m}={v:.3f}" for m, v in vals.items()))

    return {
        "config": {
            "n_trials": n_trials, "n_calib": n_calib, "seed": seed, "calib_seed": calib_seed,
            "fpr_target": FPR_TARGET, "sl_criteria": SL_CRITERIA,
            "regimes": {r: asdict(NOISE_REGIMES[r]) for r in regimes},
            "methods": METHODS,
        },
        "calibrations": calibrations,
        "results": [asdict(r) for r in results],
        "ranking": ranking,
        "counterfeit_sweep": sweeps,
        "runtime_seconds": time.time() - start,
    }


# ═══════════════════════════════════════════════════════════════════
# EXP-007b: PER-EVIDENCE-TYPE CALIBRATION, EVIDENCE VOLUME, MUTUAL INFORMATION
# ═══════════════════════════════════════════════════════════════════

N_CALIB_B = 20000
N_LEVELS_B = [100, 1000]
PRODUCTS_AT_LARGE_N = [INSULIN]
METHODS_B = ["SL-cal-pt", "SL-cal", "B1-tuned", "B2", "B3", "B4"]
SL_PT_CRITERIA = 2 + 2 * len(EVIDENCE_TYPES)   # chain score, chain u, (d, u) per type = 10
B1_TUNED_RULES_B = 3


@dataclass(frozen=True)
class SLTypeThresholds:
    """Per-evidence-type rule: detect if score < score, or chain_u > chain_u,
    or for any type t, any node has d_t > dim_d[t] or u_t > dim_u[t]."""
    score: float
    chain_u: float
    dim_d: Dict[str, float]
    dim_u: Dict[str, float]


def sl_detect_per_type(feats: SLFeatures, thr: SLTypeThresholds) -> Tuple[bool, List[str]]:
    detected = feats.score < thr.score or feats.chain_u > thr.chain_u
    flagged: List[str] = []
    for node_id, by_type in feats.per_node_type.items():
        node_flag = False
        for t, (d, u) in by_type.items():
            if t not in thr.dim_d:
                continue
            if d > thr.dim_d[t] or u > thr.dim_u[t]:
                node_flag = True
        if node_flag:
            flagged.append(node_id)
            detected = True
    return detected, flagged


def mutual_information(scores_by_scenario: Dict[str, Sequence[float]], n_bins: int = 50) -> float:
    """I(scenario; score) in bits, scenarios weighted by their sample counts
    (equal by design), score discretized into n_bins equal-width bins on [0, 1]."""
    names = list(scores_by_scenario)
    if not names:
        return 0.0
    counts = []
    for s in names:
        h, _ = np.histogram(np.asarray(scores_by_scenario[s], dtype=float), bins=n_bins, range=(0.0, 1.0))
        counts.append(h.astype(float))
    counts = np.array(counts)                      # scenarios x bins
    n_s = counts.sum(axis=1)
    total = counts.sum()
    if total == 0:
        return 0.0
    p_s = n_s / total
    p_bin = counts.sum(axis=0) / total

    def H(p):
        p = p[p > 0]
        return float(-np.sum(p * np.log2(p)))

    h_bin = H(p_bin)
    h_cond = 0.0
    for i in range(len(names)):
        if n_s[i] > 0:
            h_cond += p_s[i] * H(counts[i] / n_s[i])
    return max(0.0, h_bin - h_cond)


@dataclass
class CalibrationB:
    regime: str
    product: str
    n: int
    n_calib: int
    sl_cal_pt: SLTypeThresholds
    sl_cal: SLThresholds
    b1_tuned: Dict[str, float]
    chain_thresholds: Dict[str, float]

    def to_dict(self) -> Dict:
        return {
            "regime": self.regime, "product": self.product, "n": self.n, "n_calib": self.n_calib,
            "sl_cal_pt": asdict(self.sl_cal_pt), "sl_cal": asdict(self.sl_cal),
            "b1_tuned": self.b1_tuned, "chain_thresholds": self.chain_thresholds,
        }


def calibrate_per_type(product: ProductProfile, noise: NoiseProfile, n: int,
                       n_calib: int = N_CALIB_B, seed: int = CALIB_SEED) -> CalibrationB:
    rng = np.random.default_rng(seed)
    scores, chain_us, d_maxes, u_maxes = [], [], [], []
    type_d = {t: [] for t in EVIDENCE_TYPES}
    type_u = {t: [] for t in EVIDENCE_TYPES}
    b2s, b3s, b4s = [], [], []
    frs, srs, excs = [], [], []
    for _ in range(n_calib):
        ev = {nid: generate_noisy_normal_evidence(product, noise, rng, n_packages=n, n_readings=n)
              for nid in EVAL_PATH}
        f = sl_features(evaluate_path(EVAL_PATH, ev, weights=product.weights))
        scores.append(f.score); chain_us.append(f.chain_u)
        d_maxes.append(f.d_max); u_maxes.append(f.u_max)
        for t in EVIDENCE_TYPES:
            td, tu = f.type_max[t]
            type_d[t].append(td); type_u[t].append(tu)
        b2s.append(evaluate_weighted_scoring(EVAL_PATH, ev).chain_score)
        b3s.append(evaluate_bayesian(EVAL_PATH, ev).chain_score)
        b4s.append(evaluate_dempster_shafer(EVAL_PATH, ev).chain_score)
        fr, sr, exc = b1_trial_statistics(ev)
        frs.append(fr); srs.append(sr); excs.append(exc)

    a_pt = FPR_TARGET / SL_PT_CRITERIA        # 0.001 per criterion
    sl_cal_pt = SLTypeThresholds(
        score=pct(scores, 100 * a_pt),
        chain_u=pct(chain_us, 100 * (1 - a_pt)),
        dim_d={t: pct(type_d[t], 100 * (1 - a_pt)) for t in EVIDENCE_TYPES},
        dim_u={t: pct(type_u[t], 100 * (1 - a_pt)) for t in EVIDENCE_TYPES},
    )
    a = FPR_TARGET / SL_CRITERIA               # 0.0025 per criterion (EXP-007 pooled variant)
    sl_cal = SLThresholds(
        score=pct(scores, 100 * a),
        chain_u=pct(chain_us, 100 * (1 - a)),
        dim_d=pct(d_maxes, 100 * (1 - a)),
        dim_u=pct(u_maxes, 100 * (1 - a)),
    )
    a_b1 = FPR_TARGET / SL_PT_CRITERIA         # 0.001 per rule, matching SL-cal-pt's split
    b1_tuned = {
        "max_failure_rate": pct(frs, 100 * (1 - a_b1)),
        "min_scan_rate": pct(srs, 100 * a_b1),
        "temp_tolerance": pct(excs, 100 * (1 - a_b1)),
    }
    chain_thresholds = {
        "B2": pct(b2s, 100 * FPR_TARGET),
        "B3": pct(b3s, 100 * FPR_TARGET),
        "B4": pct(b4s, 100 * FPR_TARGET),
    }
    return CalibrationB(noise.name, product.name[:8], n, n_calib, sl_cal_pt, sl_cal,
                        b1_tuned, chain_thresholds)


@dataclass
class StressRowB(StressRow):
    n: int = 100


def evaluate_scenario_b(
    product: ProductProfile, noise: NoiseProfile, cal: CalibrationB,
    scenario: Optional[FailureScenario], scenario_name: str,
    n_trials: int = N_TRIALS, seed: int = SEED,
) -> Tuple[List[StressRowB], Dict[str, List[float]]]:
    rng = np.random.default_rng(seed)
    is_failure = scenario is not None
    targets = scenario.localization_target_set() if scenario else ()
    n = cal.n

    scores: Dict[str, List[float]] = {m: [] for m in METHODS_B}
    dets: Dict[str, List[bool]] = {m: [] for m in METHODS_B}
    locs: Dict[str, List[List[str]]] = {m: [] for m in METHODS_B}
    sl_u: List[float] = []

    for _ in range(n_trials):
        ev = {nid: generate_noisy_normal_evidence(product, noise, rng, n_packages=n, n_readings=n)
              for nid in EVAL_PATH}
        if scenario is not None:
            ev = apply_failure(ev, scenario, product, rng)

        chain = evaluate_path(EVAL_PATH, ev, weights=product.weights)
        f = sl_features(chain)
        sl_u.append(f.chain_u)
        det, flagged = sl_detect_per_type(f, cal.sl_cal_pt)
        scores["SL-cal-pt"].append(f.score); dets["SL-cal-pt"].append(det); locs["SL-cal-pt"].append(flagged)
        det, flagged = sl_detect(f, cal.sl_cal)
        scores["SL-cal"].append(f.score); dets["SL-cal"].append(det); locs["SL-cal"].append(flagged)

        r = evaluate_threshold(EVAL_PATH, ev, **cal.b1_tuned)
        scores["B1-tuned"].append(r.chain_score); dets["B1-tuned"].append(r.detected)
        locs["B1-tuned"].append([r.weakest_node])

        for name, fn in (("B2", evaluate_weighted_scoring), ("B3", evaluate_bayesian),
                         ("B4", evaluate_dempster_shafer)):
            r = fn(EVAL_PATH, ev)
            scores[name].append(r.chain_score); dets[name].append(r.chain_score < cal.chain_thresholds[name])
            locs[name].append([r.weakest_node])

    rows: List[StressRowB] = []
    for m in METHODS_B:
        n_det = int(sum(dets[m]))
        rate = n_det / n_trials
        la = localization_accuracy_from_trials(dets[m], locs[m], targets) if (is_failure and targets) else 0.0
        rows.append(StressRowB(
            regime=noise.name, product=product.name[:8], method=m, scenario=scenario_name,
            n_trials=n_trials,
            detection_rate=rate if is_failure else 0.0,
            false_positive_rate=rate if not is_failure else 0.0,
            localization_accuracy=la,
            mean_score=float(np.mean(scores[m])), std_score=float(np.std(scores[m])),
            mean_uncertainty=float(np.mean(sl_u)) if m.startswith("SL") else 0.0,
            n_detected=n_det,
            rate_ci=clopper_pearson(n_det, n_trials),
            localization_accuracy_ci=clopper_pearson(int(round(la * n_det)), n_det) if is_failure else (0.0, 0.0),
            mean_score_ci_halfwidth=mean_ci_halfwidth(scores[m]),
            n=n,
        ))
    return rows, scores


def counterfeit_sweep_b(product: ProductProfile, noise: NoiseProfile, cal: CalibrationB,
                        n_trials: int = N_TRIALS, seed: int = SEED) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for frac in COUNTERFEIT_FRACTIONS:
        rows, _ = evaluate_scenario_b(product, noise, cal, counterfeit_scenario(frac),
                                      f"F3@{frac}", n_trials, seed)
        out[str(frac)] = {r.method: r.detection_rate for r in rows}
    return out


def run_stress_test_b(
    n_trials: int = N_TRIALS, n_calib: int = N_CALIB_B,
    seed: int = SEED, calib_seed: int = CALIB_SEED,
    products: Optional[List[ProductProfile]] = None,
    regimes: Optional[List[str]] = None,
    n_levels: Optional[List[int]] = None,
    products_at_large_n: Optional[List[ProductProfile]] = None,
    sweep_regimes: Optional[List[str]] = None,
    verbose: bool = True,
) -> Dict:
    products = products or ALL_PRODUCTS
    regimes = regimes or REGIMES
    n_levels = n_levels or N_LEVELS_B
    products_at_large_n = PRODUCTS_AT_LARGE_N if products_at_large_n is None else products_at_large_n
    sweep_regimes = SWEEP_REGIMES if sweep_regimes is None else sweep_regimes
    start = time.time()

    results: List[StressRowB] = []
    calibrations: Dict[str, Dict] = {}
    ranking: Dict[str, Dict] = {}
    sweeps: Dict[str, Dict] = {}

    for n in n_levels:
        n_key = f"N{n}"
        calibrations[n_key] = {}
        ranking[n_key] = {}
        sweeps[n_key] = {}
        prods = products if n == n_levels[0] else [p for p in products if p in products_at_large_n]
        for regime in regimes:
            noise = NOISE_REGIMES[regime]
            calibrations[n_key][regime] = {}
            ranking[n_key][regime] = {}
            for product in prods:
                pname = product.name[:8]
                if verbose:
                    print(f"\n{'='*64}\nN = {n} | Regime {regime} | {product.name}\n{'='*64}")
                    print(f"  Calibrating on {n_calib} noisy Normal trials (seed {calib_seed})...")
                cal = calibrate_per_type(product, noise, n, n_calib, calib_seed)
                calibrations[n_key][regime][pname] = cal.to_dict()
                if verbose:
                    pt = cal.sl_cal_pt
                    print(f"    SL-cal-pt score<{pt.score:.5f} chain_u>{pt.chain_u:.4f}")
                    print("      d thresholds: " + " ".join(f"{t}>{pt.dim_d[t]:.4f}" for t in EVIDENCE_TYPES))
                    print("      u thresholds: " + " ".join(f"{t}>{pt.dim_u[t]:.4f}" for t in EVIDENCE_TYPES))
                    print(f"    SL-cal    score<{cal.sl_cal.score:.5f} chain_u>{cal.sl_cal.chain_u:.4f} "
                          f"dim_d>{cal.sl_cal.dim_d:.4f} dim_u>{cal.sl_cal.dim_u:.4f}")
                    print(f"    B1-tuned  max_fr={cal.b1_tuned['max_failure_rate']:.4f} "
                          f"min_sr={cal.b1_tuned['min_scan_rate']:.4f} temp_tol={cal.b1_tuned['temp_tolerance']:.3f}")
                    print(f"    B2<{cal.chain_thresholds['B2']:.5f} B3<{cal.chain_thresholds['B3']:.5f} "
                          f"B4<{cal.chain_thresholds['B4']:.5f}")

                scenarios = get_product_scenarios(product)
                scenario_list: List[Tuple[str, Optional[FailureScenario]]] = [("Normal", None)]
                scenario_list += list(scenarios.items())
                raw: Dict[str, Dict[str, Dict[str, List[float]]]] = {m: {} for m in METHODS_B}
                for sc_name, sc in scenario_list:
                    rows, sc_scores = evaluate_scenario_b(product, noise, cal, sc, sc_name, n_trials, seed)
                    results.extend(rows)
                    for m in METHODS_B:
                        raw[m][sc_name] = {"scores": sc_scores[m]}
                    if verbose:
                        label = "FPR" if sc_name == "Normal" else "DR "
                        vals = " ".join(
                            f"{r.method}={(r.false_positive_rate if sc_name == 'Normal' else r.detection_rate):.3f}"
                            for r in rows)
                        print(f"  {sc_name:<7} {label} {vals}")

                sp = compute_spearman(raw, SEVERITY_ORDER)
                mi = {m: mutual_information({sc: raw[m][sc]["scores"] for sc in raw[m]}) for m in METHODS_B}
                ranking[n_key][regime][pname] = {
                    "spearman": {m: {"rho": sp[m]["rho"], "pvalue": sp[m]["pvalue"],
                                     "constant_output": sp[m]["constant_output"]} for m in METHODS_B},
                    "mutual_information_bits": mi,
                    "mean_scores": {m: sp[m]["mean_scores"] for m in METHODS_B},
                }
                if verbose:
                    print("  Spearman (global ordering): " + "  ".join(
                        f"{m}={sp[m]['rho']:.3f}(p={sp[m]['pvalue']:.3f})" for m in METHODS_B))
                    print("  I(scenario; score) bits:    " + "  ".join(
                        f"{m}={mi[m]:.3f}" for m in METHODS_B))

                if regime in sweep_regimes and product is INSULIN:
                    if verbose:
                        print("  Counterfeit sweep (insulin):")
                    sw = counterfeit_sweep_b(product, noise, cal, n_trials, seed)
                    sweeps[n_key][regime] = sw
                    if verbose:
                        for frac, vals in sw.items():
                            print(f"    cf={frac}: " + " ".join(f"{m}={v:.3f}" for m, v in vals.items()))

    return {
        "config": {
            "variant": "b", "n_trials": n_trials, "n_calib": n_calib, "seed": seed,
            "calib_seed": calib_seed, "fpr_target": FPR_TARGET,
            "sl_pt_criteria": SL_PT_CRITERIA, "sl_criteria": SL_CRITERIA,
            "n_levels": n_levels, "products_at_large_n": [p.name[:8] for p in products_at_large_n],
            "regimes": {r: asdict(NOISE_REGIMES[r]) for r in regimes},
            "methods": METHODS_B,
        },
        "calibrations": calibrations,
        "results": [asdict(r) for r in results],
        "ranking": ranking,
        "counterfeit_sweep": sweeps,
        "runtime_seconds": time.time() - start,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="EXP-007 / EXP-007b stress tests")
    parser.add_argument("--variant", choices=["a", "b"], default="a",
                        help="a: EXP-007 (pooled calibration); b: EXP-007b (per-type, evidence volume)")
    args = parser.parse_args()

    out_dir = Path(__file__).resolve().parents[1] / "experiments"
    out_dir.mkdir(exist_ok=True)

    if args.variant == "b":
        print("=" * 64)
        print("Drug Integrity Algebra — Stress Test b (EXP-007b)")
        print(f"  Regimes {REGIMES} | N {N_LEVELS_B} | methods {len(METHODS_B)} | "
              f"calib {N_CALIB_B}@{CALIB_SEED} | eval {N_TRIALS}@{SEED}")
        print("=" * 64)
        out = run_stress_test_b()
        out_path = out_dir / "stress_results_b.json"
    else:
        print("=" * 64)
        print("Drug Integrity Algebra — Operational Stress Test (EXP-007)")
        print(f"  Regimes {REGIMES} | methods {len(METHODS)} | "
              f"calib {N_CALIB}@{CALIB_SEED} | eval {N_TRIALS}@{SEED}")
        print("=" * 64)
        out = run_stress_test()
        out_path = out_dir / "stress_results.json"

    print(f"\n{'='*64}\nCompleted in {out['runtime_seconds']:.1f}s\n{'='*64}")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")
