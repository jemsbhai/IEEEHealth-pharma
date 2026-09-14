"""
Supply Chain Simulator — Normal operation + failure injection.

Generates realistic evidence bundles for Monte Carlo evaluation of the
drug integrity algebra. Supports three product categories, seven failure
scenarios, and reproducible seeded randomness.

Architecture:
    1. generate_normal_evidence()  — realistic evidence per node under normal ops
    2. FailureScenario.apply()     — injects failures into normal evidence
    3. run_trial()                 — single trial: generate evidence → evaluate chain
    4. run_experiment()            — N Monte Carlo trials with statistics

All randomness uses numpy.random.Generator with explicit seeds for
reproducibility (lab-runner protocol: every result must be reproducible).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from models.drug_integrity_opinion import (
    ColdChainEvidence,
    DocumentationEvidence,
    EvidenceWeights,
    NodeEvidence,
    RecallStatus,
    SerializationEvidence,
    ShelfLifeEvidence,
    DEFAULT_CRITICAL_DOCUMENTS,
    DEFAULT_REQUIRED_DOCUMENTS,
)
from models.chain_integrity import (
    ChainEvaluationResult,
    evaluate_path,
    localize_failure,
)
from data.supply_chain_dag import SupplyChainDAG


# ═══════════════════════════════════════════════════════════════════
# PRODUCT CATEGORIES
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ProductProfile:
    """Physical and regulatory properties of a drug product category."""
    name: str
    cold_chain_min: float          # °C
    cold_chain_max: float          # °C
    shelf_life_manufactured: int   # days
    shelf_life_min_pharmacy: int   # days
    cold_chain_noise_std: float    # °C, normal operation noise
    weights: EvidenceWeights


INSULIN = ProductProfile(
    name="Insulin (refrigerated biologic)",
    cold_chain_min=2.0, cold_chain_max=8.0,
    shelf_life_manufactured=730, shelf_life_min_pharmacy=180,
    cold_chain_noise_std=0.8,
    weights=EvidenceWeights.for_biologic(),
)

AMOXICILLIN = ProductProfile(
    name="Amoxicillin tablets (CRT conventional)",
    cold_chain_min=15.0, cold_chain_max=25.0,
    shelf_life_manufactured=730, shelf_life_min_pharmacy=180,
    cold_chain_noise_std=1.5,
    weights=EvidenceWeights.for_conventional(),
)

MRNA_THERAPY = ProductProfile(
    name="mRNA-based therapy (frozen advanced)",
    cold_chain_min=-25.0, cold_chain_max=-15.0,
    shelf_life_manufactured=180, shelf_life_min_pharmacy=30,
    cold_chain_noise_std=1.0,
    weights=EvidenceWeights.for_advanced_therapy(),
)

ALL_PRODUCTS = [INSULIN, AMOXICILLIN, MRNA_THERAPY]


# ═══════════════════════════════════════════════════════════════════
# NORMAL EVIDENCE GENERATION
# ═══════════════════════════════════════════════════════════════════


def generate_normal_evidence(
    product: ProductProfile,
    n_packages: int = 100,
    n_readings: int = 100,
    rng: Optional[np.random.Generator] = None,
) -> NodeEvidence:
    """Generate realistic evidence for a node under normal operation.

    Normal operation parameters (from domain expert document):
    - Serialization: 95–100% scan rate, 0–0.1% failure rate
    - Cold chain: Gaussian noise around range midpoint
    - Shelf life: manufactured with full life, consumed in 6–12 months
    - Documentation: 100% complete
    - No recalls
    """
    if rng is None:
        rng = np.random.default_rng()

    # Serialization: high scan rate, near-zero failures
    scan_rate = rng.uniform(0.95, 1.0)
    n_scanned = int(round(scan_rate * n_packages))
    n_failures = int(round(rng.uniform(0, 0.001) * n_scanned))
    n_success = n_scanned - n_failures

    serialization = SerializationEvidence(
        scanned_success=n_success,
        scanned_failure=n_failures,
        total_packages=n_packages,
    )

    # Cold chain: Gaussian around midpoint, clipped to stay in range
    midpoint = (product.cold_chain_min + product.cold_chain_max) / 2.0
    temps = rng.normal(midpoint, product.cold_chain_noise_std, size=n_readings)
    # Normal ops: clip to keep within range (equipment works correctly)
    temps = np.clip(temps, product.cold_chain_min + 0.1, product.cold_chain_max - 0.1)

    cold_chain = ColdChainEvidence(
        readings=tuple(temps.tolist()),
        required_min=product.cold_chain_min,
        required_max=product.cold_chain_max,
        expected_readings=n_readings,
    )

    # Shelf life: consumed after 6–12 months of the manufactured life
    months_consumed = rng.uniform(6, 12)
    remaining = product.shelf_life_manufactured - int(months_consumed * 30.44)
    remaining = max(remaining, product.shelf_life_min_pharmacy + 30)  # normal ops always above min

    shelf_life = ShelfLifeEvidence(
        remaining_days=remaining,
        regulatory_min=product.shelf_life_min_pharmacy,
    )

    # Documentation: 100% complete in normal operations
    documentation = DocumentationEvidence(
        documents_present=DEFAULT_REQUIRED_DOCUMENTS,
        documents_required=DEFAULT_REQUIRED_DOCUMENTS,
        documents_critical=DEFAULT_CRITICAL_DOCUMENTS,
    )

    return NodeEvidence(
        serialization=serialization,
        cold_chain=cold_chain,
        shelf_life=shelf_life,
        documentation=documentation,
        recall=RecallStatus.NO_RECALL,
    )


# ═══════════════════════════════════════════════════════════════════
# FAILURE SCENARIOS (F1–F7)
# ═══════════════════════════════════════════════════════════════════


class FailureType(Enum):
    """Identifiers for the seven failure scenarios."""
    F1_MILD_EXCURSION = auto()
    F2_SEVERE_EXCURSION = auto()
    F3_COUNTERFEIT = auto()
    F4_SCAN_OMISSION = auto()
    F5_EXPIRED_BATCH = auto()
    F6_MISSING_DOCS = auto()
    F7_CASCADING = auto()


@dataclass(frozen=True)
class FailureScenario:
    """A failure scenario to inject into the supply chain.

    Attributes:
        failure_type:         Which failure from F1–F7.
        target_node:          Node ID where the failure occurs (for F7, the
                              node named here is where the first sub-failure
                              is injected; see localization_targets).
        params:               Failure-specific parameters.
        localization_targets: Nodes credited as a correct localization. Empty
                              (the default) means (target_node,). F7 injects
                              failures at two nodes and lists both, so that
                              flagging either compromised node counts as
                              correct (EXP-006, H6.1).
    """
    failure_type: FailureType
    target_node: str
    params: Dict[str, Any] = field(default_factory=dict)
    localization_targets: Tuple[str, ...] = ()

    def localization_target_set(self) -> Tuple[str, ...]:
        """Return the nodes credited as correct localization for this scenario."""
        if self.localization_targets:
            return self.localization_targets
        return (self.target_node,)


def localization_accuracy_from_trials(
    detected: Sequence[bool],
    localized: Sequence[Sequence[str]],
    targets: Sequence[str],
) -> float:
    """Fraction of detected trials whose localized nodes include any target.

    Shared by run_experiment (SL flagged-node sets) and by
    scripts/run_experiments.py (baselines pass their single weakest node as
    a one-element list), so every method is scored by the same rule.
    Returns 0.0 when no trial was detected. Trials that were not detected
    do not count toward the denominator (LA is conditional on detection).
    """
    n_detected = 0
    n_correct = 0
    for det, nodes in zip(detected, localized):
        if not det:
            continue
        n_detected += 1
        if any(n in nodes for n in targets):
            n_correct += 1
    return n_correct / n_detected if n_detected else 0.0


def apply_failure(
    evidence: Dict[str, NodeEvidence],
    scenario: FailureScenario,
    product: ProductProfile,
    rng: Optional[np.random.Generator] = None,
) -> Dict[str, NodeEvidence]:
    """Apply a failure scenario to a set of node evidence bundles.

    Returns a new dict with the failure injected (does not mutate input).
    """
    if rng is None:
        rng = np.random.default_rng()

    result = {k: copy.deepcopy(v) for k, v in evidence.items()}
    node = scenario.target_node

    if node not in result:
        raise ValueError(f"Target node {node!r} not in evidence dict")

    base = result[node]

    if scenario.failure_type == FailureType.F1_MILD_EXCURSION:
        result[node] = _inject_temperature_excursion(
            base, product,
            duration_fraction=scenario.params.get("duration_fraction", 0.05),
            magnitude_above_max=scenario.params.get("magnitude", 2.0),
            rng=rng,
        )

    elif scenario.failure_type == FailureType.F2_SEVERE_EXCURSION:
        result[node] = _inject_temperature_excursion(
            base, product,
            duration_fraction=scenario.params.get("duration_fraction", 0.5),
            magnitude_above_max=scenario.params.get("magnitude", 17.0),
            rng=rng,
        )

    elif scenario.failure_type == FailureType.F3_COUNTERFEIT:
        counterfeit_frac = scenario.params.get("counterfeit_fraction", 0.05)
        result[node] = _inject_counterfeit(base, counterfeit_frac, rng)

    elif scenario.failure_type == FailureType.F4_SCAN_OMISSION:
        scan_rate = scenario.params.get("scan_rate", 0.20)
        result[node] = _inject_scan_omission(base, scan_rate, rng)

    elif scenario.failure_type == FailureType.F5_EXPIRED_BATCH:
        remaining = scenario.params.get("remaining_days", 90)
        result[node] = _inject_expired_batch(base, remaining)

    elif scenario.failure_type == FailureType.F6_MISSING_DOCS:
        missing = scenario.params.get("missing", ["certificate_of_analysis"])
        result[node] = _inject_missing_docs(base, missing)

    elif scenario.failure_type == FailureType.F7_CASCADING:
        # Cascading: apply multiple sub-scenarios
        sub_scenarios = scenario.params.get("sub_scenarios", [])
        for sub in sub_scenarios:
            result = apply_failure(result, sub, product, rng)

    return result


def _inject_temperature_excursion(
    base: NodeEvidence,
    product: ProductProfile,
    duration_fraction: float,
    magnitude_above_max: float,
    rng: np.random.Generator,
) -> NodeEvidence:
    """Replace a fraction of cold chain readings with excursion values."""
    if base.cold_chain is None:
        return base

    readings = list(base.cold_chain.readings)
    n_excursion = max(1, int(len(readings) * duration_fraction))

    # Insert excursion at a random contiguous block
    start = rng.integers(0, max(1, len(readings) - n_excursion))
    excursion_temp = product.cold_chain_max + magnitude_above_max
    for i in range(start, min(start + n_excursion, len(readings))):
        readings[i] = excursion_temp + rng.normal(0, 0.5)

    new_cc = ColdChainEvidence(
        readings=tuple(readings),
        required_min=base.cold_chain.required_min,
        required_max=base.cold_chain.required_max,
        expected_readings=base.cold_chain.expected_readings,
    )
    return NodeEvidence(
        serialization=base.serialization,
        cold_chain=new_cc,
        shelf_life=base.shelf_life,
        documentation=base.documentation,
        recall=base.recall,
    )


def _inject_counterfeit(
    base: NodeEvidence, fraction: float, rng: np.random.Generator,
) -> NodeEvidence:
    """Replace serialization with counterfeit-fraction failures."""
    if base.serialization is None:
        return base
    total = base.serialization.total_packages
    n_counterfeit = max(1, int(total * fraction))
    n_legit = total - n_counterfeit
    # Assume all are scanned (counterfeits detected by scan)
    return NodeEvidence(
        serialization=SerializationEvidence(
            scanned_success=n_legit,
            scanned_failure=n_counterfeit,
            total_packages=total,
        ),
        cold_chain=base.cold_chain,
        shelf_life=base.shelf_life,
        documentation=base.documentation,
        recall=base.recall,
    )


def _inject_scan_omission(
    base: NodeEvidence, scan_rate: float, rng: np.random.Generator,
) -> NodeEvidence:
    """Reduce scan coverage to specified rate."""
    if base.serialization is None:
        return base
    total = base.serialization.total_packages
    n_scanned = max(1, int(total * scan_rate))
    # Assume scanned items are all good (the issue is COVERAGE, not counterfeits)
    return NodeEvidence(
        serialization=SerializationEvidence(
            scanned_success=n_scanned,
            scanned_failure=0,
            total_packages=total,
        ),
        cold_chain=base.cold_chain,
        shelf_life=base.shelf_life,
        documentation=base.documentation,
        recall=base.recall,
    )


def _inject_expired_batch(base: NodeEvidence, remaining_days: int) -> NodeEvidence:
    """Override shelf life with specified remaining days."""
    reg_min = base.shelf_life.regulatory_min if base.shelf_life else 180
    return NodeEvidence(
        serialization=base.serialization,
        cold_chain=base.cold_chain,
        shelf_life=ShelfLifeEvidence(remaining_days=remaining_days, regulatory_min=reg_min),
        documentation=base.documentation,
        recall=base.recall,
    )


def _inject_missing_docs(base: NodeEvidence, missing: List[str]) -> NodeEvidence:
    """Remove specified documents from the documentation evidence."""
    if base.documentation is None:
        return base
    present = base.documentation.documents_present - frozenset(missing)
    return NodeEvidence(
        serialization=base.serialization,
        cold_chain=base.cold_chain,
        shelf_life=base.shelf_life,
        documentation=DocumentationEvidence(
            documents_present=present,
            documents_required=base.documentation.documents_required,
            documents_critical=base.documentation.documents_critical,
        ),
        recall=base.recall,
    )


# ═══════════════════════════════════════════════════════════════════
# PRE-BUILT FAILURE SCENARIOS (from configs/base.yaml)
# ═══════════════════════════════════════════════════════════════════


def get_standard_scenarios() -> Dict[str, FailureScenario]:
    """Return the seven standard failure scenarios from the paper."""
    return {
        "F1": FailureScenario(
            FailureType.F1_MILD_EXCURSION, "distributor_a",
            {"duration_fraction": 0.05, "magnitude": 2.0},
        ),
        "F2": FailureScenario(
            FailureType.F2_SEVERE_EXCURSION, "distributor_a",
            {"duration_fraction": 0.5, "magnitude": 17.0},
        ),
        "F3": FailureScenario(
            FailureType.F3_COUNTERFEIT, "importer",
            {"counterfeit_fraction": 0.05},
        ),
        "F4": FailureScenario(
            FailureType.F4_SCAN_OMISSION, "central_warehouse",
            {"scan_rate": 0.20},
        ),
        "F5": FailureScenario(
            FailureType.F5_EXPIRED_BATCH, "distributor_a",
            {"remaining_days": 90},
        ),
        "F6": FailureScenario(
            FailureType.F6_MISSING_DOCS, "importer",
            {"missing": ["certificate_of_analysis"]},
        ),
        "F7": FailureScenario(
            FailureType.F7_CASCADING, "central_warehouse",
            {"sub_scenarios": [
                FailureScenario(
                    FailureType.F1_MILD_EXCURSION, "central_warehouse",
                    {"duration_fraction": 0.05, "magnitude": 2.0},
                ),
                FailureScenario(
                    FailureType.F4_SCAN_OMISSION, "distributor_a",
                    {"scan_rate": 0.20},
                ),
            ]},
            localization_targets=("central_warehouse", "distributor_a"),
        ),
    }


# ═══════════════════════════════════════════════════════════════════
# TRIAL AND EXPERIMENT RUNNERS
# ═══════════════════════════════════════════════════════════════════


@dataclass
class TrialResult:
    """Result of a single Monte Carlo trial."""
    trial_id: int
    product: str
    scenario: Optional[str]          # None for normal operation
    chain_result: ChainEvaluationResult
    integrity_score: float
    uncertainty: float
    weakest_node: str
    detected: bool                   # was the failure flagged?
    localized_nodes: List[str]       # which nodes were flagged


@dataclass
class ExperimentResult:
    """Aggregated results across all trials for one scenario × product."""
    product: str
    scenario: Optional[str]
    n_trials: int
    trials: List[TrialResult]

    # Aggregate statistics
    mean_integrity: float = 0.0
    std_integrity: float = 0.0
    mean_uncertainty: float = 0.0
    detection_rate: float = 0.0
    false_positive_rate: float = 0.0
    localization_accuracy: float = 0.0


def run_trial(
    trial_id: int,
    path: List[str],
    product: ProductProfile,
    scenario: Optional[FailureScenario] = None,
    scenario_name: Optional[str] = None,
    rng: Optional[np.random.Generator] = None,
    detection_threshold: float = 0.5,
    uncertainty_threshold: float = 0.3,
) -> TrialResult:
    """Run a single trial: generate evidence → optionally inject failure → evaluate.

    Args:
        trial_id:              Trial identifier.
        path:                  Supply chain path (node IDs).
        product:               Product profile.
        scenario:              Failure scenario to inject (None for normal).
        scenario_name:         Human-readable scenario name.
        rng:                   Random number generator.
        detection_threshold:   P(ω) below which = flagged.
        uncertainty_threshold: u above which = flagged for review.

    Returns:
        TrialResult with chain evaluation and detection status.
    """
    if rng is None:
        rng = np.random.default_rng()

    # Generate normal evidence for all nodes
    evidence = {
        node_id: generate_normal_evidence(product, rng=rng)
        for node_id in path
    }

    # Inject failure if specified
    if scenario is not None:
        evidence = apply_failure(evidence, scenario, product, rng)

    # Evaluate the chain
    chain_result = evaluate_path(
        path=path,
        node_evidence=evidence,
        weights=product.weights,
    )

    # Detection: is integrity below threshold OR uncertainty above threshold?
    detected = (
        chain_result.integrity_score < detection_threshold
        or chain_result.uncertainty > uncertainty_threshold
    )

    # Localization
    localized = localize_failure(
        chain_result,
        integrity_threshold=detection_threshold,
        uncertainty_threshold=uncertainty_threshold,
    )

    return TrialResult(
        trial_id=trial_id,
        product=product.name,
        scenario=scenario_name,
        chain_result=chain_result,
        integrity_score=chain_result.integrity_score,
        uncertainty=chain_result.uncertainty,
        weakest_node=chain_result.weakest_node,
        detected=detected,
        localized_nodes=localized,
    )


def run_experiment(
    path: List[str],
    product: ProductProfile,
    n_trials: int = 1000,
    scenario: Optional[FailureScenario] = None,
    scenario_name: Optional[str] = None,
    seed: int = 42,
    detection_threshold: float = 0.5,
    uncertainty_threshold: float = 0.3,
) -> ExperimentResult:
    """Run N Monte Carlo trials and compute aggregate statistics.

    Args:
        path:                  Supply chain path.
        product:               Product profile.
        n_trials:              Number of trials.
        scenario:              Failure scenario (None for normal baseline).
        scenario_name:         Human-readable name.
        seed:                  Random seed for reproducibility.
        detection_threshold:   P(ω) below which = failure detected.
        uncertainty_threshold: u above which = flagged.

    Returns:
        ExperimentResult with per-trial results and aggregate stats.
    """
    rng = np.random.default_rng(seed)
    trials: List[TrialResult] = []

    for i in range(n_trials):
        trial = run_trial(
            trial_id=i,
            path=path,
            product=product,
            scenario=scenario,
            scenario_name=scenario_name,
            rng=rng,
            detection_threshold=detection_threshold,
            uncertainty_threshold=uncertainty_threshold,
        )
        trials.append(trial)

    # Compute aggregates
    scores = [t.integrity_score for t in trials]
    uncertainties = [t.uncertainty for t in trials]
    detections = [t.detected for t in trials]

    is_failure = scenario is not None

    if is_failure:
        detection_rate = sum(detections) / n_trials
        false_positive_rate = 0.0  # by definition, no FP in failure trials

        # Localization accuracy: of detected trials, how many flagged at
        # least one of the nodes where a failure was actually injected?
        # (EXP-006: F7 injects at two nodes and credits either.)
        localization_accuracy = localization_accuracy_from_trials(
            detected=[t.detected for t in trials],
            localized=[t.localized_nodes for t in trials],
            targets=scenario.localization_target_set(),
        )
    else:
        detection_rate = 0.0
        false_positive_rate = sum(detections) / n_trials
        localization_accuracy = 0.0

    result = ExperimentResult(
        product=product.name,
        scenario=scenario_name,
        n_trials=n_trials,
        trials=trials,
        mean_integrity=float(np.mean(scores)),
        std_integrity=float(np.std(scores)),
        mean_uncertainty=float(np.mean(uncertainties)),
        detection_rate=detection_rate,
        false_positive_rate=false_positive_rate,
        localization_accuracy=localization_accuracy,
    )

    return result
