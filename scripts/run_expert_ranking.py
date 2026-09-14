"""
Expert ranking analysis (EXP-012).

Reads the rankers' response sheets (experiments/exp012_responses/*.csv),
maps the neutral labels back to scenario codes with docs/exp012/scenario_key.json,
and computes, per product:

  - Kendall's W (tie-corrected) across rankers, with the chi-square
    approximation for its p-value
  - the expert median ranking (ties broken by mean rank)
  - Spearman rho between the expert median ranking and each pre-registered
    ordering (global; CRT for amoxicillin)
  - Spearman rho between each method's clean-regime mean scores
    (experiments/full_results_v3_1.json) and the expert median ranking
  - Spearman rho between each individual ranker and the SL mean scores

Usage (from project root, after responses are saved):
    python scripts/run_expert_ranking.py
"""

from __future__ import annotations

import csv
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import stats as sp_stats

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_metrics import SEVERITY_ORDER, SEVERITY_ORDER_CRT

ROOT = Path(__file__).resolve().parents[1]
KEY_PATH = ROOT / "docs" / "exp012" / "scenario_key.json"
RESPONSES_DIR = ROOT / "experiments" / "exp012_responses"
MAIN_RESULTS = ROOT / "experiments" / "full_results_v3_1.json"
SCENARIOS = ["Normal", "F1", "F2", "F3", "F4", "F5", "F6", "F7"]
METHODS = ["SL_Algebra", "B1_Threshold", "B2_WeightedScoring", "B3_Bayesian", "B4_DempsterShafer"]
PRODUCT_ALIASES = {"insulin": "Insulin", "amoxicillin": "Amoxicil", "amoxicil": "Amoxicil",
                   "mrna": "mRNA-bas", "mrna-bas": "mRNA-bas"}


# ═══════════════════════════════════════════════════════════════════
# STATISTICS
# ═══════════════════════════════════════════════════════════════════

def kendalls_w(rank_matrix: Sequence[Sequence[float]]) -> Tuple[float, float]:
    """Kendall's coefficient of concordance with tie correction.

    rank_matrix: m rankers x n items. Returns (W, approximate p-value from
    chi2 = m (n - 1) W with n - 1 degrees of freedom).
    """
    R = np.asarray(rank_matrix, dtype=float)
    m, n = R.shape
    if m < 2 or n < 2:
        return float("nan"), float("nan")
    rank_sums = R.sum(axis=0)
    S = float(np.sum((rank_sums - rank_sums.mean()) ** 2))
    T = 0.0
    for row in R:
        _, counts = np.unique(row, return_counts=True)
        T += float(np.sum(counts ** 3 - counts))
    denom = m * m * (n ** 3 - n) - m * T
    if denom <= 0:
        return float("nan"), float("nan")
    W = 12.0 * S / denom
    chi2 = m * (n - 1) * W
    p = float(sp_stats.chi2.sf(chi2, n - 1))
    return float(W), p


def median_ranking(rank_matrix: Sequence[Sequence[float]]) -> List[float]:
    """Median rank per item over rankers; ties in the median are broken by
    the mean rank; the result is re-ranked 1..n (average ranks for ties)."""
    R = np.asarray(rank_matrix, dtype=float)
    med = np.median(R, axis=0)
    mean = R.mean(axis=0)
    keys = med + 1e-6 * mean
    return [float(r) for r in sp_stats.rankdata(keys, method="average")]


def spearman(x: Sequence[float], y: Sequence[float]) -> Dict[str, float]:
    x = np.asarray(x, dtype=float); y = np.asarray(y, dtype=float)
    if np.ptp(x) < 1e-9 or np.ptp(y) < 1e-9:
        return {"rho": float("nan"), "pvalue": float("nan"), "constant_output": True}
    rho, p = sp_stats.spearmanr(x, y)
    return {"rho": float(rho), "pvalue": float(p), "constant_output": False}


# ═══════════════════════════════════════════════════════════════════
# INPUT
# ═══════════════════════════════════════════════════════════════════

def load_key(path: Path = KEY_PATH) -> Dict:
    with open(path) as f:
        return json.load(f)


def canonical_product(name: str) -> Optional[str]:
    k = name.strip().lower()
    for alias, canon in PRODUCT_ALIASES.items():
        if k.startswith(alias):
            return canon
    return None


def load_responses(responses_dir: Path = RESPONSES_DIR, key: Optional[Dict] = None) -> Tuple[Dict, Dict]:
    """Returns ({product: {ranker: {scenario: rank}}}, {ranker: {role, years}})."""
    key = key or load_key()
    ranks: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    meta: Dict[str, Dict[str, str]] = {}
    files = sorted(Path(responses_dir).glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"no response files in {responses_dir}")
    for path in files:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                rid = (row.get("ranker_id") or path.stem).strip()
                product = canonical_product(row.get("product", ""))
                label = (row.get("label") or "").strip().upper()
                rank_txt = (row.get("rank") or "").strip()
                if product is None or not label or not rank_txt:
                    continue
                scenario = key["products"][product][label]
                ranks[product][rid][scenario] = float(rank_txt)
                meta.setdefault(rid, {"role": (row.get("role") or "").strip(),
                                      "years_experience": (row.get("years_experience") or "").strip()})
                if row.get("role"):
                    meta[rid]["role"] = row["role"].strip()
                if row.get("years_experience"):
                    meta[rid]["years_experience"] = row["years_experience"].strip()
    return {p: dict(v) for p, v in ranks.items()}, meta


def load_method_mean_scores(path: Path = MAIN_RESULTS) -> Dict[str, Dict[str, Dict[str, float]]]:
    """{product: {method: {scenario: mean_score}}} from the v3.1 main suite."""
    with open(path) as f:
        rows = json.load(f)["results"]
    out: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        out[r["product"].strip()][r["method"]][r["scenario"]] = r["mean_score"]
    return {p: dict(v) for p, v in out.items()}


# ═══════════════════════════════════════════════════════════════════
# ANALYSIS
# ═══════════════════════════════════════════════════════════════════

def analyse_product(product: str, ranker_ranks: Dict[str, Dict[str, float]],
                    method_scores: Optional[Dict[str, Dict[str, float]]]) -> Dict:
    rankers = sorted(ranker_ranks)
    matrix = [[ranker_ranks[r][s] for s in SCENARIOS] for r in rankers]
    W, p_w = kendalls_w(matrix)
    med = median_ranking(matrix)
    expert = dict(zip(SCENARIOS, med))
    orderings = {"global": SEVERITY_ORDER}
    if product == "Amoxicil":
        orderings["crt"] = SEVERITY_ORDER_CRT
    vs_orderings = {name: spearman(med, [o[s] for s in SCENARIOS]) for name, o in orderings.items()}
    vs_methods = {}
    per_ranker_vs_sl = {}
    if method_scores:
        for m in METHODS:
            if m in method_scores:
                scores = [-method_scores[m][s] for s in SCENARIOS]
                vs_methods[m] = spearman(med, scores)
        if "SL_Algebra" in method_scores:
            sl = [-method_scores["SL_Algebra"][s] for s in SCENARIOS]
            for r, row in zip(rankers, matrix):
                per_ranker_vs_sl[r] = spearman(row, sl)
    return {
        "rankers": rankers,
        "n_rankers": len(rankers),
        "rank_matrix": {r: dict(zip(SCENARIOS, row)) for r, row in zip(rankers, matrix)},
        "kendalls_w": W, "kendalls_w_pvalue_chi2": p_w,
        "expert_median_ranking": expert,
        "expert_order_least_to_greatest": [s for s, _ in sorted(expert.items(), key=lambda kv: kv[1])],
        "vs_orderings": vs_orderings,
        "vs_methods": vs_methods,
        "per_ranker_vs_sl": per_ranker_vs_sl,
    }


def run_expert_ranking(responses_dir: Path = RESPONSES_DIR, key_path: Path = KEY_PATH,
                       main_results: Optional[Path] = MAIN_RESULTS, verbose: bool = True) -> Dict:
    start = time.time()
    key = load_key(key_path)
    ranks, meta = load_responses(responses_dir, key)
    method_scores = load_method_mean_scores(main_results) if main_results and Path(main_results).exists() else {}
    out: Dict = {"rankers": meta, "products": {}}
    for product in ["Insulin", "Amoxicil", "mRNA-bas"]:
        if product not in ranks:
            continue
        res = analyse_product(product, ranks[product], method_scores.get(product))
        out["products"][product] = res
        if verbose:
            print(f"\n{'='*64}\n{key['instrument_product_names'][product]}\n{'='*64}")
            print(f"  rankers: {res['n_rankers']}  Kendall's W = {res['kendalls_w']:.3f} "
                  f"(chi2 p = {res['kendalls_w_pvalue_chi2']:.3f})")
            print("  expert median order (least -> greatest concern): " + " < ".join(res["expert_order_least_to_greatest"]))
            for name, v in res["vs_orderings"].items():
                print(f"  vs {name} ordering: rho = {v['rho']:.3f} (p = {v['pvalue']:.3f})")
            for m, v in res["vs_methods"].items():
                tag = "undefined (constant output)" if v["constant_output"] else f"rho = {v['rho']:.3f} (p = {v['pvalue']:.3f})"
                print(f"  {m:<20} vs experts: {tag}")
            for r, v in res["per_ranker_vs_sl"].items():
                print(f"  {r} vs SL: rho = {v['rho']:.3f} (p = {v['pvalue']:.3f})")
    out["runtime_seconds"] = time.time() - start
    return out


if __name__ == "__main__":
    print("=" * 64)
    print("Drug Integrity Algebra — Expert ranking analysis (EXP-012)")
    print("=" * 64)
    result = run_expert_ranking()
    out_path = ROOT / "experiments" / "expert_ranking_results.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")
