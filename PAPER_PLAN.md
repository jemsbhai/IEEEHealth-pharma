# IEEE HealthCom 2026 — Paper Plan

**Working Title:** "Drug Integrity Algebra: Quantifying End-to-End Pharmaceutical Safety with Subjective Logic"

**Authors:** Muntaser Syed, [Collaborator], Marius Silaghi
**Target:** IEEE HealthCom 2026, Best Paper
**Conference:** IEEE International Conference on E-health Networking, Application & Services
**Location:** New York City, October 19–21, 2026
**Format:** 6 pages IEEE two-column (10pt), +2 pages at $100/page
**Submission:** EDAS, PDF only

---

## 1. Core Argument

Pharmaceutical supply chains are safety-critical pipelines where drug integrity
must be maintained from manufacturer to patient. Systems like the UAE's Tatmeen
platform, the EU Falsified Medicines Directive (FMD), and the US Drug Supply
Chain Security Act (DSCSA) track drugs via GS1 serialization and enforce cold
chain monitoring. However, all of these systems operate on **binary pass/fail
thresholds** at each supply chain node independently.

This architecture has three critical weaknesses that directly endanger patients:

1. **Information destruction.** A temperature of 7.9°C on a 2–8°C product and
   one of 2.1°C both produce "pass," but carry vastly different risk signals.
   Thresholds discard continuous evidence.

2. **No uncertainty quantification.** A node scanning 100% of packages and one
   scanning 10% produce the same "compliant" status if all scanned items pass,
   but the *confidence* in that claim differs by an order of magnitude.

3. **No chain-level composition.** Each node reports independently. There is no
   principled mechanism to ask: "Given everything we know across the entire chain,
   what is our confidence that this batch is safe at the point of dispensing?"

We propose a **drug integrity algebra** grounded in Jøsang's Subjective Logic
that addresses all three weaknesses. The algebra maps heterogeneous evidence at
each supply chain node to SL opinions with explicit uncertainty, fuses them
within each node, and propagates/discounts them across the supply chain DAG —
producing an end-to-end integrity score with calibrated uncertainty.

---

## 2. Contribution Delineation

| Paper | Venue | Domain | SL Application |
|-------|-------|--------|---------------|
| Compliance Algebra (GDPR) | companion paper | Data governance | Regulatory compliance for data processing |
| Compliance Algebra (IoT) | IEEE WF-IoT 2026 | Smart meters/IoT | Energy data governance |
| HIPAA Compliance Algebra | HealthCom 2026 Paper 1 | Clinical data exchange | FHIR R4 privacy compliance |
| **Drug Integrity Algebra** | **HealthCom 2026 Paper 2** | **Pharma supply chain** | **Physical drug safety assurance** |

### What's EXCLUSIVELY in this paper:

| # | Contribution | In WF-IoT? | In HealthCom P1? |
|---|-------------|------------|------------------|
| C1 | Five pharma evidence-to-opinion formation functions | No | No |
| C2 | Supply chain DAG model (Tatmeen-grounded) | No | No |
| C3 | Chain-level fusion/discounting protocol for drug integrity | No | No |
| C4 | Simulation evaluation with failure injection | No | No |
| C5 | Industry-grounded domain model (UAE pharma co-author) | No | No |

The SL primitives (Opinion, cumulative_fuse, deduce, trust_discount,
decay_opinion) are
CITED from jsonld-ex — never re-derived. The contribution is the formalization
of pharmaceutical supply chain compliance as an SL opinion algebra and the
empirical demonstration of its superiority over threshold-based systems.

---

## 3. Formal Model

### 3.1 Supply Chain DAG (Definition 1)

A pharmaceutical supply chain is a directed acyclic graph G = (N, E) where:
- N = {n_1, ..., n_k} are custody nodes (manufacturer, importer, warehouse,
  distributor, pharmacy)
- E ⊆ N × N are custody transfer edges (each transfer is a Tatmeen EPCIS event)
- Each node n_i has a node type τ(n_i) ∈ {MANUFACTURER, IMPORTER, WAREHOUSE,
  DISTRIBUTOR, PHARMACY}
- Each edge (n_i, n_j) carries a transfer record with timestamp and conditions

A **product path** π = (n_1, ..., n_m) is a source-to-sink path in G representing
the complete journey of a batch from manufacture to dispensing.

### 3.2 Evidence Taxonomy (Definition 2)

At each node n_i, five evidence types are observable:

| ID | Evidence Type | Raw Data | Domain |
|----|--------------|----------|--------|
| E1 | Serialization | Scan results per package | {success, failure, unscanned} |
| E2 | Cold Chain | Temperature time-series | Continuous (°C) with target range |
| E3 | Shelf Life | Remaining days vs. regulatory minimum | ℕ (days) |
| E4 | Documentation | Checklist of required documents | {present, absent} per document |
| E5 | Recall Status | Batch recall flag | {active_recall, no_recall} |

### 3.3 Evidence-to-Opinion Formation Functions (Definition 3)

Each evidence type maps to an SL opinion ω = (b, d, u, a) via a formally
defined formation function.

#### F1: Serialization Opinion — φ_ser(s, f, m, N)

Given s successful scans, f failed scans, m unscanned packages, N total:

    b = s / (s + f + W)
    d = f / (s + f + W)
    u = W / (s + f + W)
    a = a_ser (prior, default 0.99 for regulated supply chains)

where W = κ · (m / N) is a non-evidence weight controlled by parameter κ
(default κ = 2, following the standard Dirichlet non-evidence weight).

Properties:
- Full scan, all pass (m=0, f=0): b ≈ 1, u ≈ 0
- Partial scan, all pass (m>0, f=0): b > 0, u > 0 (proportional to unscanned fraction)
- Any failure: d > 0 (proportional to failure count)
- No scan at all (s=0, f=0, m=N): ω = vacuous (b=0, d=0, u=1)

#### F2: Cold Chain Opinion — φ_cc(T, [T_min, T_max], Δt)

Given temperature time-series T = {(t_i, T_i)} sampled at intervals Δt, and
required range [T_min, T_max]:

Let:
- f_in = fraction of readings in range: |{i : T_min ≤ T_i ≤ T_max}| / |T|
- σ_exc = normalized excursion severity: Σ max(0, T_i - T_max, T_min - T_i) / (|T| · R)
  where R = T_max - T_min (range width, for normalization)
- g = sensor gap fraction: (total_gaps / total_expected_readings)

Then:
    b = f_in · (1 - g)
    d = min(1, σ_exc) · (1 - g)
    u = 1 - b - d    (absorbs sensor gaps and mild ambiguity)
    a = a_cc (prior, default 0.95)

Constraint enforcement: if b + d > 1 due to numerical edge cases, normalize
(b, d) proportionally before computing u.

Properties:
- Perfect cold chain, no gaps: b ≈ 1, d ≈ 0, u ≈ 0
- Mild excursion (e.g., 9°C briefly on 2–8°C range): b decreases, d increases slightly
- Severe excursion (e.g., 25°C for 6 hours): d dominates
- Sensor offline for half the period: u increases (gap fraction)

#### F3: Shelf-Life Opinion — φ_sl(r, m_min)

Given remaining shelf life r (days) and regulatory minimum m_min (days):

Let ratio = r / m_min. Then:
    If ratio ≥ 2.0:   b = 1.0, d = 0.0, u = 0.0  (ample margin)
    If 1.0 ≤ ratio < 2.0:  b = ratio - 1.0, d = 0.0, u = 2.0 - ratio  (approaching threshold)
    If 0.5 ≤ ratio < 1.0:  b = 0.0, d = 1.0 - ratio, u = ratio - 0.5 (below threshold)
    If ratio < 0.5:    b = 0.0, d = 1.0, u = 0.0  (critically expired)
    a = a_sl (prior, default 0.9)

Properties:
- Monotonically degrading: b can only decrease, d can only increase, as r decreases
- Continuous transitions, no cliff edges
- At the regulatory minimum (ratio = 1.0): ω = (0, 0, 1, a_sl) — maximum uncertainty,
  reflecting the decision point

#### F4: Documentation Opinion — φ_doc(D_present, D_required, D_critical)

Given sets of present documents D_present, required documents D_required, and
critical subset D_critical ⊆ D_required:

Let:
- c_total = |D_present ∩ D_required| / |D_required|  (total completeness)
- c_crit = |D_present ∩ D_critical| / |D_critical|   (critical completeness)
- w_crit = 0.6  (weight for critical documents)

Then:
    completeness = w_crit · c_crit + (1 - w_crit) · c_total
    b = completeness
    d = (1 - c_crit) · w_crit    (missing critical documents contribute to disbelief)
    u = 1 - b - d
    a = a_doc (prior, default 0.8)

Critical documents for pharmaceutical import (from collaborator's domain knowledge):
- Certificate of Analysis (CoA)
- GMP Certificate
- Batch Release Certificate
- Bioequivalence Study (for generics)
- Import License

#### F5: Recall Opinion — φ_rec(status)

    If status = ACTIVE_RECALL:  ω = (0, 1, 0, 0)   (dogmatic violation)
    If status = NO_RECALL:      ω = (0, 0, 1, 0.99) (vacuous — absence of recall is NOT evidence of safety)

Properties:
- Active recall is an OVERRIDE — propagates through all fusion and produces
  chain-level disbelief regardless of other evidence
- No recall contributes no information — it is absorbed by fusion with other
  evidence types. This is correct: the absence of a recall does not make a drug safe.

### 3.4 Within-Node Fusion (Definition 4)

At each node n_i, the five evidence opinions fuse into a single node-level
drug integrity opinion:

    ω_node(n_i) = cumulative_fuse(
        weight(E1) · φ_ser(...),
        weight(E2) · φ_cc(...),
        weight(E3) · φ_sl(...),
        weight(E4) · φ_doc(...),
        weight(E5) · φ_rec(...)
    )

Evidence weights depend on the product category:
- Biologics/vaccines (cold chain critical): w_cc = 0.35, w_ser = 0.25, w_sl = 0.20, w_doc = 0.15, w_rec = 0.05
- Standard medications (CRT): w_ser = 0.30, w_sl = 0.25, w_doc = 0.25, w_cc = 0.15, w_rec = 0.05
- Recall weight is low because its OVERRIDE semantics are handled separately

Note: "weight" here means pre-fusion scaling. We implement this by adjusting
the non-evidence weight W in the Dirichlet mapping, not by multiplying the
opinion components directly (which would violate the b+d+u=1 constraint).

### 3.5 Cross-Node Propagation (Definition 5)

(Revised 2026-09-14 to match the implementation and the submitted paper;
the original plan used serial trust discounting, replaced by deduction in
EXP-002a because trust discounting maps upstream disbelief to downstream
uncertainty, whereas drug compromise is irreversible.)

Along a product path π = (n_1, ..., n_m), the chain-level integrity opinion
is computed by a right fold of the deduction operator (Jøsang §9.4) with
causal conditionals:

    ω_chain = ω_{n_1} ◎ (ω_{n_2} ◎ ( ... ◎ ω_{n_m}))

where at each step ω_{Y|x} = the remaining chain opinion (upstream node
safe) and ω_{Y|¬x} = (0, 1, 0, 0.01) (upstream node compromised). The
conditional's base rate is immaterial (EXP-008: a dogmatic opinion
projects to 0 for any a).

For multi-path supply chains (a batch passes through parallel distributors),
paths are computed independently and then fused:

    ω_total = cumulative_fuse(ω_chain(π_1), ω_chain(π_2), ...)

### 3.6 Temporal Decay (Definition 6)

Evidence ages. A cold chain reading from 2 hours ago is more informative than
one from 2 days ago. At evaluation time t_eval, each evidence opinion formed
at time t_obs is decayed:

    ω_decayed = decay_opinion(ω, elapsed=(t_eval - t_obs), half_life=τ_evidence)

Evidence-type-specific half-lives (from domain analysis):
- Serialization: τ = 30 days (scan validity is long-lived)
- Cold chain: τ = 4 hours (temperature conditions can change rapidly)
- Shelf life: no decay (already time-dependent by construction)
- Documentation: τ = 180 days (documents can be revoked or updated)
- Recall: no decay (binary status is current or not)

### 3.7 Missing Evidence Handling (Definition 7)

When an evidence type is entirely absent at a node (e.g., no cold chain sensor
data available), the corresponding opinion is set to VACUOUS:

    ω_missing = Opinion(b=0, d=0, u=1, a=a_evidence)

This propagates honestly through fusion: the node's overall opinion has
higher uncertainty, which then propagates through the chain via trust
discounting. The system says "I don't know" rather than silently passing
or silently failing.

### 3.8 Provable Properties

The algebra satisfies these properties (proofs in paper appendix):

**P1 (Monotonicity):** Adding positive evidence at any node can only increase
the chain-level belief or decrease uncertainty. Never the reverse.

**P2 (Compositionality):** The chain-level opinion decomposes into node-level
opinions. Modifying evidence at node n_i affects the chain opinion only through
ω_node(n_i) and its downstream propagation.

**P3 (Sensitivity):** The chain-level opinion responds proportionally to evidence
strength. A severe cold chain violation produces a stronger chain-level response
than a mild one, regardless of the threshold boundary.

**P4 (Uncertainty conservation):** Missing evidence at any node produces
quantifiable uncertainty in the chain-level opinion. No information is silently
lost or fabricated.

**P5 (Override):** An active recall at any node produces chain-level disbelief
regardless of all other evidence (via the dogmatic fusion property of SL).

---

## 4. Evaluation Design

### 4.1 Simulated Supply Chain Topology

Based on the collaborator's domain knowledge and Tatmeen public architecture:

```
MANUFACTURER (overseas, FDA/EMA-approved)
     │
     ▼
IMPORTER (UAE, GLN-registered, Tatmeen-onboarded)
     │
     ▼
CENTRAL WAREHOUSE (Abu Dhabi, three cold chain zones)
     │
     ├──────────────────┐
     ▼                  ▼
DISTRIBUTOR A        DISTRIBUTOR B
(Dubai region)       (Northern Emirates)
     │                  │
     ├──────┐           │
     ▼      ▼           ▼
HOSPITAL  PHARMACY A  PHARMACY B
```

Three product categories simulated:
- **Product P1:** Insulin (refrigerated, 2–8°C, biologic)
- **Product P2:** Amoxicillin tablets (CRT, 15–25°C, conventional)
- **Product P3:** mRNA-based therapy (frozen, -15 to -25°C, advanced therapy)

### 4.2 Normal Operation Profiles

For each product category, we simulate 1000 normal supply chain traversals
with realistic parameters:
- Serialization: 95–100% scan rate per node, 0–0.1% failure rate
- Cold chain: Gaussian noise around midpoint of required range, σ = 1°C
- Shelf life: manufactured with 24 months, consumed in 6–12 months
- Documentation: 100% complete for normal operations
- No recalls

### 4.3 Failure Injection Scenarios

| ID | Failure Type | Injection Point | Parameters |
|----|-------------|----------------|------------|
| F1 | Mild temperature excursion | Distributor transit | 30 min at T_max + 2°C |
| F2 | Severe temperature excursion | Distributor transit | 6 hours at T_max + 17°C |
| F3 | Counterfeit insertion | Importer | 5% packages with mismatched GTIN |
| F4 | Partial scan omission | Warehouse | Only 20% of packages scanned |
| F5 | Expired batch forwarding | Distributor | 90 days remaining (min = 180) |
| F6 | Documentation gap | Importer | Missing Certificate of Analysis |
| F7 | Cascading failure | Warehouse + Distributor | F1 at warehouse + F4 at distributor |

Each scenario runs 1000 Monte Carlo trials with randomized timing and
magnitude within the specified parameters.

### 4.4 Baselines

**B1: Rule-Based Threshold (industry standard)**
Binary pass/fail at each node, AND-aggregation across chain.
This is what Tatmeen effectively does today.

**B2: Weighted Scoring (common risk management)**
Continuous scores per evidence type (0–1), weighted average per node,
minimum across chain. No uncertainty modeling.

**B3: Bayesian Network**
Probabilistic graphical model over the same DAG with conditional
probability tables. Same evidence inputs, but no SL uncertainty
decomposition (belief/disbelief/uncertainty not distinguished).

### 4.5 Metrics

| Metric | What It Measures |
|--------|-----------------|
| Detection Rate (DR) | Fraction of injected failures correctly flagged (sensitivity) |
| False Positive Rate (FPR) | Fraction of normal operations incorrectly flagged |
| Localization Accuracy (LA) | Fraction of detected failures where the correct node is identified |
| Uncertainty Calibration (UC) | Correlation between reported uncertainty and actual error rate |
| Information Retention (IR) | Entropy of output distributions (higher = more information preserved) |
| Gradual Degradation Detection (GDD) | Can the system track a slow shelf-life squeeze before threshold breach? |

### 4.6 Ablations

**A1: Evidence type ablation** — remove each evidence type in turn; which
contributes most to detection?

**A2: Fusion operator ablation** — replace cumulative_fuse with averaging_fuse;
does the associative property matter?

**A3: Temporal decay ablation** — remove temporal decay; does staleness matter?

**A4: Cross-node propagation ablation** — replace the deduce chain with
independent node evaluation; does chain-level reasoning help?

---

## 5. Paper Structure (Target: 8 pages = 6 + 2)

### Section 1: Introduction (0.75 pages)
- Drug integrity as a patient safety problem
- Current binary threshold systems (Tatmeen, DSCSA, FMD) and their limitations
- Contribution summary (5 items)

### Section 2: Background & Related Work (1.0 pages)
- Pharmaceutical supply chain monitoring (Tatmeen, DSCSA, FMD)
- IoT cold chain monitoring (sensor architectures, threshold-based alerts)
- Blockchain for pharma (related but orthogonal — we address the assessment
  layer, not the ledger layer)
- Subjective Logic primer (opinion, fusion, discounting — cite Jøsang 2016)
- Gap: no prior work applies opinion algebras to pharmaceutical supply chains

### Section 3: Drug Integrity Algebra (2.5 pages)
- §3.1 Supply chain DAG model (Definition 1)
- §3.2 Evidence taxonomy (Definition 2)
- §3.3 Evidence-to-opinion formation functions F1–F5 (Definition 3)
- §3.4 Within-node fusion (Definition 4)
- §3.5 Cross-node propagation (Definition 5)
- §3.6 Temporal decay & missing evidence (Definitions 6–7)
- §3.7 Properties (P1–P5, proof sketches)

### Section 4: Evaluation (2.0 pages)
- §4.1 Simulation setup (topology, products, normal profiles)
- §4.2 Failure scenarios and baselines
- §4.3 Main results table (DR, FPR, LA across methods and failure types)
- §4.4 Uncertainty calibration analysis (calibration plot)
- §4.5 Ablation results
- §4.6 Information retention analysis

### Section 5: Discussion (0.75 pages)
- Clinical implications: what does a u = 0.3 mean for the pharmacist?
- Integration with Tatmeen: how the algebra overlays existing infrastructure
- Limitations: simulation vs. real deployment, parameter sensitivity
- Future work: real Tatmeen data, multi-national chains, regulatory feedback

### Section 6: Conclusion (0.5 pages)

### References (0.5 pages)

---

## 6. Dual-Submission Safety

This paper is DISTINCT from HealthCom Paper 1 ("HIPAA Compliance Algebra"):
- Paper 1: clinical data *privacy* compliance for FHIR R4 exchange
- Paper 2: physical drug *safety* assurance across supply chain custody transfers
- Different domain, different operators, different evidence types, different evaluation
- Shared mathematical framework (SL) cited as the same library (jsonld-ex)
- No overlapping results, no overlapping text

---

## 7. Implementation Notes

### jsonld-ex API Usage

The paper cites but does not re-derive the following from jsonld-ex:
- `Opinion(b, d, u, a)` — core type
- `cumulative_fuse(*opinions)` — within-node fusion (Jøsang §12.3)
- `deduce(opinion_x, opinion_y_given_x, opinion_y_given_not_x)` — cross-node
  propagation (Jøsang §9.4)
- `trust_discount(trust, opinion)` — sensor and scan coverage inside F1/F2
  (Jøsang §14.3)
- `decay_opinion(op, elapsed, half_life)` — temporal decay (Jøsang §16.2.2)
- `robust_fuse(opinions, threshold)` — Byzantine filtering (if needed)
- `pairwise_conflict()` — conflict detection for localization

### New Code for This Paper

All in `src/`:
- `models/drug_integrity_opinion.py` — DrugIntegrityOpinion class + F1–F5
- `data/supply_chain_dag.py` — DAG topology builder
- `simulation/simulator.py` — normal operation + failure injection
- `simulation/failure_scenarios.py` — F1–F7 definitions
- `baselines/threshold.py` — B1 implementation
- `baselines/weighted_scoring.py` — B2 implementation
- `baselines/bayesian_network.py` — B3 implementation
- `analysis/metrics.py` — DR, FPR, LA, UC, IR, GDD computation
- `analysis/generate_figures.py` — publication-ready figures
