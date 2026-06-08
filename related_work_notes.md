# Related Work Survey — Drug Integrity Algebra (Step A)

Date: 2026-06-08

## Positioning Statement

Our paper is the first to apply Jøsang's Subjective Logic (SL) to
pharmaceutical supply chain integrity assessment. Existing approaches
fall into five categories, each with specific limitations our algebra
addresses.

---

## Category 1: Regulatory Track-and-Trace Systems

**Systems:** DSCSA (US, 2013, full enforcement 2023–25), EU FMD (2019),
UAE Tatmeen (Ministerial Decree 73/2021), Turkey ITS (2010), China NMPA.

**What they do:** Mandate serialization (GTIN + batch + expiry + serial)
and verify package identity at each custody transfer. DSCSA requires
unit-level electronic tracing. Tatmeen mandates 2D DataMatrix with
exactly 4 GS1 data elements (Article 5, Decree 73/2021).

**Limitation:** Binary pass/fail verification. A package either verifies
or doesn't. No graded severity, no uncertainty quantification, no
multi-dimensional evidence fusion. Our B1 baseline mirrors this approach
exactly — and our results show B1 outputs 0.0 for ALL failures regardless
of severity (mild excursion and severe excursion produce identical scores).

**Our advantage:** The SL algebra maps the same serialization evidence
that track-and-trace systems collect to SL opinions (F1, via
from_evidence §3.2) and ADDS four additional evidence dimensions
(cold chain, shelf life, documentation, recall), fusing them into a
graded integrity score with explicit uncertainty.

**Key references:**
- DSCSA: FDA, 2013 (Public Law 113-54, Title II)
- EU FMD: Directive 2011/62/EU, Delegated Regulation 2016/161
- Tatmeen: Ministerial Decree No. 73/2021 (UAE MoHAP)
- MediLedger Project (Chronicled/Pfizer/McKesson) — DSCSA pilot
- UCLA-LedgerDomain — FDA DSCSA Pilot Project Program

---

## Category 2: Blockchain for Pharmaceutical Supply Chain

**Systems:** MediLedger (Pfizer, Gilead, McKesson), PharmaChain,
Modum.io + Ethereum, TraceLink, Hyperledger-based solutions.

**What they do:** Provide tamper-proof, decentralized audit trails for
custody transfer events. Ensure DATA INTEGRITY of records. Some
integrate IoT sensors (Modum.io: temperature + blockchain).

**Limitation:** Blockchain ensures records haven't been tampered with,
but doesn't REASON about what the records mean. If 5% of scan
verifications fail, blockchain faithfully records those failures but
doesn't produce a risk assessment. There is no evidence fusion, no
severity grading, no uncertainty decomposition. Blockchain is
infrastructure, not intelligence.

**Our relationship:** COMPLEMENTARY. Blockchain could provide the
tamper-proof data infrastructure; our algebra reasons OVER that data to
produce integrity assessments. The SL algebra could consume EPCIS events
from a blockchain-backed Tatmeen system.

**Key references:**
- Bocek et al., 2017: Modum.io — blockchain + IoT for pharma cold chain
- PharmaChain (arXiv:2202.02592): blockchain for counterfeit-free supply chain
- FDA DSCSA Pilot Projects: MediLedger, TraceLink, UCLA-LedgerDomain
- Blockchain-track-and-trace review (wirelesslifesciences.org, 2025)

---

## Category 3: IoT Cold Chain Monitoring + ML Anomaly Detection

**Approaches:** IoT sensors (temperature, humidity, GPS) + ML models
(LSTM, ANN, Random Forest) for real-time anomaly detection. Predictive
analytics for excursion prevention.

**What they do:** Monitor temperature continuously, detect deviations
from "normal" profiles, predict failures before they occur. Some systems
claim >82% prevention rate for cold chain disruptions (PatSnap, 2026).
Mean Kinetic Temperature (MKT) used for regulatory compliance assessment.

**Limitation:** (a) Single-dimension: only temperature/humidity. No
integration with serialization, documentation, or recall evidence.
(b) Require training data: ML models need labeled examples of normal
and anomalous conditions. Our simulation generates this data, but
real-world labeled pharmaceutical incidents are scarce.
(c) No uncertainty calibration: ML outputs probabilities but these are
typically not well-calibrated. SL opinions explicitly decompose belief,
disbelief, and uncertainty.
(d) No cross-node propagation: ML monitors individual nodes but doesn't
compose a chain-level integrity assessment.

**Our advantage:** Our cold chain evidence (F2) is ONE of five dimensions.
The algebra combines it with serialization, shelf life, documentation,
and recall evidence via cumulative fusion. The F3 counterfeit sensitivity
sweep (EXP-004) shows that even with perfect cold chain monitoring,
counterfeiting detection requires the serialization dimension.

**Key references:**
- Cold Chain Monitoring with IoT Sensors (ResearchGate, 2026)
- Anomaly Detection in Cold Storage Systems: ML Approach (ResearchGate, 2025)
- Enhancing Food Safety in Cold Chain Through IoT and AI (PMC, 2025)
- PatSnap Cold Chain Technology Landscape (2026)

---

## Category 4: Dempster-Shafer Theory (DST) in Supply Chain / Fraud

**Applications:** Evidence fusion for fraud detection, supplier selection,
anomaly detection in networks. D-S provides a framework for combining
multiple evidence sources via Dempster's rule of combination.

**What it does:** Assigns mass functions (basic probability assignments)
to subsets of hypotheses and combines them via Dempster's rule. Handles
epistemic uncertainty via the mass assigned to the full frame of
discernment (analogous to SL's uncertainty u).

**Limitation vs. SL:**
(a) **Zadeh's paradox:** When evidence sources highly conflict, Dempster's
rule can produce counterintuitive results (all mass concentrated on an
unlikely hypothesis). SL's cumulative fusion handles conflict more
gracefully via the uncertainty component.
(b) **No trust discounting:** D-S has no native operator for propagating
opinions through trust networks. SL's trust_discount (§14.3) and deduce
(§12.6) handle this. Our cross-node propagation (deduce with causal
conditionals) has no D-S equivalent.
(c) **No base rate:** D-S mass functions don't include a prior base rate.
SL opinions include base rate a, which allows projection to probability
(P(ω) = b + a·u) and meaningful interpretation of uncertainty.
(d) **No temporal decay:** SL's decay_opinion (§10.4) handles evidence
staleness. No D-S equivalent.

**Our positioning:** SL is sometimes described as an extension of D-S
theory that addresses its known limitations (Jøsang 1998, 2016). Our
paper uses SL specifically because the pharmaceutical supply chain
requires trust propagation (cross-node deduce) and graceful conflict
handling (cumulative fusion with 5 heterogeneous evidence types).

**Key references:**
- Shafer, 1976: A Mathematical Theory of Evidence (foundational)
- Dempster-Shafer for Anomaly Detection (arXiv:0803.1568)
- Bayesian and D-S models for fraud detection (arXiv:2104.07440)
- Evidential supplier selection based on interval data fusion (arXiv:1703.01971)
- Jøsang, 2016: Subjective Logic (§1.4 discusses D-S relationship)

---

## Category 5: Subjective Logic in Trust and IoT

**Applications:** Trust management in MANETs, VANETs, wireless mesh
networks, e-commerce, peer-to-peer systems. SL is widely used for trust
opinion formation, fusion, and propagation.

**What exists:** SL opinions used for trust assessment in networks,
sensor data fusion in IoT, misbehavior detection in vehicular networks.
Multi-source fusion operators (CBF, ABF, WBF, CCF) have been formalized
(arXiv:1805.01388). Context-dependent trust with confidence levels
(arXiv:1309.4994).

**What does NOT exist:** No prior work applies SL to pharmaceutical
supply chain integrity. No prior work maps pharmaceutical evidence
(serialization, cold chain, shelf life, documentation, recall) to SL
opinions. No prior work uses deduce (§12.6) for cross-node propagation
in a supply chain DAG.

**Our contribution in this context:** The first application of SL to
pharmaceutical supply chain integrity. The formalization of 5 evidence
types as SL inputs. The use of deduce (not trust_discount) for cross-node
propagation, justified by the physical irreversibility of drug compromise.

**Key references:**
- Jøsang, 2016: Subjective Logic (Springer, textbook)
- Multi-Source Fusion Operations in SL (arXiv:1805.01388)
- Non-biased trust model for wireless mesh networks (arXiv:1712.09862)
- Context-dependent trust decisions with SL (arXiv:1309.4994)
- Survey on trust modeling from Bayesian perspective (arXiv:1806.03916)
- Trust management for WSN review (arXiv:1406.3204)

---

## Category 6: Motivation — Global Counterfeit Drug Crisis

**Key statistics (for Introduction):**
- WHO: ~10% of medicines in LMICs are substandard or falsified
- WHO: $30.5B/year spent on substandard/falsified medical products
- WHO GSMS: incidents doubled from 2019 to 2023
- PSI: pharmaceutical crime incidents up 48% from 2020 (IFPMA)
- 300+ children died in 2022 (Gambia, Indonesia, Uzbekistan) from
  contaminated cough syrups
- $83B annual counterfeit drug market (WHO estimate)

**Key references:**
- WHO Fact Sheet: Substandard and Falsified Medical Products (2024)
- IFPMA: Falsified Medicines (2025)
- Frontiers in Public Health: Combatting Substandard and Falsified
  Medicines (2021, DOI: 10.3389/fpubh.2021.754279)
- Counterfeit medicine: a major public health concern (Springer, 2025)
- WHO Global Surveillance and Monitoring System (GSMS)

---

## Gap Analysis — What Our Paper Fills

| Approach | Graded severity | Uncertainty | Multi-dim | Cross-node | No training data |
|----------|----------------|-------------|-----------|------------|-----------------|
| Track-and-trace (B1) | ✗ binary | ✗ | ✗ (serial only) | ✗ | ✓ |
| Blockchain | ✗ audit only | ✗ | ✗ | ✗ | ✓ |
| IoT+ML | partial | ✗ | ✗ (temp only) | ✗ | ✗ (needs data) |
| Dempster-Shafer | ✓ | ✓ (mass on Θ) | ✓ | ✗ (no deduce) | ✓ |
| **SL Algebra (ours)** | **✓** | **✓ (b,d,u,a)** | **✓ (5 dims)** | **✓ (deduce)** | **✓** |

**The unique combination:** Our algebra is the only approach that provides
graded severity, explicit uncertainty decomposition, multi-dimensional
evidence fusion, AND cross-node causal propagation, without requiring
training data.

---

## Additional SOTA Baselines — Assessment

Should we implement additional baselines beyond B1-B3?

**Dempster-Shafer fusion (B4):** Would be the strongest comparison. Map
the same 5 evidence types to D-S mass functions, combine with Dempster's
rule. Compare: does D-S handle the 5-type fusion + cross-node propagation
as well as SL? Estimate: moderate effort (2-3 hours), high paper impact.
**Recommendation: YES, implement.**

**ML anomaly detection (B5):** Train an autoencoder or isolation forest on
Normal evidence vectors, detect anomalies as reconstruction error.
Estimate: moderate effort, but introduces training/test split complexity
and the "needs training data" limitation is part of our argument.
**Recommendation: MENTION in related work, don't implement.** Our simulation
isn't the right setting to show ML's data dependency limitation.

**Fuzzy logic (B6):** Map evidence to fuzzy membership functions, apply
fuzzy rules. Estimate: moderate effort, but fuzzy logic is not a direct
competitor (it handles vagueness, not uncertainty).
**Recommendation: MENTION, don't implement.**
