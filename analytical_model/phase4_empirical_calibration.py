"""
Phase 4: feed real ChampSim-measured data into the analytical model in place
of its swept assumptions, and check whether the composed residual-bandwidth
conclusion survives.

Real inputs below are transcribed directly from a fresh run of
champsim_custom/prefetcher/loop_guided (v4, PREFETCH_DISTANCE_ITERS=1) on
429.mcf (1M warmup / 5M simulation instructions) -- see the numbers echoed
in each section below and cross-check against
results/phase4_data/pf_per_pc_waste.csv / pf_gating_branches_histogram.csv
(gitignored; regenerate with champsim_custom/README.md's build+run steps)
and champsim_custom/PHASE2_RESULTS.md / PHASE3_RESULTS.md for the narrative.

Only 0x40166d, 0x401660, 0x401671, 0x401669 are used -- 0x401682 (and the
n=1 0x401297) are excluded per Phase 2's own trustworthiness assessment
(pathological queue_evictions/stale_dropped).

Run: python3 analytical_model/phase4_empirical_calibration.py
"""

from model import (
    CHANNEL_GBPS,
    MAGELLAN_TOTAL_PREFETCH_OVERHEAD,
    latency_uplift_from_removing_residual,
    per_branch_correct_prob,
    reactive_recovery_fraction,
)

# ============================================================ real inputs
# pc_hex -> (total matched, wasted) -- from pf_per_pc_waste.csv, this run
REAL_PER_PC = {
    "0x40166d": (377, 140),
    "0x401660": (2936, 2013),
    "0x401671": (9480, 1739),
    "0x401669": (9509, 1742),
}

# ChampSim's own reported branch stats for this exact run (printed directly
# by the simulator, not derived by us)
REAL_MPKI = 2.456
REAL_BRANCH_ACCURACY = 0.9717

# From Phase 3 (PHASE3_RESULTS.md): measured total prefetch-bandwidth
# overhead at this same PREFETCH_DISTANCE_ITERS=1 setting
REAL_TOTAL_OVERHEAD_MEASURED = 0.00002  # 0.002%

# Real gating_branches distribution summary (from
# pf_gating_branches_histogram.csv, this run): total 22,475 samples,
# mean 1.843, median 1, bucket-0 share 38.7%, bucket-1 share 44.0%,
# 32+ overflow share 0.55%.
REAL_GATING_BRANCHES_MEAN = 1.843


def main():
    print("=" * 100)
    print("1. Real empirical wrong-path rate (p_wrong), aggregated across the 4 trustworthy PCs")
    print("=" * 100)
    total_matched = sum(t for t, w in REAL_PER_PC.values())
    total_wasted = sum(w for t, w in REAL_PER_PC.values())
    p_wrong_empirical = total_wasted / total_matched
    print(f"{'PC':>10} {'total':>7} {'wasted':>7} {'p_wrong':>8}")
    for pc, (t, w) in REAL_PER_PC.items():
        print(f"{pc:>10} {t:>7} {w:>7} {w/t:>7.1%}")
    print(f"{'AGGREGATE':>10} {total_matched:>7} {total_wasted:>7} {p_wrong_empirical:>7.1%}")
    print(f"\nCompare to the original v2 model's swept p_wrong range: 2.5%-88.9%")
    print(f"(computed from swept MPKI/branch_density/gating_branches, not measured).")
    print(f"25.3% sits inside that swept range, closer to the low-to-middle of it.")

    print()
    print("=" * 100)
    print("2. Cross-check against the model's formula using ChampSim's own REAL branch stats")
    print("=" * 100)
    # branch_density back-derived from ChampSim's own reported MPKI + accuracy
    # (algebraic identity, not an independent measurement -- see note below)
    mispredict_rate_per_branch = 1 - REAL_BRANCH_ACCURACY
    branch_density = (REAL_MPKI / 1000) / mispredict_rate_per_branch
    p_correct = per_branch_correct_prob(REAL_MPKI, branch_density)
    print(f"Real MPKI = {REAL_MPKI}, real branch accuracy = {REAL_BRANCH_ACCURACY:.2%} (both printed directly by ChampSim).")
    print(f"Back-derived branch_density = {branch_density:.4f} ({1/branch_density:.1f} instructions/branch) "
          f"-- below the model's originally swept range (1/8-1/5 = 0.125-0.200).")
    print(f"[This derivation is algebraic, not independent: branch_density is solved FROM the accuracy figure, "
          f"so per_branch_correct_prob reproducing {p_correct:.2%} is a consistency check, not new evidence.]")
    print()
    print(f"{'gating_branches':>16} {'formula p_wrong':>16}")
    for gb in [1, REAL_GATING_BRANCHES_MEAN, 2, 3, 5, 8]:
        pw = 1 - p_correct ** gb
        label = f"{gb:.3f}" if isinstance(gb, float) else str(gb)
        print(f"{label:>16} {pw:>15.1%}")
    formula_p_wrong_at_real_gating = 1 - p_correct ** REAL_GATING_BRANCHES_MEAN
    implied_local_p_correct = (1 - p_wrong_empirical) ** (1 / REAL_GATING_BRANCHES_MEAN)
    print(f"\nAt the REAL mean gating_branches ({REAL_GATING_BRANCHES_MEAN}), the formula predicts "
          f"{formula_p_wrong_at_real_gating:.1%} wrong-path -- about "
          f"{p_wrong_empirical/formula_p_wrong_at_real_gating:.1f}x BELOW the {p_wrong_empirical:.1%} actually measured.")
    print(f"Solving the formula backwards: matching {p_wrong_empirical:.1%} at gating={REAL_GATING_BRANCHES_MEAN} "
          f"would require a LOCAL branch accuracy of {implied_local_p_correct:.1%} for the identified gating "
          f"branch (0x40169e), well below the {REAL_BRANCH_ACCURACY:.1%} PROGRAM-WIDE average.")
    print("Plausible reading: the model's formula uses a single global branch accuracy, but the specific")
    print("branches that gate long-distance prefetches are disproportionately hard-to-predict (H2P) branches,")
    print("not average ones -- consistent with the literature survey's own H2P framing, and with this project's")
    print("core premise that per-branch (not program-average) confidence is the thing worth gating on.")

    print()
    print("=" * 100)
    print("3. Real waste concentration -> empirical reactive-recovery estimate")
    print("=" * 100)
    ranked = sorted(REAL_PER_PC.items(), key=lambda kv: -kv[1][1])
    print(f"{'PC':>10} {'wasted':>7} {'share of total wasted':>22}")
    for pc, (t, w) in ranked:
        print(f"{pc:>10} {w:>7} {w/total_wasted:>21.1%}")
    catch_1_of_4 = ranked[0][1][1] / total_wasted
    print(f"\nOnly {len(REAL_PER_PC)} distinct prefetch-issuing PCs were found in this workload/prefetcher "
          f"combination -- far fewer than the model's originally assumed N_PREFETCH_PCS=32. Reactive per-PC "
          f"throttling catching just the single worst real PC (0x401660) would recover "
          f"{catch_1_of_4:.1%} of total measured waste, leaving {1-catch_1_of_4:.1%} residual.")
    # what Zipf alpha, at the REAL n_pcs, best reproduces this empirical recovery?
    best_alpha, best_diff = None, 1e9
    for alpha_x100 in range(0, 401):
        alpha = alpha_x100 / 100
        r = reactive_recovery_fraction(alpha, n_pcs=len(REAL_PER_PC), catch_fraction=1 / len(REAL_PER_PC))
        diff = abs(r - catch_1_of_4)
        if diff < best_diff:
            best_diff, best_alpha = diff, alpha
    print(f"Best-fit Zipf alpha reproducing this at n_pcs={len(REAL_PER_PC)}, catch_fraction=1/{len(REAL_PER_PC)}: "
          f"alpha ~= {best_alpha:.2f} (recovery {reactive_recovery_fraction(best_alpha, len(REAL_PER_PC), 1/len(REAL_PER_PC)):.1%}) "
          f"-- lands within the originally swept ZIPF_ALPHA_OPTIONS range (0.0-2.0), roughly mid-range.")

    print()
    print("=" * 100)
    print("4. Composed residual bandwidth: three scenarios")
    print("=" * 100)
    recovery_empirical = catch_1_of_4

    def compose(p_wrong, overhead, recovery, label):
        wasted_frac = p_wrong * overhead
        residual_frac = wasted_frac * (1 - recovery)
        residual_gbps = residual_frac * CHANNEL_GBPS
        print(f"{label}")
        print(f"  p_wrong={p_wrong:.1%}  overhead={overhead:.4%}  recovery={recovery:.1%}  "
              f"-> residual = {residual_frac:.4%} of channel ({residual_gbps:.4f} GB/s)")
        return residual_frac

    r_a = compose(p_wrong_empirical, REAL_TOTAL_OVERHEAD_MEASURED, recovery_empirical,
                  "(A) Fully our own measured numbers (p_wrong + overhead both from this ChampSim run):")
    print("     Caveat: Phase 3 already flagged our own measured overhead as unrepresentatively low")
    print("     (2-3 orders of magnitude below Magellan's own ~10%) due to the conservative distance=1")
    print("     tuning chosen for Phase 2 measurement reliability -- this scenario mostly reflects that,")
    print("     not the real-world magnitude of the phenomenon.")
    print()
    r_b = compose(p_wrong_empirical, MAGELLAN_TOTAL_PREFETCH_OVERHEAD, recovery_empirical,
                  "(B) Real wrong-path rate + real concentration, Magellan-anchored overhead (10%):")
    print("     Rationale: use real data where Phase 2/3 validated it (p_wrong, concentration), keep the")
    print("     literature anchor where Phase 3 showed our own setup understates it (total overhead).")
    print()
    print(f"Original v2 model's swept composed range (analytical_model/FINDINGS.md): 0.1%-2.0% typical, "
          f"7.7% stress case.")
    print(f"Scenario B ({r_b:.2%}) lands INSIDE that typical range.")
    print(f"Scenario A ({r_a:.4%}) lands far below it -- expected, given the Phase 3 caveat above.")

    print()
    print("=" * 100)
    print("5. Latency-uplift framing with the real (scenario B) residual")
    print("=" * 100)
    for rho_base in [0.5, 0.7, 0.85]:
        uplift = latency_uplift_from_removing_residual(rho_base, r_b)
        print(f"rho_base={rho_base:.2f}  ->  delay-proxy uplift from eliminating the real residual: {uplift:.1%}")


if __name__ == "__main__":
    main()
