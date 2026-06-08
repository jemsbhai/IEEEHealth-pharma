# Findings

## Curated Summary

*No experimental results yet. Summary will be updated as experiments complete.*

---

## Raw Findings Log

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
