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
