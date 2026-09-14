"""
EXP-012 tests: expert ranking analysis (Kendall's W, median ranking,
label mapping, Spearman against orderings and methods).
"""

import csv
import json
import math

import numpy as np
import pytest

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import run_expert_ranking as er


class TestKendallsW:

    def test_perfect_agreement(self):
        m = [[1, 2, 3, 4, 5, 6, 7, 8]] * 3
        W, p = er.kendalls_w(m)
        assert W == pytest.approx(1.0)
        assert p < 0.01

    def test_hand_computed_case(self):
        # rankers: [1,2,3], [1,2,3], [1,3,2] -> R = [3,7,8], S = 14, W = 168/216
        W, _ = er.kendalls_w([[1, 2, 3], [1, 2, 3], [1, 3, 2]])
        assert W == pytest.approx(168 / 216)

    def test_tie_correction(self):
        # one ranker ties two items: T = 2^3 - 2 = 6 for that ranker
        m = [[1, 2, 3], [1, 2, 3], [1, 2.5, 2.5]]
        R = np.array(m).sum(axis=0)
        S = float(np.sum((R - R.mean()) ** 2))
        expected = 12 * S / (9 * 24 - 3 * 6)
        W, _ = er.kendalls_w(m)
        assert W == pytest.approx(expected)

    def test_reversed_rankers_low_agreement(self):
        m = [[1, 2, 3, 4, 5, 6, 7, 8], [8, 7, 6, 5, 4, 3, 2, 1]]
        W, _ = er.kendalls_w(m)
        assert W == pytest.approx(0.0, abs=1e-12)


class TestMedianRanking:

    def test_median_with_tiebreak(self):
        m = [[1, 2, 3], [1, 3, 2], [2, 1, 3]]
        # medians: [1, 2, 3]
        assert er.median_ranking(m) == [1.0, 2.0, 3.0]

    def test_tied_median_broken_by_mean(self):
        m = [[1, 2], [2, 1], [1, 2]]
        # medians [1, 2] -> ranks [1, 2]
        assert er.median_ranking(m) == [1.0, 2.0]
        m2 = [[1, 2], [2, 1]]
        # medians [1.5, 1.5], means equal -> average ranks 1.5, 1.5
        assert er.median_ranking(m2) == [1.5, 1.5]


class TestSpearmanHelper:

    def test_perfect_and_constant(self):
        assert er.spearman([1, 2, 3, 4], [1, 2, 3, 4])["rho"] == pytest.approx(1.0)
        assert er.spearman([1, 2, 3, 4], [4, 3, 2, 1])["rho"] == pytest.approx(-1.0)
        r = er.spearman([1, 2, 3, 4], [1, 1, 1, 1])
        assert r["constant_output"] is True and math.isnan(r["rho"])


class TestLabelMapping:

    def test_key_is_a_permutation_per_product(self):
        key = er.load_key()
        for product, mapping in key["products"].items():
            assert sorted(mapping) == list("ABCDEFGH")
            assert sorted(mapping.values()) == sorted(er.SCENARIOS)
        # different presentation orders per product
        orders = [tuple(key["products"][p][l] for l in "ABCDEFGH") for p in key["products"]]
        assert len(set(orders)) == 3

    def test_canonical_product_names(self):
        assert er.canonical_product("Insulin") == "Insulin"
        assert er.canonical_product("Amoxicillin") == "Amoxicil"
        assert er.canonical_product("mRNA") == "mRNA-bas"
        assert er.canonical_product("unknown") is None


def _write_response(path, ranker, product, ranks_by_scenario, key, role="pharmacist", years="10"):
    mapping = key["products"][er.canonical_product(product)]
    label_of = {v: k for k, v in mapping.items()}
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if path.stat().st_size == 0:
            w.writerow(["ranker_id", "role", "years_experience", "product", "label", "rank"])
        for sc, rank in ranks_by_scenario.items():
            w.writerow([ranker, role, years, product, label_of[sc], rank])


class TestEndToEnd:

    def test_synthetic_responses_recover_global_ordering(self, tmp_path):
        key = er.load_key()
        # three rankers all agreeing with the global ordering on all products
        glob = er.SEVERITY_ORDER
        for i in range(1, 4):
            p = tmp_path / f"R{i}.csv"
            p.touch()
            for product in ("Insulin", "Amoxicillin", "mRNA"):
                _write_response(p, f"R{i}", product, {sc: glob[sc] for sc in er.SCENARIOS}, key)
        # synthetic method scores file: SL follows the global ordering, B4 constant
        results = {"results": []}
        for product in ("Insulin ", "Amoxicil", "mRNA-bas"):
            for sc in er.SCENARIOS:
                results["results"].append({"product": product, "method": "SL_Algebra", "scenario": sc,
                                           "mean_score": 1.0 - 0.1 * glob[sc]})
                results["results"].append({"product": product, "method": "B4_DempsterShafer", "scenario": sc,
                                           "mean_score": 1.0})
        mr = tmp_path / "main.json"
        mr.write_text(json.dumps(results))

        out = er.run_expert_ranking(responses_dir=tmp_path, main_results=mr, verbose=False)
        assert set(out["products"]) == {"Insulin", "Amoxicil", "mRNA-bas"}
        ins = out["products"]["Insulin"]
        assert ins["n_rankers"] == 3
        assert ins["kendalls_w"] == pytest.approx(1.0)
        assert ins["vs_orderings"]["global"]["rho"] == pytest.approx(1.0)
        assert ins["vs_methods"]["SL_Algebra"]["rho"] == pytest.approx(1.0)
        assert ins["vs_methods"]["B4_DempsterShafer"]["constant_output"] is True
        assert ins["expert_order_least_to_greatest"][0] == "Normal"
        assert ins["expert_order_least_to_greatest"][-1] == "F2"
        amox = out["products"]["Amoxicil"]
        assert "crt" in amox["vs_orderings"] and amox["vs_orderings"]["crt"]["rho"] < 1.0
        assert out["rankers"]["R1"]["role"] == "pharmacist"

    def test_missing_responses_raise(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            er.load_responses(tmp_path)
