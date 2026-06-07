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

## EXP-002: Supply Chain DAG + Chain Integrity Algebra (Definitions 4–7)

**Date:** 2026-06-07
**Researcher:** Muntaser Syed (with Claude)
**Type:** computational
**Status:** completed

### Hypothesis
The chain-level integrity algebra can be composed exclusively from three
established SL operators: cumulative_fuse (§12.3), trust_discount (§14.3),
and decay_opinion (§10.4). Cross-node propagation via serial trust
discounting will capture "integrity dilution along the chain" — upstream
uncertainty compounds downstream, producing a principled end-to-end score.

### Key Design Decisions

**DAG Model:** Lightweight domain-specific model (not SLNetwork from jsonld-ex).
Rationale: our contribution is the domain formalization, not graph infrastructure.
A self-contained DAG with cycle detection, topological sort, and path enumeration
keeps the paper code reviewable and reproducible.

**Within-Node Fusion (Definition 4):** cumulative_fuse across all five evidence
opinions. Evidence weighting via adjusted prior_weight: effective_W = base_W / weight.
Proven equivalent to evidence scaling. Missing evidence → vacuous opinion (Definition 7).

**Cross-Node Propagation (Definition 5):** Right-fold serial trust_discount along
product path. Each node's integrity acts as "trust" in downstream assessment.
Intuition: if the importer's handling was uncertain, even a perfect warehouse
can't fully restore confidence — you can't un-compromise a drug.

**Evidence Weight Implementation:** Adjusting Dirichlet prior_weight W by inverse
of weight. Higher weight → lower W → more dogmatic opinion → more influential
in cumulative fusion. Mathematically equivalent to scaling evidence counts.
Preserves override semantics: recall (dogmatic, u=0) and vacuous (no evidence,
u=1) are unaffected by weight scaling.

**Failure Localization:** Per-node opinions preserved through composition.
localize_failure() flags nodes with low projected probability or high uncertainty.
This is a key advantage over threshold systems — not just "something is wrong"
but "HERE is where it went wrong."

### Artifacts
- src/data/supply_chain_dag.py (DAG model with path enumeration)
- src/models/chain_integrity.py (evaluate_node, evaluate_path, evaluate_chain, localize_failure)
- tests/test_chain_integrity.py (28 tests covering Definitions 4–7 + Properties P1–P5)

---

## EXP-002a: BUGFIX — Cross-Node Propagation Operator (Definition 5 revised)

**Date:** 2026-06-07
**Researcher:** Muntaser Syed (with Claude)
**Type:** computational
**Status:** completed

### Problem Identified
Test `test_p5_recall_override_chain_level` failed: a dogmatic recall at the
warehouse node produced chain-level P(ω) = 0.93 instead of < 0.2.

### Root Cause Analysis
`trust_discount` (Jøsang §14.3) has the WRONG semantics for drug integrity
chains. In trust models, disbelief in a source produces UNCERTAINTY about
downstream claims — "I don't trust Bob, so I'm uncertain about what Bob says."
But for drug integrity, compromise at any node should produce DISBELIEF —
"the drug was compromised at the warehouse, therefore the chain is compromised
regardless of downstream handling."

Mathematically, trust_discount maps:
  d_trust → u_result (disbelief → uncertainty)

But drug integrity requires:
  d_node → d_chain (disbelief → disbelief)

### Fix Applied
Replaced trust_discount with DEDUCTION (Jøsang §12.6) using causal conditionals:
- ω_y|x = ω_remaining_chain (if safe at this node, depends on rest)
- ω_y|¬x = (0, 1, 0, 0.01) (if compromised, chain is compromised)

This captures conjunctive semantics: drug is safe at dispensing iff it was
safe at EVERY node. The compromised conditional ω_y|¬x reflects the physical
irreversibility of drug compromise within the supply chain.

### Verification
The deduction operator correctly propagates disbelief:
- Dogmatic recall at any node → chain d ≈ 1 ✓
- All good nodes → chain b remains high ✓
- Mixed → proportional degradation with disbelief (not just uncertainty) ✓

### Impact
This is a STRONGER paper contribution. The deduction-based composition:
1. Has clearer physical interpretation (causal, irreversible compromise)
2. Uses a documented SL operator (§12.6, not ad-hoc)
3. Correctly distinguishes "uncertain" from "compromised" at chain level
4. Produces a more natural comparison against threshold baselines

### Note
PAPER_PLAN.md §3.5 still references trust_discount — needs updating to match
the implementation. Code is the source of truth.

---
