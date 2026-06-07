"""
Drug Integrity Algebra — Evidence-to-Opinion Formation Functions.

Implements the five evidence formation functions (F1–F5) for the drug
integrity algebra, as defined in [Paper §3.3].

Design Principle — COMPOSABILITY FROM ESTABLISHED OPERATORS:
    Every formation function decomposes into exactly two steps:
      1. Evidence mapping:  Opinion.from_evidence(r, w, W, a)
         — Jøsang (2016, §3.2), standard Dirichlet evidence-to-opinion
      2. Coverage adjustment: trust_discount(coverage_trust, evidence_opinion)
         — Jøsang (2016, §14.3), trust discounting for partial observability

    No ad-hoc formulas are introduced. Every computation is a documented
    SL operator, verifiable against Jøsang's textbook. This is a deliberate
    architectural decision: the contribution is the FORMALIZATION of pharma
    supply chain evidence as SL inputs, not the invention of new operators.

Proposition under evaluation:
    "This drug batch is safe and intact at this supply chain node."
    Belief (b) = evidence FOR safety/integrity.
    Disbelief (d) = evidence AGAINST safety/integrity.
    Uncertainty (u) = absence of evidence.

Evidence types:
    F1 — Serialization scans (identity verification)
    F2 — Cold chain monitoring (temperature integrity)
    F3 — Shelf life status (temporal viability)
    F4 — Documentation completeness (regulatory compliance)
    F5 — Recall status (safety override)

Dependencies:
    - jsonld_ex.confidence_algebra (Opinion, trust_discount)
    - stdlib only (dataclasses, math, typing, enum)

References:
    Jøsang, A. (2016). Subjective Logic. Springer.
      §3.2: Evidence-to-opinion mapping (Beta/Dirichlet)
      §14.3: Trust discounting operator
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional, Sequence, Tuple

from jsonld_ex.confidence_algebra import Opinion, trust_discount


# ═══════════════════════════════════════════════════════════════════
# DRUG INTEGRITY OPINION (Definition 1 — Domain Subclass)
# ═══════════════════════════════════════════════════════════════════


class DrugIntegrityOpinion(Opinion):
    """A drug integrity opinion ω = (i, g, u, a) per Definition 1.

    Extends Opinion with domain-specific semantics:
        i (integrity)    = belief      — evidence that the drug is safe/intact
        g (degradation)  = disbelief   — evidence that the drug is compromised
        u (uncertainty)  = uncertainty — absence of evidence
        a (base_rate)    = base_rate   — prior integrity probability

    Invariant: i + g + u = 1, all components in [0, 1].
    Projected probability: P(ω) = i + a·u.

    All existing SL operators accept DrugIntegrityOpinion via isinstance
    because it inherits from Opinion.
    """

    # ── Domain-specific property aliases ───────────────────────

    @property
    def integrity(self) -> float:
        """Evidence of drug safety/integrity (alias for belief)."""
        return self.belief

    @property
    def degradation(self) -> float:
        """Evidence of drug compromise/degradation (alias for disbelief)."""
        return self.disbelief

    # ── Domain factories ──────────────────────────────────────

    @classmethod
    def from_opinion(cls, op: Opinion) -> DrugIntegrityOpinion:
        """Wrap a plain Opinion as a DrugIntegrityOpinion."""
        obj = object.__new__(cls)
        object.__setattr__(obj, "belief", op.belief)
        object.__setattr__(obj, "disbelief", op.disbelief)
        object.__setattr__(obj, "uncertainty", op.uncertainty)
        object.__setattr__(obj, "base_rate", op.base_rate)
        return obj

    @classmethod
    def vacuous(cls, base_rate: float = 0.5) -> DrugIntegrityOpinion:
        """Create a vacuous (maximally uncertain) opinion."""
        return cls.from_opinion(Opinion(0.0, 0.0, 1.0, base_rate))

    @classmethod
    def dogmatic_safe(cls) -> DrugIntegrityOpinion:
        """Create a dogmatic 'drug is safe' opinion."""
        return cls.from_opinion(Opinion(1.0, 0.0, 0.0, 0.99))

    @classmethod
    def dogmatic_compromised(cls) -> DrugIntegrityOpinion:
        """Create a dogmatic 'drug is compromised' opinion."""
        return cls.from_opinion(Opinion(0.0, 1.0, 0.0, 0.01))

    # ── Representation ─────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"DrugIntegrityOpinion("
            f"integrity={self.belief:.4f}, "
            f"degradation={self.disbelief:.4f}, "
            f"u={self.uncertainty:.4f}, "
            f"a={self.base_rate:.4f})"
        )


# ═══════════════════════════════════════════════════════════════════
# EVIDENCE TYPES (Definition 2 — Enumeration)
# ═══════════════════════════════════════════════════════════════════


class EvidenceType(Enum):
    """Evidence types observable at each supply chain node."""
    SERIALIZATION = auto()
    COLD_CHAIN = auto()
    SHELF_LIFE = auto()
    DOCUMENTATION = auto()
    RECALL = auto()


class RecallStatus(Enum):
    """Recall status for a drug batch."""
    ACTIVE_RECALL = auto()
    NO_RECALL = auto()


# ═══════════════════════════════════════════════════════════════════
# F1 — SERIALIZATION OPINION (Definition 3a)
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SerializationEvidence:
    """Raw serialization scan evidence at a supply chain node.

    Attributes:
        scanned_success: Number of packages scanned with valid GTIN/serial.
        scanned_failure: Number of packages scanned with mismatched identity.
        total_packages:  Total packages in the shipment.
    """
    scanned_success: int
    scanned_failure: int
    total_packages: int

    def __post_init__(self) -> None:
        if self.scanned_success < 0 or self.scanned_failure < 0:
            raise ValueError("Scan counts must be non-negative")
        if self.total_packages <= 0:
            raise ValueError("Total packages must be positive")
        if self.scanned_success + self.scanned_failure > self.total_packages:
            raise ValueError(
                "Scanned count exceeds total packages: "
                f"{self.scanned_success} + {self.scanned_failure} > {self.total_packages}"
            )


def form_serialization_opinion(
    evidence: SerializationEvidence,
    prior_weight: float = 2.0,
    base_rate: float = 0.99,
) -> DrugIntegrityOpinion:
    """F1: Map serialization scan evidence to an SL opinion.

    Two-step construction per §3.3a:

    Step 1 — Evidence mapping (Jøsang §3.2):
        Form an opinion from observed scans using the standard
        Dirichlet evidence-to-opinion mapping:
            ω_sample = from_evidence(r=success, w=failure, W=prior_weight)

    Step 2 — Coverage adjustment (Jøsang §14.3):
        Adjust for partial observability using trust discounting.
        The scan coverage rate (fraction of packages scanned) becomes
        a trust opinion about the observation process:
            coverage = (success + failure) / total
            ω_coverage = Opinion(coverage, 0, 1 - coverage, base_rate)
            ω_ser = trust_discount(ω_coverage, ω_sample)

        Intuition: if we scanned 100% of packages, we fully trust the
        sample opinion. If we scanned 10%, the result is diluted toward
        uncertainty. If we scanned 0%, the result is vacuous.

    Properties (proven):
        P1: Full scan, all pass → ω ≈ (1, 0, 0) (near-dogmatic integrity)
        P2: Full scan, some fail → d proportional to failure rate
        P3: Partial scan, all pass → u proportional to unscanned fraction
        P4: No scan → ω = vacuous (maximal uncertainty)
        P5: Monotonic in coverage: increasing scan rate can only decrease u

    Args:
        evidence:     SerializationEvidence with scan counts.
        prior_weight: Dirichlet prior weight W (default 2.0, standard).
        base_rate:    Prior integrity probability (default 0.99 for
                      regulated supply chains where counterfeiting is rare).

    Returns:
        DrugIntegrityOpinion for the serialization evidence dimension.
    """
    s = evidence.scanned_success
    f = evidence.scanned_failure
    n = evidence.total_packages

    # Handle degenerate case: no packages scanned at all
    scanned_total = s + f
    if scanned_total == 0:
        return DrugIntegrityOpinion.vacuous(base_rate)

    # Step 1: Evidence mapping (Jøsang §3.2)
    omega_sample = Opinion.from_evidence(
        positive=s,
        negative=f,
        prior_weight=prior_weight,
        base_rate=base_rate,
    )

    # Step 2: Coverage adjustment via trust discounting (Jøsang §14.3)
    coverage = scanned_total / n
    omega_coverage = Opinion(
        belief=coverage,
        disbelief=0.0,
        uncertainty=1.0 - coverage,
        base_rate=base_rate,
    )
    omega_adjusted = trust_discount(omega_coverage, omega_sample)

    return DrugIntegrityOpinion.from_opinion(omega_adjusted)


# ═══════════════════════════════════════════════════════════════════
# F2 — COLD CHAIN OPINION (Definition 3b)
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ColdChainEvidence:
    """Raw cold chain temperature evidence at a supply chain node.

    Attributes:
        readings:          List of temperature readings (°C).
        required_min:      Minimum required temperature (°C).
        required_max:      Maximum required temperature (°C).
        expected_readings: Number of readings expected for the observation
                           period (based on sensor interval and duration).
                           Used to compute sensor coverage.
    """
    readings: Tuple[float, ...]
    required_min: float
    required_max: float
    expected_readings: int

    def __post_init__(self) -> None:
        if self.required_min >= self.required_max:
            raise ValueError(
                f"required_min must be < required_max: "
                f"{self.required_min} >= {self.required_max}"
            )
        if self.expected_readings <= 0:
            raise ValueError("expected_readings must be positive")


def form_cold_chain_opinion(
    evidence: ColdChainEvidence,
    prior_weight: float = 2.0,
    base_rate: float = 0.95,
    severity_cap: float = 5.0,
) -> DrugIntegrityOpinion:
    """F2: Map cold chain temperature evidence to an SL opinion.

    Two-step construction per §3.3b:

    Step 1 — Severity-weighted evidence mapping (Jøsang §3.2 + §3.4):
        Each temperature reading is classified as positive or negative
        evidence. Positive evidence (in-range readings) has unit weight.
        Negative evidence (out-of-range readings) is weighted by excursion
        severity: how far outside the required range, normalized by range
        width. This captures the physical reality that a 0.5°C excursion
        is far less damaging than a 17°C excursion.

            For reading T_i with range [T_min, T_max], range width R:
            - In-range:      r += 1.0
            - Out-of-range:  w += 1.0 + min(excursion / R, severity_cap)
              where excursion = max(T_i - T_max, T_min - T_i)

        The +1.0 ensures even a barely-out-of-range reading contributes
        at least one unit of negative evidence (matching the weight of
        one positive observation).

        ω_sample = from_evidence(r=r_total, w=w_total, W=prior_weight)

    Step 2 — Sensor coverage adjustment (Jøsang §14.3):
        If the sensor was offline for part of the observation period
        (gaps in readings), adjust via trust discounting:
            coverage = actual_readings / expected_readings
            ω_coverage = Opinion(coverage, 0, 1 - coverage, base_rate)
            ω_cc = trust_discount(ω_coverage, ω_sample)

    Properties (proven):
        P1: All in-range, full coverage → near-dogmatic integrity
        P2: Mild excursion → d increases slightly (proportional to severity)
        P3: Severe excursion → d dominates (proportional to severity × duration)
        P4: Sensor offline → u increases (proportional to gap fraction)
        P5: No readings → vacuous opinion

    Args:
        evidence:     ColdChainEvidence with readings and required range.
        prior_weight: Dirichlet prior weight W (default 2.0).
        base_rate:    Prior integrity probability (default 0.95).
        severity_cap: Maximum severity weight per reading (default 5.0,
                      prevents single extreme reading from dominating).

    Returns:
        DrugIntegrityOpinion for the cold chain evidence dimension.
    """
    readings = evidence.readings
    t_min = evidence.required_min
    t_max = evidence.required_max
    n_expected = evidence.expected_readings
    range_width = t_max - t_min

    # Handle degenerate case: no readings at all
    if len(readings) == 0:
        return DrugIntegrityOpinion.vacuous(base_rate)

    # Step 1: Classify each reading and accumulate weighted evidence
    r_total = 0.0  # positive evidence (in-range readings)
    w_total = 0.0  # negative evidence (severity-weighted excursions)

    for temp in readings:
        if t_min <= temp <= t_max:
            # In-range: unit positive evidence
            r_total += 1.0
        else:
            # Out-of-range: severity-weighted negative evidence
            excursion = max(temp - t_max, t_min - temp)
            severity = min(excursion / range_width, severity_cap)
            # +1.0 ensures minimum unit weight per out-of-range reading
            w_total += 1.0 + severity

    omega_sample = Opinion.from_evidence(
        positive=r_total,
        negative=w_total,
        prior_weight=prior_weight,
        base_rate=base_rate,
    )

    # Step 2: Sensor coverage adjustment via trust discounting
    coverage = min(len(readings) / n_expected, 1.0)
    omega_coverage = Opinion(
        belief=coverage,
        disbelief=0.0,
        uncertainty=1.0 - coverage,
        base_rate=base_rate,
    )
    omega_adjusted = trust_discount(omega_coverage, omega_sample)

    return DrugIntegrityOpinion.from_opinion(omega_adjusted)


# ═══════════════════════════════════════════════════════════════════
# F3 — SHELF LIFE OPINION (Definition 3c)
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ShelfLifeEvidence:
    """Raw shelf life evidence at a supply chain node.

    Attributes:
        remaining_days: Days of shelf life remaining at this node.
        regulatory_min: Regulatory minimum remaining days required
                        for sale/dispensing (e.g., 180 days in UAE).
    """
    remaining_days: int
    regulatory_min: int

    def __post_init__(self) -> None:
        if self.remaining_days < 0:
            raise ValueError("remaining_days must be non-negative")
        if self.regulatory_min <= 0:
            raise ValueError("regulatory_min must be positive")


def form_shelf_life_opinion(
    evidence: ShelfLifeEvidence,
    prior_weight: float = 2.0,
    base_rate: float = 0.90,
    scale: float = 10.0,
) -> DrugIntegrityOpinion:
    """F3: Map shelf life status to an SL opinion.

    Single-step construction per §3.3c — no coverage adjustment needed
    because shelf life is a fully observed, deterministic quantity (the
    expiry date is printed on the package).

    The mapping converts the ratio of remaining-to-minimum days into
    evidence counts using the standard Dirichlet mapping:

        ratio = remaining_days / regulatory_min

        r = max(0, ratio - 1) × scale    (positive evidence: margin above threshold)
        w = max(0, 1 - ratio) × scale    (negative evidence: shortfall below threshold)

        ω_sl = from_evidence(r, w, W=prior_weight)

    Key behavior at the regulatory threshold (ratio = 1.0):
        r = 0, w = 0 → ω = vacuous (maximally uncertain)

    This is the CORRECT behavior: the regulatory threshold is exactly
    where the decision is hardest, and the opinion reflects maximum
    epistemic uncertainty. Above the threshold, belief grows with margin.
    Below the threshold, disbelief grows with deficit. The transition is
    smooth — no cliff edges that threshold-based systems exhibit.

    Properties (proven):
        P1: ratio >> 1 (ample margin) → near-dogmatic integrity
        P2: ratio = 1.0 (at threshold) → vacuous (maximum uncertainty)
        P3: ratio << 1 (expired) → near-dogmatic degradation
        P4: Monotonically degrading: as remaining_days decreases,
            belief can only decrease and disbelief can only increase
        P5: Continuous in ratio — no discontinuities

    Args:
        evidence:     ShelfLifeEvidence with remaining days and regulatory min.
        prior_weight: Dirichlet prior weight W (default 2.0).
        base_rate:    Prior integrity probability (default 0.90).
        scale:        Evidence scale factor (default 10.0). Controls how
                      quickly opinions become dogmatic as ratio moves from 1.0.
                      At scale=10, ratio=2.0 generates 10 units of positive
                      evidence — equivalent to 10 successful observations.

    Returns:
        DrugIntegrityOpinion for the shelf life evidence dimension.
    """
    ratio = evidence.remaining_days / evidence.regulatory_min

    # Evidence counts: distance from threshold in each direction
    r = max(0.0, ratio - 1.0) * scale
    w = max(0.0, 1.0 - ratio) * scale

    omega = Opinion.from_evidence(
        positive=r,
        negative=w,
        prior_weight=prior_weight,
        base_rate=base_rate,
    )

    return DrugIntegrityOpinion.from_opinion(omega)


# ═══════════════════════════════════════════════════════════════════
# F4 — DOCUMENTATION OPINION (Definition 3d)
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class DocumentationEvidence:
    """Raw documentation evidence at a supply chain node.

    Attributes:
        documents_present:  Set of document identifiers that are present.
        documents_required: Set of document identifiers required at this node.
        documents_critical: Subset of required documents that are safety-critical.
                            Missing a critical document contributes more heavily
                            to disbelief than missing a non-critical one.
    """
    documents_present: frozenset[str]
    documents_required: frozenset[str]
    documents_critical: frozenset[str]

    def __post_init__(self) -> None:
        if not self.documents_critical.issubset(self.documents_required):
            raise ValueError(
                "Critical documents must be a subset of required documents. "
                f"Extra: {self.documents_critical - self.documents_required}"
            )


# Default critical documents for pharmaceutical import (from domain expert)
DEFAULT_CRITICAL_DOCUMENTS = frozenset({
    "certificate_of_analysis",
    "gmp_certificate",
    "batch_release_certificate",
})

DEFAULT_REQUIRED_DOCUMENTS = frozenset({
    "certificate_of_analysis",
    "gmp_certificate",
    "batch_release_certificate",
    "bioequivalence_study",
    "import_license",
})


def form_documentation_opinion(
    evidence: DocumentationEvidence,
    prior_weight: float = 2.0,
    base_rate: float = 0.80,
    critical_weight: float = 3.0,
) -> DrugIntegrityOpinion:
    """F4: Map documentation completeness to an SL opinion.

    Single-step construction per §3.3d using weighted evidence counts.

    Each required document is treated as an observation:
        - Document present:              r += 1.0
        - Document missing (critical):   w += critical_weight
        - Document missing (non-crit.):  w += 1.0

    The asymmetric weighting reflects the domain reality that a missing
    Certificate of Analysis (CoA) is far more concerning than a missing
    non-critical form. The critical_weight parameter controls this ratio.

    ω_doc = from_evidence(r, w, W=prior_weight)

    No coverage adjustment: documentation is fully observable (a document
    is either present or absent — there is no partial observation).

    Properties (proven):
        P1: All documents present → high belief, low uncertainty
        P2: All documents missing, all critical → strong disbelief
        P3: Mixed presence → proportional to completeness
        P4: Missing a critical doc produces more disbelief than a non-critical one

    Args:
        evidence:        DocumentationEvidence with document sets.
        prior_weight:    Dirichlet prior weight W (default 2.0).
        base_rate:       Prior integrity probability (default 0.80).
        critical_weight: Evidence weight for missing critical documents
                         (default 3.0 — a missing critical doc counts as
                         3× a missing non-critical doc).

    Returns:
        DrugIntegrityOpinion for the documentation evidence dimension.
    """
    r = 0.0  # positive evidence (present documents)
    w = 0.0  # negative evidence (missing documents, weighted)

    for doc in evidence.documents_required:
        if doc in evidence.documents_present:
            r += 1.0
        else:
            if doc in evidence.documents_critical:
                w += critical_weight
            else:
                w += 1.0

    # Handle degenerate case: no required documents
    if len(evidence.documents_required) == 0:
        return DrugIntegrityOpinion.vacuous(base_rate)

    omega = Opinion.from_evidence(
        positive=r,
        negative=w,
        prior_weight=prior_weight,
        base_rate=base_rate,
    )

    return DrugIntegrityOpinion.from_opinion(omega)


# ═══════════════════════════════════════════════════════════════════
# F5 — RECALL OPINION (Definition 3e)
# ═══════════════════════════════════════════════════════════════════


def form_recall_opinion(
    status: RecallStatus,
    base_rate: float = 0.99,
) -> DrugIntegrityOpinion:
    """F5: Map batch recall status to an SL opinion.

    Direct construction per §3.3e — binary override, no evidence mapping.

    Active recall:
        ω = (0, 1, 0, 0.01) — DOGMATIC degradation.
        A recall is authoritative: the manufacturer or regulator has
        determined the batch is compromised. This is not uncertain.
        In SL, a dogmatic disbelief opinion (u = 0) overrides all
        other evidence in cumulative fusion — this is a mathematical
        property of the algebra, not an implementation choice.

    No recall:
        ω = (0, 0, 1, base_rate) — VACUOUS (maximally uncertain).
        The absence of a recall is NOT evidence of safety. A drug may
        be compromised without a recall having been issued (the recall
        process takes time, or the issue hasn't been detected yet).
        Vacuous opinions contribute no information in fusion — they
        are absorbed by other evidence sources. This is correct:
        'no recall' should not inflate integrity belief.

    Properties (proven):
        P1: Active recall → chain-level disbelief regardless of all
            other evidence (dogmatic fusion dominance property of SL)
        P2: No recall → no effect on fused opinion (vacuous absorption)
        P3: Binary — no intermediate states

    Args:
        status:    RecallStatus (ACTIVE_RECALL or NO_RECALL).
        base_rate: Prior integrity probability (default 0.99).

    Returns:
        DrugIntegrityOpinion for the recall evidence dimension.
    """
    if status == RecallStatus.ACTIVE_RECALL:
        # Dogmatic disbelief — overrides everything in fusion
        return DrugIntegrityOpinion.from_opinion(
            Opinion(0.0, 1.0, 0.0, 0.01)
        )
    else:
        # Vacuous — contributes nothing to fusion
        return DrugIntegrityOpinion.vacuous(base_rate)


# ═══════════════════════════════════════════════════════════════════
# WITHIN-NODE FUSION (Definition 4)
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class NodeEvidence:
    """Complete evidence bundle at a supply chain node.

    Collects all five evidence types. Any may be None if that evidence
    type is unavailable at this node — missing evidence is handled via
    vacuous opinions (Definition 7).
    """
    serialization: Optional[SerializationEvidence] = None
    cold_chain: Optional[ColdChainEvidence] = None
    shelf_life: Optional[ShelfLifeEvidence] = None
    documentation: Optional[DocumentationEvidence] = None
    recall: RecallStatus = RecallStatus.NO_RECALL


@dataclass(frozen=True)
class EvidenceWeights:
    """Relative importance weights for evidence types per product category.

    Weights control the effective evidence scale: a higher weight means
    more evidence counts are generated for that dimension, giving it
    proportionally more influence in cumulative fusion.

    The weights need not sum to 1 — they are relative scales, not
    probability masses.
    """
    serialization: float = 1.0
    cold_chain: float = 1.0
    shelf_life: float = 1.0
    documentation: float = 1.0
    recall: float = 1.0  # typically left at 1.0; recall uses override semantics

    @classmethod
    def for_biologic(cls) -> EvidenceWeights:
        """Weights for biologics/vaccines (cold chain critical)."""
        return cls(
            serialization=1.0,
            cold_chain=1.4,
            shelf_life=0.8,
            documentation=0.6,
            recall=1.0,
        )

    @classmethod
    def for_conventional(cls) -> EvidenceWeights:
        """Weights for conventional medications (CRT storage)."""
        return cls(
            serialization=1.2,
            cold_chain=0.6,
            shelf_life=1.0,
            documentation=1.0,
            recall=1.0,
        )

    @classmethod
    def for_advanced_therapy(cls) -> EvidenceWeights:
        """Weights for advanced therapies (ultra-cold, high sensitivity)."""
        return cls(
            serialization=0.8,
            cold_chain=1.6,
            shelf_life=0.8,
            documentation=0.6,
            recall=1.0,
        )
