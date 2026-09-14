"""
Publication-Ready Figure Generation for IEEE HealthCom 2026.

Generates 5 figures from experiment data:
    Fig 1: Detection rate comparison (5 methods × 7 scenarios)
    Fig 2: Severity discrimination (SL graded vs B1 binary vs B4 flat)
    Fig 3: Counterfeit fraction sensitivity sweep
    Fig 4: Ablation — per-dimension detection impact
    Fig 5: Spearman rank correlation with significance

IEEE two-column format:
    Single-column figure: 3.5" (88mm) wide
    Double-column figure: 7.25" (184mm) wide
    Minimum font: 8pt in figures

Usage (from project root):
    python scripts/generate_figures.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")  # non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

# ═══════════════════════════════════════════════════════════════════
# STYLE CONFIG — IEEE-friendly, colorblind-safe
# ═══════════════════════════════════════════════════════════════════

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 9,
    "axes.labelsize": 9,
    "axes.titlesize": 10,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 7.5,
    "figure.dpi": 300,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.05,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.linewidth": 0.5,
})

# Colorblind-safe palette (Wong, 2011)
COLORS = {
    "SL_Algebra":       "#0072B2",  # blue
    "B1_Threshold":     "#D55E00",  # vermillion
    "B2_WeightedScoring": "#009E73",  # green
    "B3_Bayesian":      "#CC79A7",  # pink
    "B4_DempsterShafer": "#E69F00",  # orange
}
LABELS = {
    "SL_Algebra":       "SL Algebra",
    "B1_Threshold":     "B1 Threshold",
    "B2_WeightedScoring": "B2 Weighted",
    "B3_Bayesian":      "B3 Bayesian",
    "B4_DempsterShafer": "B4 Dempster-Shafer",
}

SINGLE_COL = 3.5   # inches
DOUBLE_COL = 7.25  # inches

SCENARIOS = ["F1", "F2", "F3", "F4", "F5", "F6", "F7"]
SCENARIO_LABELS = [
    "F1\nMild\nexcursion",
    "F2\nSevere\nexcursion",
    "F3\nCounterfeit\n(5%)",
    "F4\nScan\nomission",
    "F5\nExpired\nbatch",
    "F6\nMissing\nCoA",
    "F7\nCascading",
]
METHODS = ["SL_Algebra", "B1_Threshold", "B2_WeightedScoring", "B3_Bayesian", "B4_DempsterShafer"]


# ═══════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════

def load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def get_dr(results: list, method: str, scenario: str, product: str = None) -> float:
    """Get detection rate, optionally averaged across products."""
    matches = [r for r in results
               if r["method"] == method and r["scenario"] == scenario]
    if product:
        matches = [r for r in matches if r["product"].strip() == product.strip()]
    if not matches:
        return 0.0
    return np.mean([r["detection_rate"] for r in matches])


def get_score(results: list, method: str, scenario: str, product: str = None) -> float:
    """Get mean score, optionally averaged across products."""
    matches = [r for r in results
               if r["method"] == method and r["scenario"] == scenario]
    if product:
        matches = [r for r in matches if r["product"].strip() == product.strip()]
    if not matches:
        return 0.0
    return np.mean([r["mean_score"] for r in matches])


# ═══════════════════════════════════════════════════════════════════
# FIGURE 1: DETECTION RATE COMPARISON
# ═══════════════════════════════════════════════════════════════════

def fig_detection_rates(results: list, out_dir: Path):
    """Grouped bar chart: DR for 5 methods × 7 scenarios (averaged across products)."""
    fig, ax = plt.subplots(figsize=(DOUBLE_COL, 2.8))

    n_scenarios = len(SCENARIOS)
    n_methods = len(METHODS)
    bar_width = 0.15
    x = np.arange(n_scenarios)

    for i, method in enumerate(METHODS):
        drs = [get_dr(results, method, sc) for sc in SCENARIOS]
        offset = (i - n_methods / 2 + 0.5) * bar_width
        bars = ax.bar(x + offset, drs, bar_width,
                      color=COLORS[method], label=LABELS[method],
                      edgecolor="white", linewidth=0.3)
        # Annotate very low values
        for j, dr in enumerate(drs):
            if 0 < dr < 0.2:
                ax.text(x[j] + offset, dr + 0.02, f"{dr:.2f}",
                        ha="center", va="bottom", fontsize=5.5, rotation=90)

    ax.set_xticks(x)
    ax.set_xticklabels(SCENARIO_LABELS, fontsize=7, linespacing=0.9)
    ax.set_ylabel("Detection Rate")
    ax.set_ylim(0, 1.15)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.axhline(y=1.0, color="gray", linewidth=0.5, linestyle="--", alpha=0.5)
    ax.legend(loc="upper center", ncol=5, bbox_to_anchor=(0.5, 1.18),
              frameon=False, columnspacing=0.8)

    fig.savefig(out_dir / "fig1_detection_rates.pdf")
    fig.savefig(out_dir / "fig1_detection_rates.png")
    plt.close(fig)
    print("  Fig 1: Detection rate comparison")


# ═══════════════════════════════════════════════════════════════════
# FIGURE 2: SEVERITY DISCRIMINATION
# ═══════════════════════════════════════════════════════════════════

def fig_severity_discrimination(results: list, out_dir: Path):
    """Bar chart: integrity scores for Normal/F1/F2 across methods (Insulin)."""
    fig, ax = plt.subplots(figsize=(SINGLE_COL, 2.5))

    scenarios_show = ["Normal", "F1", "F2"]
    scenario_labels = ["Normal\n(safe)", "F1 Mild\n(+2°C, 5%)", "F2 Severe\n(+17°C, 50%)"]
    methods_show = ["SL_Algebra", "B1_Threshold", "B2_WeightedScoring", "B4_DempsterShafer"]
    labels_show = ["SL Algebra", "B1 Threshold", "B2 Weighted", "B4 D-S"]

    n_sc = len(scenarios_show)
    n_m = len(methods_show)
    bar_width = 0.18
    x = np.arange(n_sc)

    for i, method in enumerate(methods_show):
        scores = [get_score(results, method, sc, "Insulin") for sc in scenarios_show]
        offset = (i - n_m / 2 + 0.5) * bar_width
        bars = ax.bar(x + offset, scores, bar_width,
                      color=COLORS[method], label=labels_show[i],
                      edgecolor="white", linewidth=0.3)
        # Annotate scores
        for j, s in enumerate(scores):
            ax.text(x[j] + offset, s + 0.02, f"{s:.2f}",
                    ha="center", va="bottom", fontsize=6, rotation=0)

    ax.set_xticks(x)
    ax.set_xticklabels(scenario_labels, fontsize=8)
    ax.set_ylabel("Integrity Score")
    ax.set_ylim(0, 1.25)
    ax.legend(loc="upper center", ncol=2, bbox_to_anchor=(0.5, 1.22),
              frameon=False, fontsize=7)

    # Annotation arrow for key finding
    ax.annotate("SL correctly\ngrades severity",
                xy=(2 - 0.27, 0.37), xytext=(1.5, 0.55),
                fontsize=6.5, ha="center",
                arrowprops=dict(arrowstyle="->", color="gray", lw=0.8),
                color="#0072B2")

    fig.savefig(out_dir / "fig2_severity_discrimination.pdf")
    fig.savefig(out_dir / "fig2_severity_discrimination.png")
    plt.close(fig)
    print("  Fig 2: Severity discrimination")


# ═══════════════════════════════════════════════════════════════════
# FIGURE 3: COUNTERFEIT FRACTION SENSITIVITY SWEEP
# ═══════════════════════════════════════════════════════════════════

def fig_counterfeit_sweep(metrics: dict, out_dir: Path):
    """Line plot: DR vs counterfeit fraction for SL, B1, B2, B3, B4.

    EXP-006 (v3.1): B1 is included; its fixed failure-rate rule passes
    exactly 1% infiltration and fails 2% and above."""
    fig, ax = plt.subplots(figsize=(SINGLE_COL, 2.5))

    sweep = metrics["sensitivity_insulin"]["counterfeit_fraction_sweep"]
    fractions = sorted(sweep.keys(), key=float)
    x_vals = [float(f) * 100 for f in fractions]  # convert to percentage

    method_map = {"SL": "SL_Algebra", "B1": "B1_Threshold", "B2": "B2_WeightedScoring",
                  "B3": "B3_Bayesian", "B4": "B4_DempsterShafer"}
    markers = {"SL": "o", "B1": "s", "B2": "^", "B3": "v", "B4": "D"}

    for short_name, full_name in method_map.items():
        if not all(short_name in sweep[f] for f in fractions):
            continue
        drs = [sweep[f][short_name] for f in fractions]
        ax.plot(x_vals, drs, markers[short_name] + "-", color=COLORS[full_name],
                label=LABELS[full_name], markersize=4, linewidth=1.5)

    ax.set_xlabel("Counterfeit Infiltration Rate (%)")
    ax.set_ylabel("Detection Rate")
    ax.set_ylim(-0.05, 1.10)
    ax.set_xlim(0, 21)
    ax.set_xticks([1, 2, 3, 5, 10, 15, 20])
    ax.axhline(y=1.0, color="gray", linewidth=0.5, linestyle="--", alpha=0.5)
    ax.legend(loc="center right", frameon=True, framealpha=0.9, fontsize=6.5)

    # Annotate key finding
    ax.annotate("SL: DR=1.0\nat 1% infiltration",
                xy=(1, 1.0), xytext=(5, 0.65),
                fontsize=6.5, ha="center",
                arrowprops=dict(arrowstyle="->", color="#0072B2", lw=0.8),
                color="#0072B2")

    fig.savefig(out_dir / "fig3_counterfeit_sweep.pdf")
    fig.savefig(out_dir / "fig3_counterfeit_sweep.png")
    plt.close(fig)
    print("  Fig 3: Counterfeit fraction sensitivity")


# ═══════════════════════════════════════════════════════════════════
# FIGURE 4: ABLATION — PER-DIMENSION DETECTION IMPACT
# ═══════════════════════════════════════════════════════════════════

def fig_ablation(ablation: dict, out_dir: Path):
    """Bar chart: F4 DR with full model vs chain-only, plus evidence coupling."""
    fig, axes = plt.subplots(1, 2, figsize=(DOUBLE_COL, 2.4),
                             gridspec_kw={"width_ratios": [1, 1.8]})

    # Panel A: Per-dimension detection ablation (A4)
    ax = axes[0]
    products = ["Insulin", "Amoxicil", "mRNA-bas"]
    product_labels = ["Insulin", "Amoxicillin", "mRNA"]
    full_drs, ablated_drs = [], []

    for prod in products:
        full = [r for r in ablation["results"]
                if r["ablation"] == "FULL" and r["scenario"] == "F4"
                and r["product"].strip() == prod]
        abl = [r for r in ablation["results"]
               if r["ablation"] == "A4_Detect" and r["scenario"] == "F4"
               and r["product"].strip() == prod]
        full_drs.append(full[0]["detection_rate"] if full else 0)
        ablated_drs.append(abl[0]["detection_rate"] if abl else 0)

    x = np.arange(len(products))
    w = 0.35
    ax.bar(x - w/2, full_drs, w, color="#0072B2", label="Full model", edgecolor="white")
    ax.bar(x + w/2, ablated_drs, w, color="#D55E00", label="Chain-only", edgecolor="white")

    for i in range(len(products)):
        ax.text(x[i] + w/2, ablated_drs[i] + 0.03, f"{ablated_drs[i]:.2f}",
                ha="center", fontsize=6.5)

    ax.set_xticks(x)
    ax.set_xticklabels(product_labels, fontsize=7.5)
    ax.set_ylabel("F4 Detection Rate")
    ax.set_ylim(0, 1.15)
    ax.set_title("(a) Per-dimension detection", fontsize=8.5, pad=4)
    ax.legend(fontsize=7, loc="upper right")

    # Panel B: Evidence-failure coupling (A1 score separation)
    ax2 = axes[1]
    evidence_types = ["cold_chain", "serialization", "shelf_life", "documentation"]
    ev_labels = ["Cold\nchain", "Serial-\nization", "Shelf\nlife", "Docu-\nmentation"]
    scenarios_check = ["F1", "F2", "F3", "F4", "F5", "F6"]
    sc_short = ["F1", "F2", "F3", "F4", "F5", "F6"]

    # Build coupling matrix: 1 = this evidence type is critical for this scenario
    # (determined by Δ ≈ 0 when dropped)
    coupling = np.zeros((len(evidence_types), len(scenarios_check)))
    for i, etype in enumerate(evidence_types):
        variant = f"drop_{etype}"
        for j, sc in enumerate(scenarios_check):
            # Get Normal and failure scores for this ablation
            normal = [r for r in ablation["results"]
                      if r["ablation"] == "A1_EvType" and r["variant"] == variant
                      and r["scenario"] == "Normal" and r["product"].strip() == "Insulin"]
            failure = [r for r in ablation["results"]
                       if r["ablation"] == "A1_EvType" and r["variant"] == variant
                       and r["scenario"] == sc and r["product"].strip() == "Insulin"]
            if normal and failure:
                delta = abs(normal[0]["mean_score"] - failure[0]["mean_score"])
                # Get full model delta for comparison
                full_n = [r for r in ablation["results"]
                          if r["ablation"] == "FULL" and r["scenario"] == "Normal"
                          and r["product"].strip() == "Insulin"]
                full_f = [r for r in ablation["results"]
                          if r["ablation"] == "FULL" and r["scenario"] == sc
                          and r["product"].strip() == "Insulin"]
                if full_n and full_f:
                    full_delta = abs(full_n[0]["mean_score"] - full_f[0]["mean_score"])
                    # If dropping this type kills discrimination (delta < 10% of full)
                    if full_delta > 0.001 and delta < 0.1 * full_delta:
                        coupling[i, j] = 1.0  # critical coupling
                    else:
                        coupling[i, j] = 0.3  # not critical

    im = ax2.imshow(coupling, cmap="RdYlBu_r", aspect="auto", vmin=0, vmax=1)
    ax2.set_xticks(range(len(sc_short)))
    ax2.set_xticklabels(sc_short, fontsize=7.5)
    ax2.set_yticks(range(len(ev_labels)))
    ax2.set_yticklabels(ev_labels, fontsize=7)
    ax2.set_xlabel("Failure Scenario", fontsize=8)
    ax2.set_title("(b) Evidence–failure coupling", fontsize=8.5, pad=4)

    # Annotate cells
    for i in range(len(evidence_types)):
        for j in range(len(scenarios_check)):
            if coupling[i, j] == 1.0:
                ax2.text(j, i, "●", ha="center", va="center", fontsize=10, color="white")
            elif coupling[i, j] == 0.3:
                ax2.text(j, i, "○", ha="center", va="center", fontsize=8, color="gray")

    plt.tight_layout(w_pad=2.0)
    fig.savefig(out_dir / "fig4_ablation.pdf")
    fig.savefig(out_dir / "fig4_ablation.png")
    plt.close(fig)
    print("  Fig 4: Ablation analysis")


# ═══════════════════════════════════════════════════════════════════
# FIGURE 5: SPEARMAN + ENTROPY SUMMARY
# ═══════════════════════════════════════════════════════════════════

def fig_spearman_entropy(metrics: dict, out_dir: Path):
    """Dual-panel: Spearman ρ with significance + Shannon entropy."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(DOUBLE_COL, 2.3))

    # Use Insulin data (key has trailing space from name[:8])
    insulin_key = [k for k in metrics if k.strip().lower().startswith("insulin")][0]
    spearman = metrics[insulin_key]["spearman"]
    entropy = metrics[insulin_key]["entropy"]

    methods_order = ["SL_Algebra", "B1_Threshold", "B2_WeightedScoring",
                     "B3_Bayesian", "B4_DempsterShafer"]
    short_labels = ["SL\nAlgebra", "B1\nThreshold", "B2\nWeighted", "B3\nBayesian", "B4\nD-S"]

    # Panel A: Spearman ρ. A method with constant output has no rank
    # correlation (EXP-007 audit: B4's earlier 0.31 was rounding noise);
    # it is drawn as an empty slot labelled "undef.".
    rhos = [spearman[m]["rho"] for m in methods_order]
    pvals = [spearman[m]["pvalue"] for m in methods_order]
    colors_bar = [COLORS[m] for m in methods_order]

    def _undefined(m):
        v = spearman[m]
        return bool(v.get("constant_output", False)) or (isinstance(v["rho"], float) and v["rho"] != v["rho"])

    bar_heights = [0.0 if _undefined(m) else r for m, r in zip(methods_order, rhos)]
    bars = ax1.bar(range(len(methods_order)), bar_heights, color=colors_bar,
                   edgecolor="white", linewidth=0.3)
    for i, (m, rho, pval) in enumerate(zip(methods_order, rhos, pvals)):
        if _undefined(m):
            ax1.text(i, 0.03, "undef.\n(constant\noutput)", ha="center", va="bottom", fontsize=6)
            continue
        stars = "***" if pval < 0.001 else "**" if pval < 0.01 else "*" if pval < 0.05 else "n.s."
        ax1.text(i, rho + 0.03, f"{rho:.2f}\n{stars}",
                 ha="center", va="bottom", fontsize=6.5)

    ax1.set_xticks(range(len(methods_order)))
    ax1.set_xticklabels(short_labels, fontsize=7)
    ax1.set_ylabel("Spearman ρ")
    ax1.set_ylim(0, 1.2)
    ax1.set_title("(a) Severity Ranking", fontsize=8.5, pad=4)
    ax1.axhline(y=0, color="gray", linewidth=0.5)

    # Panel B: Shannon entropy
    entropies = [entropy[m]["_pooled"]["entropy_bits"] for m in methods_order]
    # Clamp negative to 0 (floating point artifact)
    entropies = [max(0, e) for e in entropies]

    bars2 = ax2.bar(range(len(methods_order)), entropies, color=colors_bar,
                    edgecolor="white", linewidth=0.3)
    for i, h in enumerate(entropies):
        ax2.text(i, h + 0.08, f"{h:.1f}",
                 ha="center", va="bottom", fontsize=7)

    ax2.set_xticks(range(len(methods_order)))
    ax2.set_xticklabels(short_labels, fontsize=7)
    ax2.set_ylabel("Shannon Entropy (bits)")
    ax2.set_ylim(0, 4.5)
    ax2.set_title("(b) Information Content", fontsize=8.5, pad=4)

    plt.tight_layout(w_pad=2.5)
    fig.savefig(out_dir / "fig5_spearman_entropy.pdf")
    fig.savefig(out_dir / "fig5_spearman_entropy.png")
    plt.close(fig)
    print("  Fig 5: Spearman + Entropy")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    base = Path(__file__).resolve().parents[1]
    exp_dir = base / "experiments"
    fig_dir = base / "figures"
    fig_dir.mkdir(exist_ok=True)

    print("Loading experiment data (v3.1, EXP-006)...")
    main_results = load_json(exp_dir / "full_results_v3_1.json")["results"]
    ablation = load_json(exp_dir / "ablation_results.json")
    metrics = load_json(exp_dir / "metrics_results_v3_1.json")
    # B4's Spearman in the v3.1 file predates the constant-output guard
    # (LOGBOOK addendum item 8); mark it undefined here from its mean scores.
    for pkey, pval in metrics.items():
        if isinstance(pval, dict) and "spearman" in pval:
            for m, v in pval["spearman"].items():
                ms = list(v.get("mean_scores", {}).values())
                if ms and max(ms) - min(ms) < 1e-9:
                    v["constant_output"] = True

    print(f"Generating figures to {fig_dir}/")

    fig_detection_rates(main_results, fig_dir)
    fig_severity_discrimination(main_results, fig_dir)
    fig_counterfeit_sweep(metrics, fig_dir)
    fig_ablation(ablation, fig_dir)
    fig_spearman_entropy(metrics, fig_dir)

    print(f"\nDone. {len(list(fig_dir.glob('*.pdf')))} PDFs + "
          f"{len(list(fig_dir.glob('*.png')))} PNGs generated.")
