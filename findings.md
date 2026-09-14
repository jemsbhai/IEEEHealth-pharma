# Findings

## Curated Summary

The Drug Integrity Algebra achieves perfect detection (DR=1.000) and
perfect localization (LA=1.000) across all 7 failure scenarios × 3
pharmaceutical product categories (21 combinations, 1000 Monte Carlo
trials each, seed=42), with a false positive rate of 1.0% (EXP-002b).
The rule-based threshold baseline B1 reaches the same DR and LA on all 21
combinations (confirmed in EXP-006, after localization credit was made
uniform across methods), so binary detection of the injected failures is
not the differentiator; graded severity and explicit uncertainty are. Every
DR = 1.000 entry carries a 95% Clopper-Pearson interval of [0.9963, 1.000]
and every mean-score 95% half-width is at most 0.0033 (EXP-006).

The algebra’s core advantage over the three baselines is **graded severity
discrimination with explicit uncertainty**. For insulin under a mild
temperature excursion (F1: 5% of readings, +2°C above range), the SL
algebra produces P(ω) = 0.9512 ± 0.0045; under a severe excursion
(F2: 50% of readings, +17°C), it produces P(ω) = 0.3317 ± 0.0322.
The rule-based threshold baseline (B1) outputs 0.0 for both—a pharmacist
receives identical “fail” signals for fundamentally different risk levels.

Two critical architectural decisions drive the results:

1. **Deduction-based cross-node propagation** (Jøsang §12.6 with causal
   conditionals) correctly propagates disbelief through the supply chain,
   reflecting the physical irreversibility of drug compromise. The original
   trust_discount operator (§14.3) mapped upstream disbelief to downstream
   uncertainty—semantically wrong for this domain (EXP-002a).

2. **Per-dimension detection monitoring** (disbelief > 2% or uncertainty > 50%
   per evidence type per node) catches subtle failures invisible to chain-level
   aggregation. B2 (weighted scoring) completely misses 5% counterfeit
   infiltration (F3, DR=0.000) because its serialization score of 0.95 exceeds
   its threshold. The SL algebra detects F3 via per-dimension disbelief in
   the serialization evidence at the affected node.

All SL operations anchor exclusively to established operators from Jøsang’s
textbook: `from_evidence` (§3.2), `trust_discount` (§14.3), `cumulative_fuse`
(§12.3), `deduce` (§12.6). No ad-hoc formulas are introduced. The contribution
is the formalization of pharmaceutical supply chain evidence as SL inputs,
not the invention of new mathematical operators.

Ablation analysis (EXP-003) reveals a clear hierarchy among the algebra’s
components. Per-dimension detection monitoring is the single most critical
architectural choice: removing it collapses F4 (scan omission) detection
from DR=1.000 to DR≈0.33 across all product categories, while all other
ablations preserve DR=1.000. The scan omission failure manifests as high
uncertainty (u≈0.80) in the serialization dimension without elevating
disbelief—a pattern invisible to chain-level aggregation.

Evidence type ablation (A1) confirms clean 1:1 coupling: each failure type
becomes score-indistinguishable from Normal operation when its corresponding
evidence type is removed (cold chain → F1/F2, serialization → F3/F4, shelf
life → F5, documentation → F6). No single evidence type accounts for more
than two failure scenarios, validating the multi-dimensional design.

The fusion operator choice (cumulative vs averaging, A2) and propagation
operator choice (deduce vs independent node evaluation, A3) have zero impact
on detection rates. Their value is in uncertainty calibration (cumulative
fusion produces 4.8× lower uncertainty than averaging) and semantic
correctness (deduce produces proper chain-level opinions, not point
estimates). Product-specific evidence weights (A5) adjust severity
discrimination by 5–10% for temperature failures but do not affect detection
capability.

Richer metrics (EXP-004, extended in EXP-006) confirm two additional
advantages. First, the SL algebra is the only method with statistically
significant severity ranking: Spearman ρ=0.976 (p=3.3×10⁻⁵) on insulin and
mRNA against the global physical severity ordering; on amoxicillin ρ=0.786
(p=0.021) under the global ordering and 0.976 (p=3.3×10⁻⁵) under the
pre-registered CRT-specific ordering, while no baseline reaches significance
on any product under either ordering (B1 p=0.134, B2 p≥0.286, B3 p≥0.071,
B4 p≥0.452). All orderings are author-defined pending EXP-012. Second, the
F3 counterfeit fraction sensitivity sweep shows that the algebra detects
counterfeiting at 1% infiltration rate with DR=1.000, while B1 misses
exactly 1% (its failure-rate rule passes 0.01) and detects 2% and above,
B2 requires ≥10% and B3 requires >5% for majority detection (EXP-006).
The system is also completely robust to parameter variation, maintaining
DR=1.000 across 20× variation in detection thresholds and 3× variation in
evidence weights.

ROC/AUC analysis (Mann-Whitney U) shows SL achieves AUC=0.979, slightly
below B3’s 0.990 and B1’s trivially perfect 1.000 (binary output). The
lower AUC reflects that scan omission (F4) barely moves the chain-level
score—detection relies on per-dimension uncertainty monitoring, which ROC
cannot capture. Shannon entropy of SL output (2.19 bits) is 4× higher than
B1 (0.54 bits), quantifying the information advantage of graded scores
over binary pass/fail.

A Dempster-Shafer baseline (B4) reveals a concrete domain-specific
manifestation of Zadeh’s paradox: when four evidence types indicate
safety and an active pharmaceutical recall indicates compromise,
Dempster’s rule normalizes away the conflict and reports BetP(safe)=0.997
for a recalled drug. (This recall example is a unit-test construction from
EXP-005; no recall scenario is simulated in F1-F7, and the paper
illustrates the paradox with the simulated F2 instead.) The SL algebra
correctly produces near-zero integrity
through dogmatic opinion override in cumulative fusion, demonstrating a
practical advantage of SL’s conflict handling over D-S’s normalization.

The full experiment confirms this is not an edge case: B4 achieves the
worst detection rate of all five methods (DR=0.000–0.129 across all
scenarios), the worst AUC (0.516, barely above random), and zero Shannon
entropy (all scores are 1.0000, containing literally no information).
The failure is structural: Dempster’s rule across 25 mass functions
(5 evidence types × 5 nodes) drives uncertainty to zero and dilutes
failure signals to nothing. This evidence dilution problem goes beyond
Zadeh’s paradox and demonstrates that D-S is fundamentally unsuitable
for multi-dimensional, multi-node supply chain integrity assessment.

---

## Raw Findings Log

### 2026-09-14 — EXP-006: Main Suite v3.1 (localization fairness, intervals, B1 in sweeps, per-product orderings)

**Key result:** v3.1 reproduces every v3 DR, FPR and score exactly; crediting
either injected node for F7 raises B2/B3/B4 F7 LA from 0.000 to 1.000; B1
misses 1% counterfeit and detects 2%+; amoxicillin SL ρ = 0.786 (global
ordering) / 0.976 (pre-registered CRT ordering).

**Numbers (from experiments/full_results_v3_1.json, metrics_results_v3_1.json):**
- All 72 DR = 1.000 rows: 95% CI [0.9963, 1.000]. B3 on F3: 0.461
  [0.430, 0.492]. FPR 0.010 rows: [0.0048, 0.0183]. Largest mean-score
  half-width 0.0033 (SL, F2, amoxicillin); Table II rows ≤ 0.002.
- Counterfeit sweep (insulin), SL / B1 / B2 / B3 / B4: 1%: 1.000 / 0.000 /
  0.000 / 0.002 / 0.103; 2%: 1.000 / 1.000 / 0.000 / 0.016 / 0.000; 5%:
  1.000 / 1.000 / 0.000 / 0.461 / 0.013; 10%+: all 1.000 except B4 ≤ 0.036.
- Corrected excursion-duration sweep (+2 C, insulin), durations 1/2/5/10%:
  SL 1.000 throughout; B1 1.000 throughout (zero-tolerance rule);
  B2 0.101 / 0.298 / 0.892 / 1.000; B3 0.056 / 0.154 / 0.739 / 1.000.
  The EXP-004 excursion sweep was invalid (parameter-name bug, see LOGBOOK
  addendum 2026-09-14) and is superseded.
- Spearman, product-specific ordering, amoxicillin: SL 0.976 (p=3.3e-05),
  B1 0.577 (n.s.), B2 0.311 (n.s.), B3 0.667 (p=0.071), B4 0.031 (n.s.).
- Realized FPR: SL 0.010, B3 0.010, B1 0.000, B2 0.000, B4 0.000; B1 uses a
  fixed 0.5 threshold, not percentile calibration.

**Implications:** no headline number changes; the paper's "only method"
claim for DR/LA is withdrawn, LA is defined explicitly, Fig. 4 gains B1,
the ranking paragraph reports per-product values, and intervals are stated.

### 2026-06-07 — EXP-002b: Full Experiment Suite v2.1 Final Results

**Key result:** SL algebra DR=1.000, LA=1.000 across all 21 scenario×product
combinations. B2 misses F3 entirely (DR=0.000). B3 detects F3 at only
44.5% average. B1 detects everything but provides zero severity discrimination.

**Numbers (from experiments/full_results_v2.json):**

SL Algebra:
- DR: 1.000 on F1, F2, F3, F4, F5, F6, F7 (all products)
- FPR: 0.010 (matches 1% adaptive target)
- LA: 1.000 on all scenarios
- Severity: F2(Insulin)=0.332, F1(Insulin)=0.951, Normal=0.997
- Uncertainty: u ≈ 0.01 throughout (low uncertainty, high evidence)

B1 Threshold:
- DR: 1.000 on F1–F7 | FPR: 0.000 | LA: 1.000 on F1–F6, 1.000 on F7
- All scores binary 0.0/1.0 — no grading

B2 Weighted Scoring:
- DR: 0.892 on F1 | 0.000 on F3 | 1.000 on F2,F4,F5,F6,F7
- FPR: 0.000 | LA: 0.714 mean (misses F3, wrong node on F7)

B3 Bayesian:
- DR: 0.732 on F1 | 0.445 on F3 | 1.000 on F2,F4,F5,F6,F7
- FPR: 0.010 | LA: 0.857 mean (wrong node on F7)

**Critical observation:** F3 (5% counterfeit) is the differentiating scenario.
B2 cannot detect it at all. B3 detects it less than half the time.
SL catches it perfectly via per-dimension disbelief monitoring (d > 0.02
in serialization dimension at the importer node).

**Implications for ablation:** These results establish the control condition
for EXP-003. The ablation study should reveal which components are
responsible for the SL algebra’s advantages.

### 2026-06-08 — EXP-003: Ablation Study Suite (A1–A5)

**Key result:** Per-dimension detection monitoring (A4) is the ONLY ablation
that breaks detection. Removing it collapses F4 scan omission DR from 1.000
to 0.297–0.369 across products. All other ablations (A1–A3, A5) maintain
DR=1.000 on all scenarios.

**A1 — Evidence type ablation (score discrimination):**
Each evidence type is solely responsible for detecting its matched failure.
Dropping cold_chain makes F1/F2 scores identical to Normal (Δ=0.000).
Dropping serialization makes F3/F4 scores identical to Normal.
Dropping shelf_life makes F5 identical. Dropping documentation makes F6
identical. Clean 1:1 coupling across all three product categories.

**A2 — Fusion operator (averaging vs cumulative):**
DR=1.000 on all scenarios. Averaging fusion yields 4.8× higher uncertainty
(u=0.046 vs u=0.010) because it doesn’t accumulate independent evidence.
Severity discrimination unchanged (F2 Insulin: 0.332 vs 0.332).

**A3 — Propagation operator (independent min vs deduce):**
DR=1.000 on all scenarios. Normal scores are higher without propagation
(0.999 vs 0.997), making thresholds tighter. Severity discrimination
unchanged. Deduce’s advantage is semantic (principled opinion composition),
not operational.

**A4 — Per-dimension detection (chain-only vs chain+per-dim):**
CRITICAL: F4 DR drops from 1.000 to 0.297 (Insulin), 0.369 (Amoxicillin),
0.314 (mRNA). Scan omission produces u≈0.80 in serialization but no
disbelief; the chain-level score barely moves. Only per-dimension
uncertainty monitoring catches this. All other scenarios unaffected.

**A5 — Evidence weights (uniform vs product-specific):**
DR=1.000 on all. Severity shifts: Insulin F2 score rises from 0.332 to
0.381 (less sensitive) with uniform weights. Product-specific weights
correctly elevate cold_chain sensitivity for biologics.

**Hypothesis outcomes:** H4 (per-dim detection essential) STRONGLY SUPPORTED.
H1 (no single type >50%) SUPPORTED. H2 (cumulative > averaging) and H3
(deduce > independent) NOT SUPPORTED on DR, supported only on calibration.
H5 (weights matter) WEAKLY SUPPORTED on severity, not on detection.

### 2026-06-08 — EXP-004: Richer Metrics Suite

**Key results:**

1. Spearman ρ=0.976 (p=3.3e-05): SL is the ONLY method with significant
   severity ranking. B1/B2/B3 all fail significance.

2. F3 counterfeit sweep: SL DR=1.000 at 1% infiltration. B2 needs ≥10%.
   B3 needs >5% for majority detection.

3. ROC/AUC: SL=0.979, B1=1.000 (binary), B2=0.968, B3=0.990.
   SL min on F4 (AUC=0.856) because chain score barely moves.

4. Entropy: SL=2.19 bits, B1=0.54 bits (4× information advantage).

5. Calibration: ECE=0.77 (high by design). P(ω) measures evidence quality,
   not P(safe). Subtle failures correctly score high.

6. Parameter sensitivity: DR=1.000 across 20× variation in thresholds,
   3× variation in weights. System is completely robust.

**Bug found and fixed:** Original ROC/AUC used sort+trapz which produced
AUC=0.50 for perfectly separated distributions due to duplicate FPR
handling. Fixed with Mann-Whitney U statistic (exact).

### 2026-06-08 — Related Work Survey (Step A)

**Key finding:** No prior work applies Subjective Logic to pharmaceutical
supply chain integrity. The paper is positioned at the intersection of
five research areas, each with specific limitations our algebra addresses.

Six categories surveyed:
1. **Track-and-trace (DSCSA/EU FMD/Tatmeen):** Binary pass/fail. Our B1
   baseline mirrors this. No severity grading.
2. **Blockchain for pharma:** Ensures data integrity of records, not drug
   integrity. Complementary to our approach (could provide data layer).
3. **IoT+ML cold chain:** Single-dimension (temperature), needs training
   data, no cross-node composition. Our F2 is one of five dimensions.
4. **Dempster-Shafer:** Closest theoretical competitor. Lacks trust
   discounting, has Zadeh’s paradox, no base rate, no temporal decay.
   SL addresses all of these (Jøsang 2016, §1.4).
5. **SL in trust/IoT:** SL widely used for trust in MANETs/VANETs, but
   NEVER applied to pharmaceutical supply chain. Our paper is the first.
6. **WHO statistics:** 10% of medicines in LMICs are substandard/falsified.
   $30.5B/year. Incidents doubled 2019–2023. 300+ children died in 2022.

Full survey with references in `related_work_notes.md`.

**Recommendation:** Implement a Dempster-Shafer baseline (B4) for direct
theoretical comparison. This is the highest-impact addition. ML and fuzzy
logic baselines should be mentioned in Related Work but not implemented.

### 2026-06-08 — EXP-005: B4 Dempster-Shafer + Zadeh’s Paradox

**HEADLINE FINDING:** Dempster-Shafer theory fails to detect an active
pharmaceutical recall when other evidence is clean. This is Zadeh’s paradox
(Zadeh, 1979) manifesting in our exact domain.

Setup: A supply chain node has 4 clean evidence types (serialization,
cold chain, shelf life, documentation) plus an ACTIVE_RECALL. D-S produces
BetP(safe) = 0.997 — reporting 99.7% confidence in safety for a recalled
drug. The SL algebra correctly produces near-zero integrity via dogmatic
opinion override in `form_recall_opinion()`.

Root cause: Dempster’s rule normalizes away conflict (K ≈ 0.99) rather
than propagating it. It treats the recall as a “measurement error” and
discounts it against the 4 agreeing sources. But a pharmaceutical recall
is a regulatory authority determination, not a noisy sensor reading.

This directly answers the reviewer question “Why not D-S?” and
strengthens the paper’s theoretical contribution.

**Full experiment results (v3.0):** B4 achieves DR=0.000–0.129 across all
7 failure scenarios (worst of all 5 methods). All B4 scores are 1.0000
for both Normal and failure trials — zero discrimination. Cause:
evidence dilution from combining 25 mass functions via Dempster’s rule
drives m(Θ)→0 and concentrates all mass on m(safe). AUC=0.516 (near
random). Spearman ρ=0.312 (worst, p=0.452). Entropy=0.000 bits (zero
information). B4 is worse than the binary threshold baseline (B1) on
every metric except raw AUC (where B1’s trivial binary separation wins).

Counterfeit fraction sweep: B4 shows erratic detection (0.000–0.214)
with no coherent pattern as counterfeit fraction increases. SL=1.000
at all levels.

### 2026-06-07 — EXP-000: Codebase Audit

**Key result:** jsonld-ex provides all required SL primitives (Opinion, cumulative_fuse,
trust_discount, decay_opinion, SLNetwork DAG) — no mathematical re-implementation needed.
The contribution is purely in the domain formalization (evidence-to-opinion mappings) and
evaluation (simulation + baselines).

**jsonld-ex API surface for this paper:**
- `Opinion(b, d, u, a)` with `projected_probability()`, arithmetic validation
- `cumulative_fuse(*opinions)` — n-ary, proper simultaneous formula
- `averaging_fuse(*opinions)` — n-ary, simultaneous (for ablation A2)
- `trust_discount(trust, opinion)` — chain propagation
- `decay_opinion(op, elapsed, half_life, decay_fn)` — 3 built-in decay fns
- `robust_fuse(opinions, threshold)` — Byzantine-resistant (conflict filtering)
- `pairwise_conflict(a, b)` — for failure localization
- `ComplianceOpinion` — subclass with domain aliases (can extend for pharma)
- `SLNetwork` — DAG with topological sort, cycle detection
- `propagate_trust()` — transitive trust through network with temporal decay

**Domain findings from collaborator document:**
- UAE pharma supply chain has 3 product categories (Municipality, GSL, Conventional)
  with distinct regulatory and pricing regimes
- Tatmeen is GS1-based: GTIN + serial + expiry + batch in 2D DataMatrix, GLN per
  stakeholder, SSCC for aggregation, EPCIS events for tracking
- Cold chain zones: CRT 15–25°C, refrigerated 2–8°C, frozen -15 to -70°C
- Shelf-life constraint: ~24 months manufactured, ~12 months by time of arrival,
  6-month minimum for pharmacy stock (MoHAP regulation)
- IMS data gaps for supplements; seasonal demand patterns (summer expat exodus)
- Insurance formulary coverage drives product selection and pricing

---

### 2026-06-07 — Tatmeen Primary Source Document Review

**Key result:** 22 official Tatmeen documents reviewed (EVOTEQ/MoHAP, v3.0–v5.0).
These are primary source technical specifications — not secondary reports. This
gives our paper authoritative grounding that most supply chain papers lack.

**Critical findings for our model:**

**1. EPCIS Event Taxonomy (from WKI-0060 Manufacturer Guide v5.0, 139 pages)**
Tatmeen uses GS1 EPCIS events as the atomic unit of supply chain reporting:
- Commissioning (SGTIN for items, SSCC for logistics units)
- Shipping / Receiving (custody transfer between GLN-identified parties)
- Aggregation / Disaggregation (pack/unpack hierarchies)
- Status Change: damaged, stolen, exported, lost/missing
- Batch Recall: initiate → MoHAP approval → completion
- Destruction: initiate → completion
- Each event is a single GS1 EPCIS XML file, max 10MB (was 15MB, corrected)

**2. Serialization Data Elements (Article 5 of Ministerial Decree 73/2021)**
Every 2D DataMatrix code contains exactly four data elements:
1. Global Trade Item Number (GTIN) — product identity
2. Batch Number — production lot
3. Medicine Expiry Date — shelf life endpoint
4. Serial Number — unique per package (random, non-repeating)
→ Our F1 (serialization) evidence maps directly to scan verification of these four elements.

**3. Supply Chain Topology (confirmed from technical guides)**
Two import routes documented:
a. Manufacturer → 3PL Warehouse → Distributor (via import clearance/customs)
b. Manufacturer → direct to Distributor (via import clearance/customs)
MAH/Licensing Agent responsible for Tatmeen commissioning reporting.
→ Our DAG model matches real-world topology.

**4. Dispensing Verification (WKI-0062 Dispenser Guide v3.0)**
CRITICAL: Dispensing is a SYNCHRONOUS real-time verification against Tatmeen.
The dispenser MUST request verification before dispensing. Tatmeen performs:
- Authenticity check (is the serial valid?)
- Status check (is the product active, not recalled, not expired?)
If verification fails, the product MUST NOT be dispensed.
→ This is exactly where our chain-level integrity score is evaluated — at the
dispensing verification point. The paper should frame the algebra as providing
a RICHER assessment at this exact point than the binary pass/fail that Tatmeen
currently returns.

**5. Batch Recall Process (TRM-0260 Batch Recall v2.0)**
Three-step process:
1. Initiation (by MAH/Scientific Office/Licensed Agent or MoHAP PV)
2. Approval (by MoHAP PV — can also reject)
3. Completion (all items decommissioned and blocked)
Between initiation and approval: items are marked "Initiated" — dispensing,
transfer, and logistics operations are STILL ALLOWED.
Between approval and completion: items are BLOCKED from dispensation but
transfer is still allowed.
→ Our F5 (recall) currently models binary ACTIVE_RECALL/NO_RECALL. The
three-step process suggests a richer model is possible (INITIATED → APPROVED →
COMPLETED), but binary is sufficient for the paper's scope.

**6. Product Transfer Events (WKI-0064 Logistics Guide v5.0)**
Transfer requires both sender and receiver to report:
- Shipping event (sender)
- Receiving event (receiver)
Both must be EPCIS compliant with GLN identifiers.
Cancellation events exist for corrections.
Status change events: damaged, stolen, exported, lost/missing.
→ Each custody transfer generates a pair of EPCIS events. Our serialization
evidence (F1) maps to whether both events were reported and matched.

**7. API Structure (Postman collection)**
Live API endpoints at tatmeen.ae with OAuth2 authentication.
Staging environment available for testing.
RESTful EPCIS submission + SOAP product verification.
→ Confirms the technical feasibility of our model overlaying real infrastructure.

**Impact on paper:**
These documents let us cite the ACTUAL Ministerial Decree articles (3, 4, 5),
the ACTUAL EPCIS event specifications (commissioning, shipping, receiving,
dispensing), and the ACTUAL verification process at the dispensing point. This
is a level of primary-source grounding that most supply chain papers don't have.
The collaborator's contribution is validated and enriched by these official specs.

---
