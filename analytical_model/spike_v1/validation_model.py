"""
run_010 validation spike: analytical model, not a simulator.

Idea: "Speculative prefetch pollution" -- gate/throttle long-distance software
prefetches (Magellan-style) using a per-branch dynamic reconvergence/confidence
signal (CHESS-style), to stop wrong-path prefetches from wasting real DRAM
bandwidth before a misprediction is caught.

Before building anything in gem5/ChampSim, this script puts numbers on the
three open questions flagged in run_010_literature_survey.md:

  Q1) Under realistic misprediction rates, what fraction of dispatched
      software prefetches are actually wrong-path? (Is the phenomenon itself
      large enough to matter?) NOTE: an earlier draft of this model derived
      the "branches in window" term as distance * branch_density, which
      saturates to ~100% wrong-path at any realistic prefetch distance --
      that's an artifact of treating every branch in the window as gating
      the prefetch's usefulness, when in reality only a small number of
      control-dependent branches actually do (same insight as the Ahead
      Prediction paper's "few missing history patterns" result). Fixed by
      sweeping a small, explicit GATING_BRANCHES count directly instead.
  Q2) How much real DRAM bandwidth does that consume, in absolute terms and
      as a percentage of a realistic channel budget?
  Q3) Existing REACTIVE utility-based prefetch throttling (already deployed,
      e.g. accuracy-counters per prefetch PC/stream) already recovers some of
      this "for free" -- how much residual survives it, as a function of how
      concentrated vs. diffuse the wrong-path waste is across static PCs?
      (This is the same "does an existing simple mechanism already solve it"
      test that killed run_005/run_011/run_047.)

Known-from-literature anchors used below:
  - ROB = 352 entries: CHESS's (ISCA'25) own simulated OoO baseline config.
  - DRAM: 3200 MT/s, matches CHESS's own simulated DRAM data rate.
  - MPKI range 5-30: matches the spread of graph/irregular-workload branch
    MPKI reported in the Ahead Prediction (ISCA'25) and LLBP (MICRO'24)
    evaluations (e.g. leela/mcf-class benchmarks).
  - Prefetch distance 128-2048 instructions: the idea's own framing
    ("hundreds to thousands of instructions ahead"), consistent with
    published software-prefetch-distance tuning ranges.
  - H2P (hard-to-predict) branch fraction 1-20%: matches the swept range used
    in the Bullseye / LLBP literature for graph/database/interpreter code.
Everything else (prefetch density, reactive-throttling recovery model) is an
explicit, clearly-labeled assumption swept over a plausible range, exactly
like the OFFSET_INDEX_BYTES / OUTLIER_RATE sweep in run_027's spike.
"""

import itertools

# ---------------- fixed, literature-anchored parameters ----------------
ROB_ENTRIES = 352            # CHESS (ISCA'25) simulated OoO baseline
CACHE_LINE_BYTES = 64
DRAM_DATA_RATE_MTS = 3200    # matches CHESS's simulated DRAM
DRAM_CHANNEL_BYTES_PER_S = DRAM_DATA_RATE_MTS * 1e6 * 8  # 8B bus width/channel
CLOCK_GHZ = 3.4              # CHESS baseline core frequency
IPC_ASSUMED = 1.5            # realistic sustained IPC for irregular/memory-bound code

# ---------------- swept / unknown parameters ----------------
BRANCH_DENSITY_OPTIONS = [1/8, 1/5]        # branches per instruction (irregular code)
MPKI_OPTIONS = [5, 10, 20, 30]             # branch mispredictions per kilo-instr
PREFETCH_DISTANCE_OPTIONS = [128, 256, 512, 1024, 2048]  # instructions ahead (context only)
GATING_BRANCHES_OPTIONS = [1, 2, 3, 5, 8]  # control-dependent branches per prefetch --
                                            # NOT derived from distance; see docstring
                                            # in p_wrong_path_prefetch for why
PREFETCH_DENSITY_PPKI_OPTIONS = [10, 30, 50]  # software prefetches per kilo-instr
H2P_FRACTION_OPTIONS = [0.01, 0.05, 0.20]  # fraction of branches that are H2P


def per_branch_correct_prob(mpki, branch_density):
    """Probability a single dynamic branch is predicted correctly."""
    branches_per_kilo = branch_density * 1000
    mispredicts_per_branch = mpki / branches_per_kilo
    return max(0.0, 1.0 - mispredicts_per_branch)


def p_wrong_path_prefetch(gating_branches, p_correct):
    """
    Probability that a prefetch issued now is on a path that gets squashed
    before the prefetched data would be used, i.e. the prefetch is wasted.

    IMPORTANT MODELING CHOICE: this is *not* every branch in the full
    [issue, issue+distance] instruction window -- most of those branches are
    control-independent of whether this specific prefetch's target is ever
    used (they reconverge quickly, same insight as the Ahead-Prediction
    paper's "few missing history patterns" result). Only the small number of
    branches that are actually control-dependent ("gating") on the prefetch's
    target matter. Treating every branch in the window as gating (i.e. using
    distance * branch_density directly) saturates to ~100% wrong-path at any
    realistic distance -- which would imply long-distance software
    prefetching can never work, contradicting Magellan's own measured
    speedups. So GATING_BRANCHES is swept directly as a small number instead
    of derived from distance, and is exactly the kind of parameter a real
    gem5/ChampSim characterization would need to measure, not guess.
    """
    p_all_correct = p_correct ** gating_branches
    return 1.0 - p_all_correct


# ---------------- Q1/Q2: wrong-path rate and DRAM bandwidth wasted ----------------
def wasted_bandwidth_gbps(p_wrong, ppki):
    wasted_prefetches_per_kilo = ppki * p_wrong
    instr_per_sec = CLOCK_GHZ * 1e9 * IPC_ASSUMED
    wasted_prefetches_per_sec = (wasted_prefetches_per_kilo / 1000.0) * instr_per_sec
    wasted_bytes_per_sec = wasted_prefetches_per_sec * CACHE_LINE_BYTES
    return wasted_bytes_per_sec / 1e9


# ---------------- Q3: reactive utility-based throttling recovery ----------------
def reactive_recovery_fraction(h2p_fraction):
    """
    Reactive, accuracy-counter-based throttling (already deployed) learns
    per-PC/per-stream historical accuracy and throttles persistently-bad
    prefetch sites. It is good at catching CONCENTRATED waste (a few static
    sites that are always wrong) but blind to DIFFUSE waste caused by
    data-dependent H2P branches scattered across many sites, since each
    individual site still looks "usually fine" in aggregate statistics.
    Modeled as linearly decaying recovery as H2P fraction (a proxy for how
    diffuse/data-dependent the misprediction sources are) increases.
    """
    max_h2p_swept = max(H2P_FRACTION_OPTIONS)
    base_recovery = 0.85  # reactive schemes recover most of *concentrated* waste
    return base_recovery * (1.0 - h2p_fraction / max_h2p_swept)


def main():
    print("=" * 100)
    print("Q1: Wrong-path software-prefetch rate vs. number of control-dependent (gating) branches")
    print("=" * 100)
    header = f"{'branch_dens':>11} {'mpki':>5} {'gating_br':>9} {'p_correct':>10} {'p_wrong':>8}"
    print(header)
    worst, best = 0, 1
    for bd, mpki, gb in itertools.product(BRANCH_DENSITY_OPTIONS, MPKI_OPTIONS, GATING_BRANCHES_OPTIONS):
        p_correct = per_branch_correct_prob(mpki, bd)
        p_wrong = p_wrong_path_prefetch(gb, p_correct)
        worst = max(worst, p_wrong)
        best = min(best, p_wrong)
        if (bd, mpki, gb) in [(1/8, 10, 2), (1/5, 20, 3), (1/8, 5, 1)]:
            print(f"{bd:>11.3f} {mpki:>5} {gb:>9} {p_correct:>10.4f} {p_wrong:>8.2%}")
    print(f"\nAcross full sweep: P(wrong-path prefetch) ranges {best:.2%} - {worst:.2%}")
    print("(Even with just 1-3 gating branches -- a conservative, defensible estimate --")
    print(" wrong-path rates are already substantial at realistic MPKI. This is the number")
    print(" a real gem5/ChampSim characterization must measure directly, not assume.)")

    print()
    print("=" * 100)
    print("Q2: Resulting wasted DRAM bandwidth (GB/s and % of one channel's budget)")
    print("=" * 100)
    channel_gbps = DRAM_CHANNEL_BYTES_PER_S / 1e9
    print(f"(Reference: one DRAM channel @ {DRAM_DATA_RATE_MTS} MT/s ~= {channel_gbps:.1f} GB/s)")
    print(f"{'mpki':>5} {'gating_br':>9} {'ppki':>5} {'p_wrong':>8} {'wasted_GB/s':>12} {'%_of_channel':>13}")
    for mpki, gb, ppki in itertools.product(MPKI_OPTIONS, GATING_BRANCHES_OPTIONS, PREFETCH_DENSITY_PPKI_OPTIONS):
        bd = 1/6  # representative mid-point branch density for this table
        p_correct = per_branch_correct_prob(mpki, bd)
        p_wrong = p_wrong_path_prefetch(gb, p_correct)
        wasted = wasted_bandwidth_gbps(p_wrong, ppki)
        pct = 100.0 * wasted / channel_gbps
        if (mpki, gb, ppki) in [(10, 2, 30), (20, 3, 30), (30, 5, 50), (5, 1, 10)]:
            print(f"{mpki:>5} {gb:>9} {ppki:>5} {p_wrong:>8.2%} {wasted:>12.2f} {pct:>12.1f}%")

    print()
    print("=" * 100)
    print("Q3: Residual after existing REACTIVE utility-based throttling")
    print("=" * 100)
    print(f"{'h2p_frac':>9} {'recovery':>9} {'residual_frac':>14}")
    for h2p in H2P_FRACTION_OPTIONS:
        recovery = reactive_recovery_fraction(h2p)
        residual = 1.0 - recovery
        print(f"{h2p:>9.2%} {recovery:>9.2%} {residual:>14.2%}")
    print("\nAt low H2P density, reactive per-PC throttling already recovers most waste")
    print("(small residual -> weak motivation for a new mechanism).")
    print("At high H2P density (diffuse, data-dependent mispredictions), reactive")
    print("throttling has little to grab onto -- the residual it leaves behind is")
    print("exactly the gap a proactive, per-branch reconvergence-gated scheme could fill.")


if __name__ == "__main__":
    main()
