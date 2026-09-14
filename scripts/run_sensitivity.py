"""
Parameter Sensitivity of the Formation Functions and the Compromised
Conditional (EXP-008).

One-at-a-time grid around the published defaults, clean regime, N = 100,
main-suite detection rule (per-dimension d > 0.02, u > 0.50, chain u > 0.30)
with the chain threshold recalibrated per setting at the 1st percentile of
that setting's Normal scores (in-sample, exactly as in run_experiments.py).
The default setting therefore reproduces the main-suite numbers and doubles
as a regression check.

Usage (from project root):
    python scripts/run_sensitivity.py
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict, replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jsonld_ex.confidence_algebra import Opinion  # noqa: E402  (same import path as chain_integrity)
from simulation.simulator import (
    ALL_PRODUCTS, INSULIN, ProductProfile, FailureScenario,
    generate_normal_evidence, apply_failure, localization_accuracy_from_trials,
)
from models.chain_integrity import (
    evaluate_path, FormationParams, DEFAULT_FORMATION, COMPROMISED_CONDITIONAL,
)
from run_experiments import (
    EVAL_PATH, clopper_pearson, mean_ci_halfwidth, get_product_scenarios,
    sl_detect_per_dimension,
)
from run_metrics import SEVERITY_ORDER, compute_spearman

N_TRIALS = 1000
SEED = 42
FPR_TARGET = 0.01
SCENARIOS = ["Normal", "F1", "F2", "F3", "F4", "F5", "F6", "F7"]


@dataclass(frozen=True)
class Setting:
    name: str
    group: str
    formation: FormationParams
    conditional: Opinion
    base_prior_weight: float = 2.0


def build_grid() -> List[Setting]:
    """The pre-registered one-at-a-time grid, default first."""
    d = DEFAULT_FORMATION
    grid: List[Setting] = [Setting("default", "default", d, COMPROMISED_CONDITIONAL)]
    for a in (0.001, 0.1, 0.5):
        grid.append(Setting(f"cond_base_rate={a}", "conditional_base_rate", d, Opinion(0.0, 1.0, 0.0, a)))
    for dis in (0.8, 0.9):
        grid.append(Setting(f"cond_disbelief={dis}", "conditional_disbelief", d, Opinion(0.0, dis, 1.0 - dis, 0.01)))
    for cw in (1.0, 2.0, 5.0):
        grid.append(Setting(f"critical_weight={cw:g}", "critical_weight", replace(d, critical_weight=cw), COMPROMISED_CONDITIONAL))
    for s in (5.0, 20.0):
        grid.append(Setting(f"shelf_life_scale={s:g}", "shelf_life_scale", replace(d, shelf_life_scale=s), COMPROMISED_CONDITIONAL))
    for w in (1.0, 4.0):
        grid.append(Setting(f"prior_weight={w:g}", "prior_weight", d, COMPROMISED_CONDITIONAL, base_prior_weight=w))
    grid.append(Setting("base_rates=0.5", "base_rates", replace(
        d, base_rate_serialization=0.5, base_rate_cold_chain=0.5, base_rate_shelf_life=0.5,
        base_rate_documentation=0.5, base_rate_recall=0.5), COMPROMISED_CONDITIONAL))
    for cap in (2.0, 10.0):
        grid.append(Setting(f"severity_cap={cap:g}", "severity_cap", replace(d, severity_cap=cap), COMPROMISED_CONDITIONAL))
    return grid


def setting_to_dict(s: Setting) -> Dict:
    c = s.conditional
    return {
        "name": s.name, "group": s.group, "formation": asdict(s.formation),
        "conditional": {"b": c.belief, "d": c.disbelief, "u": c.uncertainty, "a": c.base_rate},
        "base_prior_weight": s.base_prior_weight,
    }


@dataclass
class SensRow:
    setting: str
    product: str
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
    mean_score_ci_halfwidth: float
    chain_threshold: float


def evaluate_setting(setting: Setting, product: ProductProfile, n_trials: int = N_TRIALS,
                     seed: int = SEED) -> Tuple[List[SensRow], Dict[str, List[float]]]:
    """Main-suite protocol for one setting and product."""
    def chain_of(ev):
        return evaluate_path(EVAL_PATH, ev, weights=product.weights,
                             base_prior_weight=setting.base_prior_weight,
                             compromised_conditional=setting.conditional,
                             formation=setting.formation)

    scenarios: Dict[str, Optional[FailureScenario]] = {"Normal": None}
    scenarios.update(get_product_scenarios(product))

    # Calibration: same evidence stream as the Normal run (seed), 1st percentile
    rng = np.random.default_rng(seed)
    normal_scores = []
    for _ in range(n_trials):
        ev = {nid: generate_normal_evidence(product, rng=rng) for nid in EVAL_PATH}
        normal_scores.append(chain_of(ev).integrity_score)
    thr = float(np.percentile(normal_scores, 100 * FPR_TARGET))

    rows: List[SensRow] = []
    scores: Dict[str, List[float]] = {}
    for sc_name, sc in scenarios.items():
        is_failure = sc is not None
        targets = sc.localization_target_set() if sc else ()
        rng = np.random.default_rng(seed)
        sc_scores, sc_u, dets, flags = [], [], [], []
        for _ in range(n_trials):
            ev = {nid: generate_normal_evidence(product, rng=rng) for nid in EVAL_PATH}
            if sc is not None:
                ev = apply_failure(ev, sc, product, rng)
            chain = chain_of(ev)
            det, flagged = sl_detect_per_dimension(chain, thr)
            sc_scores.append(chain.integrity_score); sc_u.append(chain.uncertainty)
            dets.append(det); flags.append(flagged)
        n_det = int(sum(dets))
        rate = n_det / n_trials
        la = localization_accuracy_from_trials(dets, flags, targets) if (is_failure and targets) else 0.0
        scores[sc_name] = sc_scores
        rows.append(SensRow(
            setting=setting.name, product=product.name[:8], scenario=sc_name, n_trials=n_trials,
            detection_rate=rate if is_failure else 0.0,
            false_positive_rate=rate if not is_failure else 0.0,
            localization_accuracy=la,
            mean_score=float(np.mean(sc_scores)), std_score=float(np.std(sc_scores)),
            mean_uncertainty=float(np.mean(sc_u)), n_detected=n_det,
            rate_ci=clopper_pearson(n_det, n_trials),
            mean_score_ci_halfwidth=mean_ci_halfwidth(sc_scores),
            chain_threshold=thr,
        ))
    return rows, scores


def summarize(rows: List[SensRow], scores: Dict[str, List[float]]) -> Dict:
    by_sc = {r.scenario: r for r in rows}
    raw = {"SL": {sc: {"scores": v} for sc, v in scores.items()}}
    sp = compute_spearman(raw, SEVERITY_ORDER)["SL"]
    failures = [by_sc[s] for s in SCENARIOS[1:]]
    return {
        "fpr": by_sc["Normal"].false_positive_rate,
        "min_dr": min(r.detection_rate for r in failures),
        "min_dr_scenario": min(failures, key=lambda r: r.detection_rate).scenario,
        "min_la": min(r.localization_accuracy for r in failures),
        "rho": sp["rho"], "pvalue": sp["pvalue"],
        "mean_scores": {s: by_sc[s].mean_score for s in SCENARIOS},
        "severity_gap_F1_F2": by_sc["F1"].mean_score - by_sc["F2"].mean_score,
        "chain_threshold": by_sc["Normal"].chain_threshold,
    }


def run_sensitivity(n_trials: int = N_TRIALS, seed: int = SEED,
                    products: Optional[List[ProductProfile]] = None,
                    grid: Optional[List[Setting]] = None, verbose: bool = True) -> Dict:
    products = products or ALL_PRODUCTS
    grid = grid or build_grid()
    start = time.time()
    all_rows: List[SensRow] = []
    summary: Dict[str, Dict[str, Dict]] = {}
    for setting in grid:
        summary[setting.name] = {}
        if verbose:
            print(f"\n{'='*64}\nSetting: {setting.name}\n{'='*64}")
        for product in products:
            rows, scores = evaluate_setting(setting, product, n_trials, seed)
            all_rows.extend(rows)
            s = summarize(rows, scores)
            summary[setting.name][product.name[:8]] = s
            if verbose:
                ms = s["mean_scores"]
                print(f"  {product.name[:8].strip():9s} FPR={s['fpr']:.3f} minDR={s['min_dr']:.3f} ({s['min_dr_scenario']}) "
                      f"minLA={s['min_la']:.3f} rho={s['rho']:.3f} (p={s['pvalue']:.1e}) "
                      f"P(F1)={ms['F1']:.3f} P(F2)={ms['F2']:.3f} gap={s['severity_gap_F1_F2']:.3f} "
                      f"P(F3)={ms['F3']:.3f} P(F5)={ms['F5']:.3f} P(F6)={ms['F6']:.3f} thr={s['chain_threshold']:.5f}")
    return {
        "config": {"n_trials": n_trials, "seed": seed, "fpr_target": FPR_TARGET,
                   "grid": [setting_to_dict(s) for s in grid]},
        "rows": [asdict(r) for r in all_rows],
        "summary": summary,
        "runtime_seconds": time.time() - start,
    }


if __name__ == "__main__":
    print("=" * 64)
    print("Drug Integrity Algebra — Parameter Sensitivity (EXP-008)")
    print(f"  {len(build_grid())} settings | trials {N_TRIALS}@{SEED} | clean regime, N = 100")
    print("=" * 64)
    out = run_sensitivity()
    print(f"\n{'='*64}\nCompleted in {out['runtime_seconds']:.1f}s\n{'='*64}")
    out_dir = Path(__file__).resolve().parents[1] / "experiments"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "sensitivity_results.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")
