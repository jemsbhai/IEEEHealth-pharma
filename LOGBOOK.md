# Experimental Logbook

All experiments for the Drug Integrity Algebra paper.
Append-only. Never delete or modify past entries.

---

## EXP-000: Project Scaffolding & Formal Model

**Date:** 2026-06-07
**Researcher:** Muntaser Syed (with Claude)
**Type:** computational
**Status:** completed

### Notes
- Scaffolded project repo at E:\data\code\claudecode\IEEEHealth-pharma
- Completed jsonld-ex codebase audit: identified all reusable SL primitives
- Formalized the drug integrity algebra (Definitions 1–7, Properties P1–P5)
- Defined five evidence-to-opinion formation functions (F1–F5)
- Defined seven failure injection scenarios (F1–F7)
- Defined three baselines (B1–B3) and six evaluation metrics
- Wrote PAPER_PLAN.md with complete formal model and paper structure

### Artifacts
- PAPER_PLAN.md (full formal model)
- README.md
- Directory scaffold per lab-runner protocol

---

## EXP-001: Formation Function Implementation (F1–F5)

**Date:** 2026-06-07
**Researcher:** Muntaser Syed (with Claude)
**Type:** computational
**Status:** completed

### Hypothesis
All five evidence-to-opinion formation functions can be composed exclusively
from established SL operators (Jøsang §3.2 evidence mapping + §14.3 trust
discounting), with no ad-hoc formulas. This makes every computation
independently verifiable against the textbook.

### Key Design Decisions

**Refinement from PAPER_PLAN.md:** During implementation, the formation
functions were refined from the initial ad-hoc formulations to a rigorous
two-step architecture:

1. **Evidence mapping:** `Opinion.from_evidence(r, w, W, a)` — Jøsang §3.2
2. **Coverage adjustment:** `trust_discount(ω_coverage, ω_sample)` — Jøsang §14.3

This refinement strengthens the paper because:
- No ad-hoc formulas anywhere — every step is a documented SL operator
- Reviewers can verify each step against Jøsang's textbook
- The contribution is cleanly the FORMALIZATION (which evidence maps to which
  SL input), not the invention of new mathematical operators

**F1 (Serialization):** Two-step. Evidence from scan counts, trust-discounted
by scan coverage. Key property: partial scan produces proportional uncertainty
via trust discounting — not an ad-hoc formula.

**F2 (Cold Chain):** Two-step. Severity-weighted negative evidence (mild vs
severe excursions weighted differently), trust-discounted by sensor coverage.
Severity normalization uses range width R = T_max - T_min. Severity cap
prevents single extreme reading from dominating.

**F3 (Shelf Life):** Single-step. Ratio-to-evidence mapping where distance
from regulatory threshold in each direction produces positive/negative evidence.
Key property: at ratio = 1.0 (threshold), r = 0, w = 0 → VACUOUS opinion.
This is the correct behavior: maximum uncertainty at the decision point.
No piecewise-linear breakpoints — smooth, continuous, SL-native.

**F4 (Documentation):** Single-step. Each document is an observation: present
→ positive evidence, missing critical → 3× negative evidence, missing
non-critical → 1× negative evidence. Asymmetric weighting reflects domain
reality (missing CoA >> missing import license).

**F5 (Recall):** Direct construction. Active recall → dogmatic disbelief
(override semantics via SL's dogmatic fusion dominance). No recall → vacuous
(absence of recall ≠ evidence of safety).

### Artifacts
- src/models/drug_integrity_opinion.py (DrugIntegrityOpinion + F1–F5)
- tests/test_formation_functions.py (property tests for P1–P5 per function)

---
