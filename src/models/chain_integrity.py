"""
Chain Integrity Algebra — Composition of drug integrity opinions.

Implements Definitions 4–7 from [Paper §3.4–§3.7]:
    §3.4 — Within-node fusion (Definition 4)
    §3.5 — Cross-node propagation (Definition 5)
    §3.6 — Temporal decay (Definition 6)
    §3.7 — Missing evidence handling (Definition 7)

Architecture:
    ┌─────────────────────────────────────────────────────┐
    │  Chain-Level Integrity Score (this module)           │
    │                                                     │
    │  evaluate_chain() ← evaluate_node() × N             │
    │       │                    │                         │
    │       │ cross-node         │ within-node             │
    │       │ deduce()           │ cumulative_fuse()       │
    │       │ (Jøsang §12.6)    │ (Jøsang §12.3)         │
    │       │                    │                         │
    │       │                    ├─ F1: serialization      │
    │       │                    ├─ F2: cold chain         │
    │       │                    ├─ F3: shelf life         │
    │       │                    ├─ F4: documentation      │
    │       │                    └─ F5: recall             │
    └─────────────────────────────────────────────────────┘

Cross-Node Propagation Semantics (Definition 5, revised):
    Trust discounting (§14.3) has the WRONG semantics for drug integrity
    chains. In trust models, disbelief in a source produces UNCERTAINTY
    about the source's claims. But for drug integrity, compromise at any
    node should produce chain-level DISBELIEF — you can't un-compromise
    a drug.

    Instead, we use the DEDUCTION operator (§12.6) with causal conditionals:
        - If node i was safe: chain safety depends on remaining nodes
        - If node i was compromised: chain is compromised regardless

    This captures the conjunctive semantics: the drug is safe at dispensing
    iff it was handled safely at EVERY node. A single compromise breaks
    the chain.

All composition uses established SL operators from jsonld-ex.
No new operators are defined in this module.

Dependencies:
    - jsonld_ex.confidence_algebra (Opinion, cumulative_fuse, deduce)
    - jsonld_ex.confidence_decay (decay_opinion)
    - models.drug_integrity_opinion (F1–F5, DrugIntegrityOpinion, NodeEvidence)
    - data.supply_chain_dag (SupplyChainDAG)

References:
    Jøsang, A. (2016). Subjective Logic. Springer.
      §12.3: Cumulative fusion
      §12.6: Deduction operator (conditional reasoning under uncertainty)
      §10.4: Opinion aging (temporal decay)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from jsonld_ex.confidence_algebra import (
    Opinion,
    cumulative_fuse,
    deduce,
)
from jsonld_ex.confidence_decay import decay_opinion, exponential_decay

from models.drug_integrity_opinion import (
    DrugIntegrityOpinion,
    EvidenceWeights,
    NodeEvidence,
    RecallStatus,
    form_serialization_opinion,
    form_cold_chain_opinion,
    form_shelf_life_opinion,
    form_documentation_opinion,
    form_recall_opinion,
)
from data.supply_chain_dag import SupplyChainDAG


# ═══════════════════════════════════════════════════════════════════
# CAUSAL OVERRIDE CONDITIONAL (Definition 5 — key constant)
# ═══════════════════════════════════════════════════════════════════

# The "compromised conditional": if a node compromised the drug,
# the chain is compromised regardless of downstream handling.
#
# ω_{chain|¬safe_i} = (0, 1, 0, 0.01)
#
# Dogmatic disbelief. Base rate 0.01 reflects the negligible prior
# probability that a compromised drug is somehow still safe.
#
# This is the key causal assumption: drug compromise is irreversible
# within the supply chain. A temperature excursion cannot be undone
# by subsequent perfect handling.
COMPROMISED_CONDITIONAL = Opinion(0.0, 1.0, 0.0, 0.01)


# ═══════════════════════════════════════════════════════════════════
# FORMATION PARAMETERS (application-specific modeling choices, Definition 3)
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class FormationParams:
    """The application-specific parameters of the formation functions.

    These are modeling assumptions of the drug integrity algebra, distinct
    from Josang's operators: the per-type base rates, the weight of a
    missing critical document, the shelf-life evidence scale and the
    cold-chain severity cap. Defaults equal the values that were
    hard-coded before EXP-008 (camera-ready), so evaluate_path with the
    default parameters reproduces every published result exactly.
    EXP-008 varies them one at a time.
    """
    base_rate_serialization: float = 0.99
    base_rate_cold_chain: float = 0.95
    base_rate_shelf_life: float = 0.90
    base_rate_documentation: float = 0.80
    base_rate_recall: float = 0.99
    critical_weight: float = 3.0
    shelf_life_scale: float = 10.0
    severity_cap: float = 5.0


DEFAULT_FORMATION = FormationParams()


# ═══════════════════════════════════════════════════════════════════
# TEMPORAL DECAY CONFIGURATION (Definition 6)
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TemporalDecayConfig:
    """Half-lives for temporal decay of each evidence type.

    Per Definition 6 (Paper §3.6): evidence ages, and older evidence
    contributes less to the current integrity assessment. Each evidence
    type has a domain-appropriate half-life reflecting how quickly
    conditions can change.

    Units: seconds.
    """
    serialization_half_life: float = 2_592_000.0   # 30 days
    cold_chain_half_life: float = 14_400.0          # 4 hours
    # shelf_life: no decay (already time-dependent by construction)
    documentation_half_life: float = 15_552_000.0   # 180 days
    # recall: no decay (binary status, always current)


# ═══════════════════════════════════════════════════════════════════
# NODE-LEVEL EVALUATION (Definition 4 — Within-Node Fusion)
# ═══════════════════════════════════════════════════════════════════


@dataclass
class NodeEvaluationResult:
    """Result of evaluating integrity at a single supply chain node.

    Captures the fused opinion and all individual evidence opinions
    for traceability and localization analysis.
    """
    node_id: str
    fused_opinion: DrugIntegrityOpinion
    evidence_opinions: Dict[str, DrugIntegrityOpinion] = field(default_factory=dict)
    missing_evidence: List[str] = field(default_factory=list)


def evaluate_node(
    node_id: str,
    evidence: NodeEvidence,
    weights: EvidenceWeights = EvidenceWeights(),
    base_prior_weight: float = 2.0,
    decay_config: Optional[TemporalDecayConfig] = None,
    evidence_timestamps: Optional[Dict[str, datetime]] = None,
    eval_time: Optional[datetime] = None,
    formation: Optional[FormationParams] = None,
) -> NodeEvaluationResult:
    """Evaluate drug integrity at a single supply chain node.

    Per Definition 4 (Paper §3.4): fuse five evidence opinions into
    a single node-level integrity opinion using cumulative fusion.

    Evidence weighting (Paper §3.4 Note):
        Weights control effective evidence strength by adjusting the
        Dirichlet prior weight W. For evidence type with weight w_i:
            effective_W = base_prior_weight / w_i

        Higher weight → lower W → more evidence per observation →
        opinion is more influential in cumulative fusion.

        Mathematically equivalent to scaling evidence counts by w_i
        (proven in LOGBOOK EXP-001), but cleaner to implement via W.

    Missing evidence (Definition 7, Paper §3.7):
        When an evidence type is unavailable (None in NodeEvidence),
        a VACUOUS opinion is substituted: ω = (0, 0, 1, a).
        Vacuous opinions participate in fusion but contribute no
        information — uncertainty propagates honestly.

    Temporal decay (Definition 6, Paper §3.6):
        If decay_config and timestamps are provided, each evidence
        opinion is decayed based on its age before fusion.

    Args:
        node_id:              Identifier for this node.
        evidence:             NodeEvidence bundle (any field may be None).
        weights:              Evidence type weights (default: equal weights).
        base_prior_weight:    Base Dirichlet prior weight (default 2.0).
        decay_config:         Temporal decay half-lives (optional).
        evidence_timestamps:  Timestamps for each evidence type (optional).
                              Keys: "serialization", "cold_chain", "documentation".
        eval_time:            Current evaluation time (optional, for decay).

    Returns:
        NodeEvaluationResult with fused opinion and per-evidence opinions.
    """
    opinions: List[Opinion] = []
    evidence_opinions: Dict[str, DrugIntegrityOpinion] = {}
    missing: List[str] = []
    fp = formation if formation is not None else DEFAULT_FORMATION

    # ── F1: Serialization ──────────────────────────────────────
    if evidence.serialization is not None:
        eff_w = base_prior_weight / weights.serialization
        op = form_serialization_opinion(
            evidence.serialization, prior_weight=eff_w, base_rate=fp.base_rate_serialization,
        )
        op = _maybe_decay(
            op, "serialization", decay_config, evidence_timestamps, eval_time,
            lambda cfg: cfg.serialization_half_life,
        )
        evidence_opinions["serialization"] = op
    else:
        op = DrugIntegrityOpinion.vacuous(fp.base_rate_serialization)
        evidence_opinions["serialization"] = op
        missing.append("serialization")
    opinions.append(op)

    # ── F2: Cold Chain ─────────────────────────────────────────
    if evidence.cold_chain is not None:
        eff_w = base_prior_weight / weights.cold_chain
        op = form_cold_chain_opinion(
            evidence.cold_chain, prior_weight=eff_w, base_rate=fp.base_rate_cold_chain,
            severity_cap=fp.severity_cap,
        )
        op = _maybe_decay(
            op, "cold_chain", decay_config, evidence_timestamps, eval_time,
            lambda cfg: cfg.cold_chain_half_life,
        )
        evidence_opinions["cold_chain"] = op
    else:
        op = DrugIntegrityOpinion.vacuous(fp.base_rate_cold_chain)
        evidence_opinions["cold_chain"] = op
        missing.append("cold_chain")
    opinions.append(op)

    # ── F3: Shelf Life ─────────────────────────────────────────
    if evidence.shelf_life is not None:
        eff_w = base_prior_weight / weights.shelf_life
        op = form_shelf_life_opinion(
            evidence.shelf_life, prior_weight=eff_w, base_rate=fp.base_rate_shelf_life,
            scale=fp.shelf_life_scale,
        )
        # No temporal decay for shelf life (already time-dependent)
        evidence_opinions["shelf_life"] = op
    else:
        op = DrugIntegrityOpinion.vacuous(fp.base_rate_shelf_life)
        evidence_opinions["shelf_life"] = op
        missing.append("shelf_life")
    opinions.append(op)

    # ── F4: Documentation ──────────────────────────────────────
    if evidence.documentation is not None:
        eff_w = base_prior_weight / weights.documentation
        op = form_documentation_opinion(
            evidence.documentation, prior_weight=eff_w, base_rate=fp.base_rate_documentation,
            critical_weight=fp.critical_weight,
        )
        op = _maybe_decay(
            op, "documentation", decay_config, evidence_timestamps, eval_time,
            lambda cfg: cfg.documentation_half_life,
        )
        evidence_opinions["documentation"] = op
    else:
        op = DrugIntegrityOpinion.vacuous(fp.base_rate_documentation)
        evidence_opinions["documentation"] = op
        missing.append("documentation")
    opinions.append(op)

    # ── F5: Recall ─────────────────────────────────────────────
    op = form_recall_opinion(evidence.recall, base_rate=fp.base_rate_recall)
    evidence_opinions["recall"] = op
    # No decay for recall (binary, always current)
    opinions.append(op)

    # ── Within-node fusion (cumulative, Jøsang §12.3) ─────────
    if len(opinions) == 1:
        fused = opinions[0]
    else:
        fused = cumulative_fuse(*opinions)

    return NodeEvaluationResult(
        node_id=node_id,
        fused_opinion=DrugIntegrityOpinion.from_opinion(fused),
        evidence_opinions=evidence_opinions,
        missing_evidence=missing,
    )


def _maybe_decay(
    opinion: DrugIntegrityOpinion,
    evidence_key: str,
    decay_config: Optional[TemporalDecayConfig],
    timestamps: Optional[Dict[str, datetime]],
    eval_time: Optional[datetime],
    get_half_life,
) -> DrugIntegrityOpinion:
    """Apply temporal decay if configuration and timestamps are available."""
    if decay_config is None or timestamps is None or eval_time is None:
        return opinion
    ts = timestamps.get(evidence_key)
    if ts is None:
        return opinion
    elapsed = (eval_time - ts).total_seconds()
    if elapsed <= 0:
        return opinion
    half_life = get_half_life(decay_config)
    decayed = decay_opinion(opinion, elapsed=elapsed, half_life=half_life)
    return DrugIntegrityOpinion.from_opinion(decayed)


# ═══════════════════════════════════════════════════════════════════
# CHAIN-LEVEL EVALUATION (Definition 5 — Cross-Node Propagation)
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ChainEvaluationResult:
    """Result of evaluating integrity across an entire supply chain path.

    Attributes:
        path:            List of node IDs in the evaluated path.
        chain_opinion:   End-to-end integrity opinion for this path.
        node_results:    Per-node evaluation results (for localization).
        weakest_node:    Node ID with lowest projected probability.
        integrity_score: Projected probability P(ω) of chain opinion.
        uncertainty:     Uncertainty component u of chain opinion.
    """
    path: List[str]
    chain_opinion: DrugIntegrityOpinion
    node_results: Dict[str, NodeEvaluationResult]
    weakest_node: str
    integrity_score: float
    uncertainty: float


def evaluate_path(
    path: List[str],
    node_evidence: Dict[str, NodeEvidence],
    weights: EvidenceWeights = EvidenceWeights(),
    base_prior_weight: float = 2.0,
    decay_config: Optional[TemporalDecayConfig] = None,
    evidence_timestamps: Optional[Dict[str, Dict[str, datetime]]] = None,
    eval_time: Optional[datetime] = None,
    compromised_conditional: Opinion = COMPROMISED_CONDITIONAL,
    formation: Optional[FormationParams] = None,
) -> ChainEvaluationResult:
    """Evaluate drug integrity along a single supply chain path.

    Per Definition 5 (Paper §3.5): conjunctive composition via the
    deduction operator along the product path π = (n_1, ..., n_m).

    Cross-node propagation (Jøsang §12.6 — Deduction):
        The drug is safe at dispensing iff it was handled safely at
        EVERY node. This is conjunctive: safe_chain = ∧_i safe_i.

        We model this via deduction with causal conditionals:
            ω_y|x_i   = ω_remaining  (if node i safe, chain depends on rest)
            ω_y|¬x_i  = ω_compromised (if node i compromised, chain fails)

        Serial composition (right fold):
            ω_chain = deduce(ω_1, deduce(ω_2, ..., deduce(ω_{m-1}, ω_m,
                      ω_compromised), ω_compromised), ω_compromised)

    Why deduction, not trust discounting:
        trust_discount maps upstream disbelief to downstream UNCERTAINTY.
        But drug compromise at any node should produce chain-level
        DISBELIEF — you can't un-compromise a drug by handling it
        properly afterward. The deduction operator with causal
        conditionals correctly propagates disbelief.

    Args:
        path:                  Ordered list of node IDs from source to sink.
        node_evidence:         Evidence bundles keyed by node ID.
        weights:               Evidence type weights.
        base_prior_weight:     Base Dirichlet prior weight.
        decay_config:          Temporal decay configuration.
        evidence_timestamps:   Per-node evidence timestamps.
        eval_time:             Current evaluation time.
        compromised_conditional: The ω_y|¬x conditional (default: dogmatic
                               disbelief — compromise is irreversible).

    Returns:
        ChainEvaluationResult with chain-level opinion and per-node breakdown.
    """
    if len(path) == 0:
        raise ValueError("Path must contain at least one node")

    # Step 1: Evaluate each node independently
    node_results: Dict[str, NodeEvaluationResult] = {}
    for node_id in path:
        ev = node_evidence.get(node_id, NodeEvidence())
        ts = (evidence_timestamps or {}).get(node_id)
        result = evaluate_node(
            node_id=node_id,
            evidence=ev,
            weights=weights,
            base_prior_weight=base_prior_weight,
            decay_config=decay_config,
            evidence_timestamps=ts,
            eval_time=eval_time,
            formation=formation,
        )
        node_results[node_id] = result

    # Step 2: Cross-node propagation via deduction (right fold)
    #
    # Causal semantics: for each node i along the path:
    #   ω_chain = deduce(ω_i, ω_remaining, ω_compromised)
    # where:
    #   ω_i = node i's integrity opinion (the antecedent)
    #   ω_remaining = chain opinion from nodes i+1..m (if safe at i)
    #   ω_compromised = dogmatic disbelief (if compromised at i)
    #
    # Base case: ω_chain = ω_m (last node's opinion)
    # Inductive: ω_chain = deduce(ω_i, ω_chain_{i+1..m}, ω_compromised)
    node_opinions = [node_results[nid].fused_opinion for nid in path]

    if len(node_opinions) == 1:
        chain_opinion = node_opinions[0]
    else:
        # Right fold: start from last node, fold leftward
        chain_opinion = node_opinions[-1]
        for i in range(len(node_opinions) - 2, -1, -1):
            chain_opinion = deduce(
                opinion_x=node_opinions[i],             # antecedent: is node i safe?
                opinion_y_given_x=chain_opinion,         # if safe: chain depends on rest
                opinion_y_given_not_x=compromised_conditional,  # if compromised: chain fails
            )

    chain_opinion = DrugIntegrityOpinion.from_opinion(chain_opinion)

    # Identify weakest node (lowest projected probability)
    weakest_node = min(
        path,
        key=lambda nid: node_results[nid].fused_opinion.projected_probability(),
    )

    return ChainEvaluationResult(
        path=path,
        chain_opinion=chain_opinion,
        node_results=node_results,
        weakest_node=weakest_node,
        integrity_score=chain_opinion.projected_probability(),
        uncertainty=chain_opinion.uncertainty,
    )


def evaluate_chain(
    dag: SupplyChainDAG,
    node_evidence: Dict[str, NodeEvidence],
    weights: EvidenceWeights = EvidenceWeights(),
    base_prior_weight: float = 2.0,
    decay_config: Optional[TemporalDecayConfig] = None,
    evidence_timestamps: Optional[Dict[str, Dict[str, datetime]]] = None,
    eval_time: Optional[datetime] = None,
    target_sink: Optional[str] = None,
    compromised_conditional: Opinion = COMPROMISED_CONDITIONAL,
    formation: Optional[FormationParams] = None,
) -> Dict[str, ChainEvaluationResult]:
    """Evaluate drug integrity across all paths in the supply chain.

    For multi-path supply chains (Definition 5, Paper §3.5):
        When a batch can reach the same sink through multiple paths,
        each path is evaluated independently. The results are returned
        per-sink so the caller can fuse or compare.

    Args:
        dag:                Supply chain DAG topology.
        node_evidence:      Evidence bundles keyed by node ID.
        weights:            Evidence type weights.
        base_prior_weight:  Base Dirichlet prior weight.
        decay_config:       Temporal decay configuration.
        evidence_timestamps: Per-node evidence timestamps.
        eval_time:          Current evaluation time.
        target_sink:        If specified, only evaluate paths to this sink.

    Returns:
        Dict mapping sink_node_id → ChainEvaluationResult for the path
        reaching that sink. If multiple paths reach the same sink, the
        one with the lowest integrity score is reported (conservative).
    """
    paths = dag.enumerate_paths(sink=target_sink)

    results: Dict[str, ChainEvaluationResult] = {}

    for path in paths:
        sink = path[-1]
        result = evaluate_path(
            path=path,
            node_evidence=node_evidence,
            weights=weights,
            base_prior_weight=base_prior_weight,
            decay_config=decay_config,
            evidence_timestamps=evidence_timestamps,
            eval_time=eval_time,
            compromised_conditional=compromised_conditional,
            formation=formation,
        )

        # If multiple paths reach the same sink, keep the worst (conservative)
        if sink not in results or result.integrity_score < results[sink].integrity_score:
            results[sink] = result

    return results


# ═══════════════════════════════════════════════════════════════════
# FAILURE LOCALIZATION
# ═══════════════════════════════════════════════════════════════════


def localize_failure(
    chain_result: ChainEvaluationResult,
    integrity_threshold: float = 0.5,
    uncertainty_threshold: float = 0.3,
) -> List[str]:
    """Identify which nodes are most likely responsible for chain degradation.

    Uses per-node evaluation results to find nodes where:
    1. Projected probability is below integrity_threshold, OR
    2. Uncertainty is above uncertainty_threshold

    Returns node IDs sorted by severity (lowest projected probability first).

    This is a key advantage over threshold-based systems: the algebra
    doesn't just detect that something is wrong — it can point to WHERE
    in the chain the problem occurred, because per-node opinions are
    preserved through the composition.
    """
    flagged: List[Tuple[str, float]] = []

    for node_id, result in chain_result.node_results.items():
        pp = result.fused_opinion.projected_probability()
        u = result.fused_opinion.uncertainty
        if pp < integrity_threshold or u > uncertainty_threshold:
            flagged.append((node_id, pp))

    # Sort by projected probability (worst first)
    flagged.sort(key=lambda x: x[1])

    return [node_id for node_id, _ in flagged]
