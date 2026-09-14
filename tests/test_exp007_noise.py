"""
EXP-007 tests: operational noise regimes (NoiseProfile, apply_operational_noise).

Validates:
- R0 is the identity
- benign scan failures conserve package counts and never touch total_packages
- blips land outside the range by the pre-registered displacement, and only
  where the mask fires
- dropout keeps a subsequence of readings, respects coverage bounds, and
  leaves expected_readings unchanged
- late documents remove only non-critical documents
- shelf life and recall are never touched
- the R1/R2 constants match the LOGBOOK pre-registration
- noisy evidence still accepts failure injection
- results are reproducible for a given seed
"""

import numpy as np
import pytest

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from simulation.simulator import (
    INSULIN, AMOXICILLIN,
    NoiseProfile, NOISE_R0, NOISE_R1, NOISE_R2, NOISE_REGIMES,
    apply_operational_noise, generate_noisy_normal_evidence,
    generate_normal_evidence, apply_failure, get_standard_scenarios,
)
from models.drug_integrity_opinion import RecallStatus


def _base(seed=11, product=INSULIN):
    rng = np.random.default_rng(seed)
    return generate_normal_evidence(product, rng=rng), rng


def _out_of_range(cc):
    return [t for t in cc.readings if t < cc.required_min or t > cc.required_max]


class TestRegimeConstants:

    def test_r0_is_null(self):
        assert NOISE_R0.is_null
        assert not NOISE_R1.is_null and not NOISE_R2.is_null

    def test_r1_matches_pre_registration(self):
        assert NOISE_R1 == NoiseProfile("R1", 0.005, 0.02, 1.0, 0.85, 0.05)

    def test_r2_matches_pre_registration(self):
        assert NOISE_R2 == NoiseProfile("R2", 0.01, 0.05, 2.0, 0.70, 0.10)

    def test_registry(self):
        assert list(NOISE_REGIMES) == ["R0", "R1", "R2"]


class TestIdentityAndInvariants:

    def test_r0_returns_evidence_unchanged(self):
        ev, rng = _base()
        assert apply_operational_noise(ev, INSULIN, NOISE_R0, rng) is ev

    @pytest.mark.parametrize("noise", [NOISE_R1, NOISE_R2])
    def test_shelf_life_and_recall_untouched(self, noise):
        ev, rng = _base()
        out = apply_operational_noise(ev, INSULIN, noise, rng)
        assert out.shelf_life == ev.shelf_life
        assert out.recall == RecallStatus.NO_RECALL

    @pytest.mark.parametrize("noise", [NOISE_R1, NOISE_R2])
    def test_package_counts_conserved(self, noise):
        ev, rng = _base()
        out = apply_operational_noise(ev, INSULIN, noise, rng)
        a, b = ev.serialization, out.serialization
        assert b.total_packages == a.total_packages
        assert b.scanned_success + b.scanned_failure == a.scanned_success + a.scanned_failure
        assert b.scanned_failure >= a.scanned_failure

    def test_critical_documents_never_removed(self):
        ev, rng = _base()
        noise = NoiseProfile("x", doc_delay_prob=1.0)
        out = apply_operational_noise(ev, INSULIN, noise, rng)
        doc = out.documentation
        assert doc.documents_critical.issubset(doc.documents_present)
        assert doc.documents_present == doc.documents_critical  # every non-critical doc late
        assert doc.documents_required == ev.documentation.documents_required


class TestScanErrors:

    def test_high_rate_produces_failures(self):
        ev, rng = _base()
        out = apply_operational_noise(ev, INSULIN, NoiseProfile("x", scan_error_rate=0.5), rng)
        assert out.serialization.scanned_failure > 0
        assert out.serialization.scanned_failure < ev.serialization.scanned_success

    def test_zero_rate_leaves_serialization_unchanged(self):
        ev, rng = _base()
        out = apply_operational_noise(ev, INSULIN, NoiseProfile("x", blip_prob=0.01), rng)
        assert out.serialization == ev.serialization


class TestBlips:

    def test_all_readings_blipped_land_in_the_displacement_band(self):
        ev, rng = _base()
        noise = NoiseProfile("x", blip_prob=1.0, blip_max_c=1.0)
        out = apply_operational_noise(ev, INSULIN, noise, rng)
        cc = out.cold_chain
        assert len(cc.readings) == len(ev.cold_chain.readings)
        assert len(_out_of_range(cc)) == len(cc.readings)
        for t in cc.readings:
            excursion = max(t - cc.required_max, cc.required_min - t)
            assert 0.1 <= excursion <= 1.0

    def test_r2_band_is_wider(self):
        ev, rng = _base()
        noise = NoiseProfile("x", blip_prob=1.0, blip_max_c=2.0)
        out = apply_operational_noise(ev, INSULIN, noise, rng)
        exc = [max(t - out.cold_chain.required_max, out.cold_chain.required_min - t)
               for t in out.cold_chain.readings]
        assert max(exc) > 1.0 and min(exc) >= 0.1

    def test_no_blips_when_probability_zero(self):
        ev, rng = _base()
        out = apply_operational_noise(ev, INSULIN, NoiseProfile("x", scan_error_rate=0.001), rng)
        assert out.cold_chain == ev.cold_chain

    def test_blips_go_both_directions(self):
        ev, rng = _base()
        out = apply_operational_noise(ev, INSULIN, NoiseProfile("x", blip_prob=1.0, blip_max_c=1.0), rng)
        cc = out.cold_chain
        assert any(t > cc.required_max for t in cc.readings)
        assert any(t < cc.required_min for t in cc.readings)


class TestDropout:

    def test_coverage_bounds_and_subsequence(self):
        ev, rng = _base()
        noise = NoiseProfile("x", coverage_min=0.5)
        out = apply_operational_noise(ev, INSULIN, noise, rng)
        cc = out.cold_chain
        n0 = len(ev.cold_chain.readings)
        assert 0.5 * n0 - 1 <= len(cc.readings) <= n0
        assert cc.expected_readings == ev.cold_chain.expected_readings == n0
        # order-preserving subsequence of the original readings
        it = iter(ev.cold_chain.readings)
        assert all(any(abs(orig - kept) < 1e-12 for orig in it) for kept in cc.readings)

    def test_full_coverage_keeps_everything(self):
        ev, rng = _base()
        out = apply_operational_noise(ev, INSULIN, NoiseProfile("x", scan_error_rate=0.001), rng)
        assert len(out.cold_chain.readings) == len(ev.cold_chain.readings)


class TestCompositionAndReproducibility:

    def test_generate_noisy_normal_is_reproducible(self):
        a = generate_noisy_normal_evidence(INSULIN, NOISE_R2, np.random.default_rng(5))
        b = generate_noisy_normal_evidence(INSULIN, NOISE_R2, np.random.default_rng(5))
        assert a == b

    def test_different_seeds_differ(self):
        a = generate_noisy_normal_evidence(INSULIN, NOISE_R2, np.random.default_rng(5))
        b = generate_noisy_normal_evidence(INSULIN, NOISE_R2, np.random.default_rng(6))
        assert a != b

    def test_failures_inject_on_top_of_noise(self):
        rng = np.random.default_rng(9)
        path = ["manufacturer", "importer", "central_warehouse", "distributor_a", "pharmacy_a"]
        noisy = {n: generate_noisy_normal_evidence(AMOXICILLIN, NOISE_R1, rng) for n in path}
        f3 = get_standard_scenarios()["F3"]
        injected = apply_failure(noisy, f3, AMOXICILLIN, rng=rng)
        ser = injected["importer"].serialization
        assert ser.scanned_failure == 5 and ser.total_packages == 100
        # other nodes untouched by the injection
        for n in path:
            if n != "importer":
                assert injected[n] == noisy[n]

    def test_r1_is_milder_than_r2_on_average(self):
        rng1, rng2 = np.random.default_rng(1), np.random.default_rng(1)
        blips1 = blips2 = 0
        for _ in range(50):
            e1 = generate_noisy_normal_evidence(INSULIN, NOISE_R1, rng1)
            e2 = generate_noisy_normal_evidence(INSULIN, NOISE_R2, rng2)
            blips1 += len(_out_of_range(e1.cold_chain))
            blips2 += len(_out_of_range(e2.cold_chain))
        assert blips1 < blips2
