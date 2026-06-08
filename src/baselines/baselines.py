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
