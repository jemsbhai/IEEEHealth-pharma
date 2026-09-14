"""
EXP-006 tests: localization credit for multi-node failures (H6.1).

Validates:
- FailureScenario.localization_target_set() defaults to the injection node
- F7 lists both nodes where it injects, and actually injects at both
- localization_accuracy_from_trials scores the same rule for any method
- run_experiment uses that rule for its reported localization accuracy
"""

import numpy as np
import pytest

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from simulation.simulator import (
    INSULIN,
    FailureScenario,
    FailureType,
    apply_failure,
    generate_normal_evidence,
    get_standard_scenarios,
    localization_accuracy_from_trials,
    run_experiment,
)


PATH = ["manufacturer", "importer", "central_warehouse", "distributor_a", "pharmacy_a"]


class TestLocalizationTargets:

    def test_single_node_scenarios_default_to_injection_node(self):
        scenarios = get_standard_scenarios()
        for sid in ["F1", "F2", "F3", "F4", "F5", "F6"]:
            sc = scenarios[sid]
            assert sc.localization_targets == ()
            assert sc.localization_target_set() == (sc.target_node,)

    def test_f7_lists_both_injected_nodes(self):
        f7 = get_standard_scenarios()["F7"]
        sub_nodes = tuple(sub.target_node for sub in f7.params["sub_scenarios"])
        assert set(f7.localization_target_set()) == set(sub_nodes)
        assert len(f7.localization_target_set()) == 2

    def test_f7_injects_at_exactly_the_listed_nodes(self):
        """The credited set must equal the set of nodes whose evidence changed."""
        rng = np.random.default_rng(7)
        base = {n: generate_normal_evidence(INSULIN, rng=rng) for n in PATH}
        f7 = get_standard_scenarios()["F7"]
        injected = apply_failure(base, f7, INSULIN, rng=rng)
        changed = {n for n in PATH if injected[n] != base[n]}
        assert changed == set(f7.localization_target_set())

    def test_explicit_targets_override_default(self):
        sc = FailureScenario(
            FailureType.F3_COUNTERFEIT, "importer",
            {"counterfeit_fraction": 0.05},
            localization_targets=("importer", "central_warehouse"),
        )
        assert sc.localization_target_set() == ("importer", "central_warehouse")


class TestLocalizationAccuracyHelper:

    def test_credits_any_target_node(self):
        detected = [True, True, True, False]
        localized = [["central_warehouse"], ["distributor_a"], ["importer"], ["distributor_a"]]
        la = localization_accuracy_from_trials(
            detected, localized, ("central_warehouse", "distributor_a"))
        assert la == pytest.approx(2 / 3)

    def test_single_target_matches_previous_rule(self):
        detected = [True, True, True]
        localized = [["importer"], ["importer", "pharmacy_a"], ["distributor_a"]]
        la = localization_accuracy_from_trials(detected, localized, ("importer",))
        assert la == pytest.approx(2 / 3)

    def test_undetected_trials_do_not_count(self):
        detected = [False, False, True]
        localized = [["importer"], ["importer"], ["manufacturer"]]
        la = localization_accuracy_from_trials(detected, localized, ("importer",))
        assert la == 0.0

    def test_no_detections_returns_zero(self):
        la = localization_accuracy_from_trials([False, False], [[], []], ("importer",))
        assert la == 0.0

    def test_single_weakest_node_as_one_element_list(self):
        """Baselines pass one node per trial; a match on either target counts."""
        detected = [True, True]
        localized = [["distributor_a"], ["central_warehouse"]]
        la = localization_accuracy_from_trials(
            detected, localized, ("central_warehouse", "distributor_a"))
        assert la == 1.0


class TestRunExperimentUsesTargetSet:

    def test_reported_la_matches_helper_on_trials(self):
        f7 = get_standard_scenarios()["F7"]
        result = run_experiment(
            path=PATH, product=INSULIN, scenario=f7, scenario_name="F7",
            n_trials=20, seed=42,
        )
        expected = localization_accuracy_from_trials(
            [t.detected for t in result.trials],
            [t.localized_nodes for t in result.trials],
            f7.localization_target_set(),
        )
        assert result.localization_accuracy == pytest.approx(expected)
        assert 0.0 <= result.localization_accuracy <= 1.0
