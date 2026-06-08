"""
Baseline Methods for Comparison (B1–B3).

Implements three baseline approaches to drug integrity assessment:
    B1 — Rule-Based Threshold (industry standard, Tatmeen-like)
    B2 — Weighted Scoring (common supply chain risk management)
    B3 — Bayesian Probabilistic (graphical model, no SL decomposition)

Each baseline takes the same NodeEvidence inputs and produces a chain-level
score and detection decision, enabling direct comparison against the
drug integrity algebra.

Design: all baselines implement a common interface:
    evaluate_baseline(path, node_evidence, product) → BaselineResult
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from models.drug_integrity_opinion import (
    ColdChainEvidence,
    DocumentationEvidence,
    NodeEvidence,
    RecallStatus,
    SerializationEvidence,
    ShelfLifeEvidence,
    DEFAULT_CRITICAL_DOCUMENTS,
)


# ═══════════════════════════════════════════════════════════════════
# COMMON RESULT TYPE
# ═══════════════════════════════════════════════════════════════════


@dataclass
class BaselineResult:
    """Result from any baseline method."""
    method: str
    path: List[str]
    chain_score: float           # overall integrity score [0, 1]
    node_scores: Dict[str, float]  # per-node scores
    detected: bool               # was failure flagged?
    weakest_node: str            # node with lowest score
    detection_threshold: float   # threshold used for detection


# ═══════════════════════════════════════════════════════════════════
# B1 — RULE-BASED THRESHOLD (Industry Standard)
# ═══════════════════════════════════════════════════════════════════


def _threshold_serialization(ev: Optional[SerializationEvidence],
                              min_scan_rate: float = 0.90,
                              max_failure_rate: float = 0.01) -> bool:
    """Pass if scan rate >= min AND failure rate <= max."""
    if ev is None:
        return True  # no data = no flag (threshold systems can't flag absence)
    total_scanned = ev.scanned_success + ev.scanned_failure
    if total_scanned == 0:
        return True  # no scans = no flag
    scan_rate = total_scanned / ev.total_packages
    failure_rate = ev.scanned_failure / total_scanned
    return scan_rate >= min_scan_rate and failure_rate <= max_failure_rate


def _threshold_cold_chain(ev: Optional[ColdChainEvidence],
                           tolerance: float = 0.0) -> bool:
    """Pass if ALL readings are within [min - tolerance, max + tolerance]."""
    if ev is None:
        return True
    for temp in ev.readings:
        if temp < ev.required_min - tolerance or temp > ev.required_max + tolerance:
            return False
    return True


def _threshold_shelf_life(ev: Optional[ShelfLifeEvidence]) -> bool:
    """Pass if remaining days >= regulatory minimum."""
    if ev is None:
        return True
    return ev.remaining_days >= ev.regulatory_min


def _threshold_documentation(ev: Optional[DocumentationEvidence]) -> bool:
    """Pass if all critical documents are present."""
    if ev is None:
        return True
    return ev.documents_critical.issubset(ev.documents_present)


def _threshold_recall(recall: RecallStatus) -> bool:
    """Pass if no active recall."""
    return recall != RecallStatus.ACTIVE_RECALL


def evaluate_threshold(
    path: List[str],
    node_evidence: Dict[str, NodeEvidence],
    detection_threshold: float = 0.5,  # unused for binary, kept for interface
) -> BaselineResult:
    """B1: Rule-based threshold — binary pass/fail per node, AND across chain.

    This is what Tatmeen effectively does today: each evidence type is
    independently checked against a threshold. If ANY check fails at ANY
    node, the chain fails. No continuous scores, no uncertainty, no
    proportional response.

    Key limitations (what the paper demonstrates):
    - A temperature of 7.9°C and 2.1°C both "pass" on a 2–8°C range
    - Scanning 10% and 100% of packages both "pass" if scanned items are clean
    - No information about HOW close to failure any check is
    - Missing data = silent pass (no flag for absence of evidence)
    """
    node_scores: Dict[str, float] = {}

    for node_id in path:
        ev = node_evidence.get(node_id, NodeEvidence())
        checks = [
            _threshold_serialization(ev.serialization),
            _threshold_cold_chain(ev.cold_chain),
            _threshold_shelf_life(ev.shelf_life),
            _threshold_documentation(ev.documentation),
            _threshold_recall(ev.recall),
        ]
        node_pass = all(checks)
        node_scores[node_id] = 1.0 if node_pass else 0.0

    chain_score = 1.0 if all(s == 1.0 for s in node_scores.values()) else 0.0
    weakest = min(path, key=lambda n: node_scores[n])

    return BaselineResult(
        method="B1_Threshold",
        path=path,
        chain_score=chain_score,
        node_scores=node_scores,
        detected=(chain_score == 0.0),
        weakest_node=weakest,
        detection_threshold=detection_threshold,
    )


# ═══════════════════════════════════════════════════════════════════
# B2 — WEIGHTED SCORING (Risk Management Standard)
# ═══════════════════════════════════════════════════════════════════


def _score_serialization(ev: Optional[SerializationEvidence]) -> float:
    """Continuous score: scan_rate × (1 - failure_rate)."""
    if ev is None:
        return 1.0  # no data = assume good (common in risk scoring)
    total_scanned = ev.scanned_success + ev.scanned_failure
    if total_scanned == 0:
        return 1.0
    scan_rate = total_scanned / ev.total_packages
    failure_rate = ev.scanned_failure / total_scanned
    return scan_rate * (1.0 - failure_rate)


def _score_cold_chain(ev: Optional[ColdChainEvidence]) -> float:
    """Continuous score: fraction of readings in range."""
    if ev is None:
        return 1.0
    if len(ev.readings) == 0:
        return 1.0
    in_range = sum(
        1 for t in ev.readings
        if ev.required_min <= t <= ev.required_max
    )
    return in_range / len(ev.readings)


def _score_shelf_life(ev: Optional[ShelfLifeEvidence]) -> float:
    """Continuous score: ratio of remaining to minimum, capped at 1."""
    if ev is None:
        return 1.0
    return min(1.0, ev.remaining_days / (2.0 * ev.regulatory_min))


def _score_documentation(ev: Optional[DocumentationEvidence]) -> float:
    """Continuous score: fraction of required documents present."""
    if ev is None:
        return 1.0
    if len(ev.documents_required) == 0:
        return 1.0
    return len(ev.documents_present & ev.documents_required) / len(ev.documents_required)


def _score_recall(recall: RecallStatus) -> float:
    """Binary: 0 if recalled, 1 otherwise."""
    return 0.0 if recall == RecallStatus.ACTIVE_RECALL else 1.0


def evaluate_weighted_scoring(
    path: List[str],
    node_evidence: Dict[str, NodeEvidence],
    weights: Tuple[float, ...] = (0.25, 0.25, 0.20, 0.20, 0.10),
    detection_threshold: float = 0.5,
) -> BaselineResult:
    """B2: Weighted scoring — continuous scores, weighted average, min across chain.

    Each evidence type produces a continuous score in [0, 1]. Per-node
    score is the weighted average. Chain score is the minimum node score
    (weakest-link model).

    Key limitations (what the paper demonstrates):
    - No uncertainty decomposition: a score of 0.5 could mean "moderate
      evidence of compromise" or "we scanned half the packages and everything
      looked fine" — fundamentally different situations
    - Missing data → score 1.0 (assumed good), inflating chain scores
    - Severity not captured: all out-of-range readings weighted equally
    """
    w_ser, w_cc, w_sl, w_doc, w_rec = weights
    node_scores: Dict[str, float] = {}

    for node_id in path:
        ev = node_evidence.get(node_id, NodeEvidence())
        scores = [
            w_ser * _score_serialization(ev.serialization),
            w_cc * _score_cold_chain(ev.cold_chain),
            w_sl * _score_shelf_life(ev.shelf_life),
            w_doc * _score_documentation(ev.documentation),
            w_rec * _score_recall(ev.recall),
        ]
        node_scores[node_id] = sum(scores)

    chain_score = min(node_scores.values()) if node_scores else 0.0
    weakest = min(path, key=lambda n: node_scores[n])

    return BaselineResult(
        method="B2_WeightedScoring",
        path=path,
        chain_score=chain_score,
        node_scores=node_scores,
        detected=(chain_score < detection_threshold),
        weakest_node=weakest,
        detection_threshold=detection_threshold,
    )


# ═══════════════════════════════════════════════════════════════════
# B3 — BAYESIAN PROBABILISTIC (No SL Decomposition)
# ═══════════════════════════════════════════════════════════════════


def _bayesian_serialization(ev: Optional[SerializationEvidence]) -> float:
    """Posterior probability of integrity given serialization evidence.

    Uses Beta-Binomial conjugate model:
        P(safe|data) = (successes + α) / (total_scanned + α + β)
    with uniform prior α = β = 1.
    """
    if ev is None:
        return 0.5  # uninformative prior
    total_scanned = ev.scanned_success + ev.scanned_failure
    if total_scanned == 0:
        return 0.5
    # Beta-binomial posterior
    alpha_post = ev.scanned_success + 1
    beta_post = ev.scanned_failure + 1
    # Posterior mean, adjusted for coverage
    posterior = alpha_post / (alpha_post + beta_post)
    coverage = total_scanned / ev.total_packages
    # Blend posterior with prior based on coverage
    return coverage * posterior + (1.0 - coverage) * 0.5


def _bayesian_cold_chain(ev: Optional[ColdChainEvidence]) -> float:
    """Posterior probability given cold chain evidence.

    Uses fraction-in-range as a Beta observation, with coverage adjustment.
    """
    if ev is None:
        return 0.5
    if len(ev.readings) == 0:
        return 0.5
    in_range = sum(
        1 for t in ev.readings
        if ev.required_min <= t <= ev.required_max
    )
    out_range = len(ev.readings) - in_range
    alpha_post = in_range + 1
    beta_post = out_range + 1
    posterior = alpha_post / (alpha_post + beta_post)
    coverage = len(ev.readings) / ev.expected_readings
    return coverage * posterior + (1.0 - coverage) * 0.5


def _bayesian_shelf_life(ev: Optional[ShelfLifeEvidence]) -> float:
    """Logistic probability based on ratio above/below threshold."""
    if ev is None:
        return 0.5
    ratio = ev.remaining_days / ev.regulatory_min
    # Logistic: steep transition around ratio = 1.0
    k = 5.0  # steepness
    return 1.0 / (1.0 + math.exp(-k * (ratio - 1.0)))


def _bayesian_documentation(ev: Optional[DocumentationEvidence]) -> float:
    """Probability based on document completeness."""
    if ev is None:
        return 0.5
    if len(ev.documents_required) == 0:
        return 0.5
    completeness = len(ev.documents_present & ev.documents_required) / len(ev.documents_required)
    # Weight critical documents more heavily
    if ev.documents_critical:
        crit_complete = len(ev.documents_present & ev.documents_critical) / len(ev.documents_critical)
        return 0.6 * crit_complete + 0.4 * completeness
    return completeness


def _bayesian_recall(recall: RecallStatus) -> float:
    """Binary posterior for recall."""
    return 0.001 if recall == RecallStatus.ACTIVE_RECALL else 0.999


def evaluate_bayesian(
    path: List[str],
    node_evidence: Dict[str, NodeEvidence],
    detection_threshold: float = 0.5,
) -> BaselineResult:
    """B3: Bayesian probabilistic — conjugate posteriors, product across chain.

    Each evidence type produces a posterior probability P(safe|evidence).
    Per-node probability is the product of evidence probabilities
    (independence assumption). Chain probability is the product across nodes
    (serial conjunction).

    Key limitations (what the paper demonstrates):
    - Single probability conflates belief and uncertainty: P=0.5 could mean
      "strong conflicting evidence" or "no evidence at all"
    - No explicit uncertainty component — cannot distinguish ignorance from conflict
    - Missing data → 0.5 (uninformative), which multiplicatively degrades
      chain score even without negative evidence
    """
    node_scores: Dict[str, float] = {}

    for node_id in path:
        ev = node_evidence.get(node_id, NodeEvidence())
        probs = [
            _bayesian_serialization(ev.serialization),
            _bayesian_cold_chain(ev.cold_chain),
            _bayesian_shelf_life(ev.shelf_life),
            _bayesian_documentation(ev.documentation),
            _bayesian_recall(ev.recall),
        ]
        # Node probability: product of evidence probabilities (independence)
        node_prob = 1.0
        for p in probs:
            node_prob *= p
        node_scores[node_id] = node_prob

    # Chain probability: product across nodes (serial conjunction)
    chain_score = 1.0
    for s in node_scores.values():
        chain_score *= s
    weakest = min(path, key=lambda n: node_scores[n])

    return BaselineResult(
        method="B3_Bayesian",
        path=path,
        chain_score=chain_score,
        node_scores=node_scores,
        detected=(chain_score < detection_threshold),
        weakest_node=weakest,
        detection_threshold=detection_threshold,
    )


# ═══════════════════════════════════════════════════════════════════
# UNIFIED EVALUATOR
# ═══════════════════════════════════════════════════════════════════


BASELINE_METHODS = {
    "B1_Threshold": evaluate_threshold,
    "B2_WeightedScoring": evaluate_weighted_scoring,
    "B3_Bayesian": evaluate_bayesian,
}


# ═════════════════════════════════════════════════════════════════
# B4 — DEMPSTER-SHAFER EVIDENCE THEORY
# ═════════════════════════════════════════════════════════════════
#
# Frame of discernment: Θ = {safe, compromised}
# Mass function: m(safe), m(compromised), m(Θ)
# Dempster’s rule of combination for fusing evidence sources.
#
# Key differences from SL algebra:
#   - No trust discounting or deduce operator (no cross-node propagation)
#   - Subject to Zadeh’s paradox under high conflict
#   - No base rate (pignistic probability uses uniform prior)
#   - Cross-node: Dempster’s rule treats each node as independent evidence


@dataclass
class MassFunction:
    """D-S mass function over binary frame {safe, compromised}."""
    m_safe: float       # m({safe})
    m_compromised: float  # m({compromised})
    m_theta: float      # m(Θ) = uncertainty mass

    def __post_init__(self):
        total = self.m_safe + self.m_compromised + self.m_theta
        assert abs(total - 1.0) < 1e-9, f"Mass must sum to 1.0, got {total}"

    def bel_safe(self) -> float:
        """Belief in safe = m(safe)."""
        return self.m_safe

    def plaus_safe(self) -> float:
        """Plausibility of safe = 1 - m(compromised)."""
        return 1.0 - self.m_compromised

    def pignistic_safe(self) -> float:
        """Pignistic probability: BetP(safe) = m(safe) + m(Θ)/2."""
        return self.m_safe + self.m_theta / 2.0

    @staticmethod
    def vacuous() -> "MassFunction":
        """Complete ignorance: all mass on Θ."""
        return MassFunction(0.0, 0.0, 1.0)


def dempster_combine(m1: MassFunction, m2: MassFunction) -> MassFunction:
    """Dempster’s rule of combination for binary frame.

    Combines two independent evidence sources. Subject to Zadeh’s paradox
    when conflict K approaches 1.0.
    """
    # All pairwise intersections
    # safe ∩ safe = safe
    # safe ∩ compromised = ∅ (conflict)
    # safe ∩ Θ = safe
    # compromised ∩ compromised = compromised
    # compromised ∩ safe = ∅ (conflict)
    # compromised ∩ Θ = compromised
    # Θ ∩ safe = safe
    # Θ ∩ compromised = compromised
    # Θ ∩ Θ = Θ

    conflict = (m1.m_safe * m2.m_compromised +
                m1.m_compromised * m2.m_safe)

    if conflict >= 1.0 - 1e-12:
        # Total conflict — Zadeh’s paradox. Return vacuous.
        return MassFunction.vacuous()

    norm = 1.0 / (1.0 - conflict)

    m_safe = norm * (m1.m_safe * m2.m_safe +
                     m1.m_safe * m2.m_theta +
                     m1.m_theta * m2.m_safe)

    m_comp = norm * (m1.m_compromised * m2.m_compromised +
                     m1.m_compromised * m2.m_theta +
                     m1.m_theta * m2.m_compromised)

    m_theta = norm * (m1.m_theta * m2.m_theta)

    return MassFunction(m_safe, m_comp, m_theta)


def dempster_combine_multi(*masses: MassFunction) -> MassFunction:
    """Combine N mass functions via iterated Dempster’s rule."""
    if len(masses) == 0:
        return MassFunction.vacuous()
    result = masses[0]
    for m in masses[1:]:
        result = dempster_combine(result, m)
    return result


# ── Evidence-to-mass mappings (parallel to SL formation functions) ──

def _ds_serialization(ev: Optional[SerializationEvidence],
                      W: float = 2.0) -> MassFunction:
    """Map serialization evidence to D-S mass function.

    Positive evidence (successful scans) → m(safe)
    Negative evidence (failures) → m(compromised)
    Remaining mass → m(Θ) based on total evidence vs prior weight
    """
    if ev is None:
        return MassFunction.vacuous()
    total_scanned = ev.scanned_success + ev.scanned_failure
    if total_scanned == 0:
        return MassFunction.vacuous()
    # Evidence strength relative to prior weight
    total_evidence = total_scanned
    denom = total_evidence + W
    m_safe = ev.scanned_success / denom
    m_comp = ev.scanned_failure / denom
    m_theta = W / denom
    # Adjust for scan coverage (discount by coverage)
    coverage = total_scanned / ev.total_packages
    m_safe *= coverage
    m_comp *= coverage
    m_theta = 1.0 - m_safe - m_comp
    return MassFunction(max(0, m_safe), max(0, m_comp), max(0, m_theta))


def _ds_cold_chain(ev: Optional[ColdChainEvidence],
                    W: float = 2.0) -> MassFunction:
    """Map cold chain evidence to D-S mass function."""
    if ev is None:
        return MassFunction.vacuous()
    if len(ev.readings) == 0:
        return MassFunction.vacuous()
    in_range = sum(1 for t in ev.readings
                   if ev.required_min <= t <= ev.required_max)
    out_range = len(ev.readings) - in_range
    total = len(ev.readings)
    denom = total + W
    # Weight by severity of deviations
    severity = 0.0
    if out_range > 0:
        for t in ev.readings:
            if t < ev.required_min:
                severity += abs(t - ev.required_min)
            elif t > ev.required_max:
                severity += abs(t - ev.required_max)
        severity = min(severity / out_range, 10.0) / 10.0  # normalize
    m_safe = in_range / denom
    m_comp = (out_range / denom) * (0.5 + 0.5 * severity)
    # Coverage adjustment
    coverage = total / ev.expected_readings
    m_safe *= coverage
    m_comp *= coverage
    m_theta = max(0.0, 1.0 - m_safe - m_comp)
    return MassFunction(max(0, m_safe), max(0, m_comp), max(0, m_theta))


def _ds_shelf_life(ev: Optional[ShelfLifeEvidence],
                    W: float = 2.0) -> MassFunction:
    """Map shelf life evidence to D-S mass function."""
    if ev is None:
        return MassFunction.vacuous()
    ratio = ev.remaining_days / ev.regulatory_min
    if ratio >= 2.0:
        # Well above threshold: strong evidence of safety
        return MassFunction(0.8, 0.0, 0.2)
    elif ratio >= 1.0:
        # Above threshold but approaching: moderate evidence
        strength = (ratio - 1.0) / 1.0  # 0.0 at threshold, 1.0 at 2×
        return MassFunction(0.4 * strength + 0.1, 0.0, 0.9 - 0.4 * strength)
    else:
        # Below threshold: evidence of compromise
        strength = 1.0 - ratio  # 0.0 at threshold, 1.0 at ratio=0
        return MassFunction(0.0, 0.3 + 0.5 * strength, 0.7 - 0.5 * strength)


def _ds_documentation(ev: Optional[DocumentationEvidence],
                      W: float = 2.0) -> MassFunction:
    """Map documentation evidence to D-S mass function."""
    if ev is None:
        return MassFunction.vacuous()
    if len(ev.documents_required) == 0:
        return MassFunction.vacuous()
    present = len(ev.documents_present & ev.documents_required)
    required = len(ev.documents_required)
    completeness = present / required
    # Critical documents check
    crit_ok = ev.documents_critical.issubset(ev.documents_present) if ev.documents_critical else True
    if completeness >= 1.0 and crit_ok:
        return MassFunction(0.6, 0.0, 0.4)
    elif crit_ok:
        return MassFunction(0.3 * completeness, 0.1 * (1 - completeness), 0.6 + 0.1 * (1 - completeness))
    else:
        # Missing critical docs: moderate evidence of compromise
        return MassFunction(0.1, 0.3, 0.6)


def _ds_recall(recall: RecallStatus) -> MassFunction:
    """Map recall status to D-S mass function."""
    if recall == RecallStatus.ACTIVE_RECALL:
        return MassFunction(0.0, 0.99, 0.01)
    else:
        return MassFunction.vacuous()  # NO_RECALL = no evidence


def evaluate_dempster_shafer(
    path: List[str],
    node_evidence: Dict[str, NodeEvidence],
    detection_threshold: float = 0.5,
) -> BaselineResult:
    """B4: Dempster-Shafer evidence theory.

    Maps each evidence type to a D-S mass function, combines within-node
    via Dempster’s rule, then combines cross-node via Dempster’s rule.
    Uses pignistic probability BetP(safe) as the integrity score.

    Key limitations vs. SL algebra:
    - No deduce operator: cross-node combination is Dempster’s rule
      (treats nodes as independent evidence, doesn’t model causal
      propagation of compromise)
    - Subject to Zadeh’s paradox under high conflict
    - No base rate: pignistic probability assumes uniform prior
    - No trust discounting for sensor coverage
    """
    node_scores: Dict[str, float] = {}
    node_masses: Dict[str, MassFunction] = {}

    for node_id in path:
        ev = node_evidence.get(node_id, NodeEvidence())
        masses = [
            _ds_serialization(ev.serialization),
            _ds_cold_chain(ev.cold_chain),
            _ds_shelf_life(ev.shelf_life),
            _ds_documentation(ev.documentation),
            _ds_recall(ev.recall),
        ]
        fused = dempster_combine_multi(*masses)
        node_masses[node_id] = fused
        node_scores[node_id] = fused.pignistic_safe()

    # Cross-node: Dempster’s rule across all nodes (no deduce equivalent)
    chain_mass = dempster_combine_multi(*[node_masses[n] for n in path])
    chain_score = chain_mass.pignistic_safe()
    weakest = min(path, key=lambda n: node_scores[n])

    return BaselineResult(
        method="B4_DempsterShafer",
        path=path,
        chain_score=chain_score,
        node_scores=node_scores,
        detected=(chain_score < detection_threshold),
        weakest_node=weakest,
        detection_threshold=detection_threshold,
    )


# Update registry
BASELINE_METHODS["B4_DempsterShafer"] = evaluate_dempster_shafer


def evaluate_all_baselines(
    path: List[str],
    node_evidence: Dict[str, NodeEvidence],
    detection_threshold: float = 0.5,
) -> Dict[str, BaselineResult]:
    """Run all three baselines on the same evidence."""
    return {
        name: fn(path, node_evidence, detection_threshold=detection_threshold)
        for name, fn in BASELINE_METHODS.items()
    }
