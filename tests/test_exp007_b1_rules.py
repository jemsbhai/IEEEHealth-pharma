"""
EXP-007 tests: B1 rule limits exposed through evaluate_threshold.

Validates:
- the default call is identical to the deployed rules (B1-fixed unchanged)
- temp_tolerance, max_failure_rate and min_scan_rate each change the verdict
  in the expected direction and only for their own evidence type
"""

import numpy as np
import pytest

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from simulation.simulator import INSULIN, generate_normal_evidence
from models.drug_integrity_opinion import (
    NodeEvidence, SerializationEvidence, ColdChainEvidence,
)
from baselines.baselines import evaluate_threshold

PATH = ["manufacturer", "importer", "central_warehouse", "distributor_a", "pharmacy_a"]


def _clean_chain(seed=2):
    rng = np.random.default_rng(seed)
    return {n: generate_normal_evidence(INSULIN, rng=rng) for n in PATH}


def _with(ev_map, node, **fields):
    base = ev_map[node]
    out = dict(ev_map)
    out[node] = NodeEvidence(
        serialization=fields.get("serialization", base.serialization),
        cold_chain=fields.get("cold_chain", base.cold_chain),
        shelf_life=base.shelf_life,
        documentation=base.documentation,
        recall=base.recall,
    )
    return out


class TestDefaultsUnchanged:

    def test_default_call_equals_explicit_deployed_limits(self):
        ev = _clean_chain()
        a = evaluate_threshold(PATH, ev)
        b = evaluate_threshold(PATH, ev, min_scan_rate=0.90, max_failure_rate=0.01, temp_tolerance=0.0)
        assert (a.chain_score, a.node_scores, a.detected, a.weakest_node) == \
               (b.chain_score, b.node_scores, b.detected, b.weakest_node)

    def test_clean_chain_passes(self):
        assert evaluate_threshold(PATH, _clean_chain()).detected is False


class TestTemperatureTolerance:

    def test_small_blip_fails_zero_tolerance_and_passes_with_tolerance(self):
        ev = _clean_chain()
        cc = ev["importer"].cold_chain
        readings = list(cc.readings)
        readings[0] = cc.required_max + 0.5
        blipped = ColdChainEvidence(tuple(readings), cc.required_min, cc.required_max, cc.expected_readings)
        ev = _with(ev, "importer", cold_chain=blipped)
        assert evaluate_threshold(PATH, ev).detected is True
        assert evaluate_threshold(PATH, ev, temp_tolerance=1.0).detected is False
        assert evaluate_threshold(PATH, ev, temp_tolerance=0.4).detected is True

    def test_tolerance_does_not_affect_serialization_rule(self):
        ev = _clean_chain()
        ser = SerializationEvidence(scanned_success=95, scanned_failure=5, total_packages=100)
        ev = _with(ev, "importer", serialization=ser)
        assert evaluate_threshold(PATH, ev, temp_tolerance=5.0).detected is True


class TestFailureRateLimit:

    def test_two_failures_fail_deployed_limit_and_pass_tuned_limit(self):
        ev = _clean_chain()
        ser = SerializationEvidence(scanned_success=98, scanned_failure=2, total_packages=100)
        ev = _with(ev, "importer", serialization=ser)
        assert evaluate_threshold(PATH, ev).detected is True
        assert evaluate_threshold(PATH, ev, max_failure_rate=0.03).detected is False

    def test_five_percent_counterfeit_still_fails_a_tuned_limit_of_three_percent(self):
        ev = _clean_chain()
        ser = SerializationEvidence(scanned_success=95, scanned_failure=5, total_packages=100)
        ev = _with(ev, "importer", serialization=ser)
        assert evaluate_threshold(PATH, ev, max_failure_rate=0.03).detected is True


class TestScanRateLimit:

    def test_sixty_percent_coverage_fails_deployed_and_passes_lowered_limit(self):
        ev = _clean_chain()
        ser = SerializationEvidence(scanned_success=60, scanned_failure=0, total_packages=100)
        ev = _with(ev, "central_warehouse", serialization=ser)
        assert evaluate_threshold(PATH, ev).detected is True
        assert evaluate_threshold(PATH, ev, min_scan_rate=0.5).detected is False
        assert evaluate_threshold(PATH, ev).weakest_node == "central_warehouse"
