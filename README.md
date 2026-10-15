# Drug Integrity Algebra for Pharmaceutical Cold Chain

**Target:** IEEE HealthCom 2026 (Oct 19–21, NYC)

## Overview

Formalizes pharmaceutical supply chain compliance as a Subjective Logic
opinion algebra. Each node in the supply chain (manufacturer, importer,
warehouse, distributor, pharmacy) generates compliance opinions from
heterogeneous evidence (serialization scans, cold chain IoT sensors,
shelf-life status, documentation completeness, recall status). Opinions
fuse within each node and propagate across the supply chain DAG, producing
an end-to-end drug integrity score with calibrated uncertainty at the
point of patient dispensing.

## Key Contributions

1. **Five evidence-to-opinion formation functions** mapping heterogeneous
   supply chain data to SL opinions
2. **Chain-level fusion protocol** for propagating and composing opinions
   across a directed acyclic supply chain graph
3. **Simulation-based evaluation** on a Tatmeen-realistic topology with
   seven failure injection scenarios and three baselines

## Dependencies

- `jsonld-ex` (Python) — SL opinion algebra, fusion, trust discounting,
  temporal decay, DAG infrastructure
- Python 3.11+
- NumPy, Matplotlib (for simulation and figures)
- pytest (testing)

## Repository Structure

```
IEEEHealth-pharma/
├── PAPER_PLAN.md          # Full paper architecture and formal model
├── LOGBOOK.md             # Experimental logbook
├── findings.md            # Curated results + raw findings
├── configs/               # Experiment configurations
│   └── base.yaml          # Shared defaults
├── data/                  # Simulation data (gitignored)
├── src/                   # Source code
│   ├── models/            # DrugIntegrityOpinion, evidence formation
│   ├── data/              # Supply chain DAG, node/edge models
│   ├── simulation/        # Supply chain simulator, failure injection
│   ├── baselines/         # Threshold, weighted scoring, BN baselines
│   └── analysis/          # Statistical analysis, figure generation
├── scripts/               # Entry-point scripts
├── experiments/           # Experiment outputs
├── figures/               # Publication-ready figures
├── tests/                 # Test suite
└── paper/                 # LaTeX source
```

## Reproduction

```powershell
# Install dependencies
pip install jsonld-ex numpy matplotlib pytest --break-system-packages

# Run tests
python -m pytest tests/ -v

# Run experiments (after configs are set)
python scripts/run_experiment.py --config configs/base.yaml
```

## Authors

- Muntaser Syed (Florida Institute of Technology)
- Mahasen Enam (Pharma Industry, UAE)
- Marius Silaghi (Florida Institute of Technology)
- Venkat Durga Sai (BITS Pilani)
- Vincent Occhiogrosso (Arizona State University)  

## License

Research code — not for redistribution without permission.
