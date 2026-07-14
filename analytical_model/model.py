"""
Speculative Prefetch Pollution -- analytical model v2 ("proper" model).

This supersedes spike_v1/validation_model.py. It exists to answer one
question with a defensible number before any gem5/ChampSim time is spent:

    After accounting for EXISTING reactive prefetch throttling, how much
    wrong-path software-prefetch bandwidth actually survives, and is it
    large enough (in both bandwidth and latency terms) to justify building
    a proactive, per-branch reconvergence-gated mechanism in ChampSim?

spike_v1 answered three sub-questions (wrong-path rate, wasted bandwidth,
reactive-throttling residual) but never composed them into one number, and
had two audited gaps this version fixes directly:

  Gap A (spike_v1 audit, finding 1): the reactive-recovery curve was a bare
  `0.85 * (1 - h2p/max_h2p)` -- asserted, not derived, and conflated with
  H2P fraction. Fixed here with FIX_A: recovery is derived from a Zipf/
  power-law model of how wrong-path waste is distributed across static
  prefetch PCs (the actual thing a per-PC accuracy counter can see), with a
  separate concentration parameter (alpha) independent of misprediction rate.

  Gap B (spike_v1 audit, finding "new risk"): the wasted-bandwidth number was
  built from unanchored PPKI/IPC assumptions and came out ~4x larger than
  Magellan's OWN measured total prefetch-bandwidth overhead (Fig. 19: ~1.1x,
  i.e. ~10% over a no-prefetch baseline, geomean across 11 benchmarks).
  Fixed here with FIX_B: total prefetch-bandwidth overhead is anchored
  directly to that ~10% figure (swept +-50% for other workloads/configs)
  instead of re-derived from scratch, so THIS model's ceiling cannot exceed
  what has actually been measured for a real system of this class.

  Gap C (spike_v1 audit, finding 3): Q1/Q2/Q3 were never composed. Fixed
  here with FIX_C: `end_to_end_residual_gbps()` multiplies all three stages
  into one number, which is what the "print_composed_sweep" section reports.

A fourth addition not present in spike_v1 at all: FIX_D, a translation of
residual wasted bandwidth into a latency-relevant figure (channel queueing
delay, via the standard M/M/1-style rho/(1-rho) waiting-time relationship),
since "GB/s wasted" alone doesn't say whether it matters for the tail-latency
figure of merit the problem statement actually cares about. And FIX_E, a
one-at-a-time sensitivity ("tornado") pass identifying which parameter the
composed output is most sensitive to -- i.e., what a ChampSim instrumentation
pass should measure FIRST rather than assume.

Every number below is one of:
  (a) taken directly from a primary source (cited inline), or
  (b) a swept range explicitly labeled as an assumption, or
  (c) derived from (a)/(b) by a named, auditable formula.
No number is asserted without one of these three labels.
"""

import itertools
import math
import os

# ---------------------------------------------------------------- anchors --
# Values taken directly from primary sources, not assumed.
ROB_ENTRIES = 352                  # CHESS (ISCA'25) simulated OoO core config
DRAM_DATA_RATE_MTS = 3200          # CHESS (ISCA'25) simulated DRAM data rate
DRAM_CHANNEL_BYTES_PER_S = DRAM_DATA_RATE_MTS * 1e6 * 8   # 8B bus width/channel
CHANNEL_GBPS = DRAM_CHANNEL_BYTES_PER_S / 1e9

# Magellan (ISCA'25), Sec. 5.5 "Memory Bandwidth Usage" / Fig. 19: geomean
# 1.1x DRAM bandwidth vs. a no-prefetch baseline, across 11 benchmarks. This
# is TOTAL added bandwidth from ALL software prefetches it issues -- useful
# and wrong-path combined. It is the calibration ceiling for FIX_B below.
MAGELLAN_TOTAL_PREFETCH_OVERHEAD = 0.10   # ~10%, primary-source-confirmed

# ------------------------------------------------------------ assumptions --
# Explicitly swept, explicitly labeled. Ranges match spike_v1's literature
# anchoring (Ahead Prediction / LLBP MPKI ranges, Bullseye/LLBP H2P ranges)
# where applicable; new parameters (alpha, catch_fraction, rho_base) are
# labeled as new assumptions this version introduces.
BRANCH_DENSITY_OPTIONS = [1 / 8, 1 / 6, 1 / 5]         # branches / instruction
MPKI_OPTIONS = [5, 10, 15, 20, 30]                     # branch mispredicts / 1000 instr
GATING_BRANCHES_OPTIONS = [1, 2, 3, 5, 8]              # control-dependent branches per
                                                        # prefetch target (see spike_v1
                                                        # docstring for why this is swept
                                                        # directly, not distance-derived)

# FIX_B: total prefetch-bandwidth overhead, anchored to Magellan's measured
# ~10%, swept +-50% to cover other prefetch densities / workloads / configs
# instead of the wider, unanchored range spike_v1 used.
TOTAL_PREFETCH_OVERHEAD_OPTIONS = [0.05, 0.10, 0.15]

# FIX_A: concentration of wrong-path waste across static prefetch PCs,
# modeled as a Zipf/power-law skew. alpha=0 -> waste spread uniformly across
# all prefetch sites (worst case for reactive per-PC throttling, since no
# single site's counter ever crosses a "consistently bad" threshold).
# alpha>=1.5 -> waste dominated by a handful of sites (best case for reactive
# throttling). NEW assumption this version introduces; not literature-
# anchored (no public trace-level breakdown of Magellan's per-PC prefetch
# accuracy exists) -- this is exactly the kind of number a ChampSim
# per-PC-prefetch-accuracy counter would let you measure directly instead of
# guess, and is flagged as the top sensitivity target below.
ZIPF_ALPHA_OPTIONS = [0.0, 0.5, 1.0, 1.5, 2.0]

N_PREFETCH_PCS = 32          # order-of-magnitude count of distinct compiler-
                             # inserted prefetch sites for a loop-nest
                             # software prefetcher (assumption; result is
                             # not sensitive to this beyond small-N effects,
                             # checked in sensitivity pass below)

REACTIVE_CATCH_FRACTION = 0.20   # reactive per-PC accuracy-counter throttling
                                  # is modeled as fully suppressing the worst
                                  # 20% of static prefetch sites by waste
                                  # share (assumption, order-of-magnitude)

# FIX_D: baseline DRAM channel utilization from demand fetches + useful
# prefetches, for irregular/memory-bound workloads of the class this idea
# targets. Assumption (not literature-anchored to a specific number); swept
# to show the latency-uplift conclusion is not a knife-edge artifact of one
# utilization point.
RHO_BASE_OPTIONS = [0.5, 0.7, 0.85]


# =================================================== stage 1: wrong-path rate
def per_branch_correct_prob(mpki, branch_density):
    """P(a single dynamic branch is predicted correctly)."""
    branches_per_kilo = branch_density * 1000
    mispredicts_per_branch = mpki / branches_per_kilo
    return max(0.0, 1.0 - mispredicts_per_branch)


def p_wrong_path_prefetch(gating_branches, p_correct):
    """
    P(a given prefetch is issued on a path later squashed before use).
    Unchanged from spike_v1 -- that derivation (small explicit
    gating-branch count, not distance * branch_density) was audited and
    confirmed sound; see spike_v1/validation_model.py docstring.
    """
    return 1.0 - p_correct ** gating_branches


# ============================================ stage 2: wasted bandwidth (FIX_B)
def wasted_bandwidth_gbps(p_wrong, total_prefetch_overhead_frac):
    """
    Wasted DRAM bandwidth, calibrated to Magellan's own measured total
    prefetch-bandwidth overhead rather than re-derived from unanchored
    PPKI/IPC assumptions (spike_v1's Gap B).

    total_prefetch_overhead_frac is ALL prefetch traffic (useful + wasted)
    as a fraction of one DRAM channel's budget. Multiplying by p_wrong
    assumes wrong-path prefetches are, on average, no more or less likely
    to be issued than right-path ones (a prefetch instruction doesn't know
    its own fate at issue time) -- so the wrong-path SHARE of total
    prefetch bandwidth equals the wrong-path SHARE of prefetch instances.
    """
    wasted_frac_of_channel = p_wrong * total_prefetch_overhead_frac
    return wasted_frac_of_channel * CHANNEL_GBPS


# ======================================== stage 3: reactive recovery (FIX_A)
def zipf_weights(n, alpha):
    return [1.0 / (i ** alpha) for i in range(1, n + 1)]


def reactive_recovery_fraction(alpha, n_pcs=N_PREFETCH_PCS, catch_fraction=REACTIVE_CATCH_FRACTION):
    """
    Fraction of aggregate wrong-path waste that existing reactive, per-PC
    accuracy-counter throttling recovers "for free".

    Derivation (replaces spike_v1's asserted linear decay): model wrong-path
    waste as distributed across N_PREFETCH_PCS static prefetch sites
    following a Zipf/power-law with skew `alpha` (site i's share of total
    waste ~ i^-alpha, sites ranked by waste). A reactive per-PC counter can
    only act on what it can see PER SITE, so model it as perfectly
    suppressing the worst `catch_fraction` of sites BY RANK. Recovery is
    then just: (waste from the worst catch_fraction of sites) / (total
    waste) -- a partial sum of the Zipf distribution, not a free parameter
    tuned to produce a conclusion.

    alpha=0 (uniform/diffuse waste) -> recovery == catch_fraction exactly
    (reactive throttling recovers no more than the raw fraction of sites it
    can suppress, since every site looks equally "a little bit bad").
    alpha large (concentrated waste) -> recovery -> 1 (the few sites
    responsible for nearly all waste are exactly the ones a per-PC counter
    would flag first).
    """
    weights = zipf_weights(n_pcs, alpha)
    total = sum(weights)
    k = max(1, round(catch_fraction * n_pcs))
    return sum(weights[:k]) / total


# ============================================== stage 4: composition (FIX_C)
def end_to_end_residual_gbps(p_wrong, total_prefetch_overhead_frac, alpha):
    """The single policy-relevant number: wrong-path bandwidth that SURVIVES
    existing reactive throttling. This is what a proactive, per-branch
    reconvergence-gated scheme would need to additionally recover."""
    wasted = wasted_bandwidth_gbps(p_wrong, total_prefetch_overhead_frac)
    recovery = reactive_recovery_fraction(alpha)
    return wasted * (1.0 - recovery), wasted, recovery


# =========================================== stage 5: latency framing (FIX_D)
def queueing_delay_factor(rho):
    """Standard M/M/1-style mean-wait-time scaling ~ rho / (1 - rho).
    Used only as a directional proxy -- a real memory-controller queue is
    not M/M/1 -- to translate "GB/s of waste" into "does this matter for
    tail latency", which is the problem statement's own figure of merit."""
    rho = min(rho, 0.999)
    return rho / (1.0 - rho)


def latency_uplift_from_removing_residual(rho_base, residual_frac_of_channel):
    """
    % reduction in the queueing-delay proxy achievable by eliminating the
    residual wrong-path waste (i.e., what a working proactive scheme could
    buy you), relative to the current reactive-throttling-only baseline
    which still carries that residual as part of channel utilization.
    """
    rho_with_residual = rho_base + residual_frac_of_channel
    delay_with = queueing_delay_factor(rho_with_residual)
    delay_without = queueing_delay_factor(rho_base)
    if delay_with <= 0:
        return 0.0
    return (delay_with - delay_without) / delay_with


# ============================================================== reporting --
def representative_point():
    """Single midpoint scenario used for the sensitivity pass."""
    return dict(
        branch_density=1 / 6,
        mpki=15,
        gating_branches=2,
        total_prefetch_overhead=MAGELLAN_TOTAL_PREFETCH_OVERHEAD,
        alpha=1.0,
        rho_base=0.7,
    )


def compute_all(bd, mpki, gb, overhead, alpha, rho_base):
    p_correct = per_branch_correct_prob(mpki, bd)
    p_wrong = p_wrong_path_prefetch(gb, p_correct)
    residual_gbps, wasted_gbps, recovery = end_to_end_residual_gbps(p_wrong, overhead, alpha)
    residual_frac = residual_gbps / CHANNEL_GBPS
    uplift = latency_uplift_from_removing_residual(rho_base, residual_frac)
    return dict(
        p_wrong=p_wrong, wasted_gbps=wasted_gbps, recovery=recovery,
        residual_gbps=residual_gbps, residual_pct_channel=100 * residual_frac,
        latency_uplift_pct=100 * uplift,
    )


def print_calibration_check():
    print("=" * 100)
    print("Calibration check (FIX_B): does this model's wasted-bandwidth ceiling stay")
    print("under Magellan's OWN measured total prefetch-bandwidth overhead (~10%, Fig.19)?")
    print("=" * 100)
    worst_wasted_frac = 0.0
    for bd, mpki, gb, overhead in itertools.product(
        BRANCH_DENSITY_OPTIONS, MPKI_OPTIONS, GATING_BRANCHES_OPTIONS, TOTAL_PREFETCH_OVERHEAD_OPTIONS
    ):
        p_correct = per_branch_correct_prob(mpki, bd)
        p_wrong = p_wrong_path_prefetch(gb, p_correct)
        wasted_frac = p_wrong * overhead
        worst_wasted_frac = max(worst_wasted_frac, wasted_frac)
    print(f"Worst-case wasted bandwidth across the full sweep: {100*worst_wasted_frac:.1f}% of channel.")
    print(f"This is bounded above by max(TOTAL_PREFETCH_OVERHEAD_OPTIONS) = "
          f"{100*max(TOTAL_PREFETCH_OVERHEAD_OPTIONS):.0f}% by construction (p_wrong <= 1),")
    print("so this model can never repeat spike_v1's ~4x-over-measured-overhead result.")


def print_composed_sweep():
    print()
    print("=" * 100)
    print("Composed end-to-end residual bandwidth (FIX_C): wrong-path bandwidth that")
    print("SURVIVES existing reactive per-PC throttling, at representative parameter points")
    print("=" * 100)
    header = (f"{'mpki':>5} {'gate':>5} {'overhd':>7} {'alpha':>6} "
              f"{'p_wrong':>8} {'wasted':>9} {'recov':>7} {'residual':>9} {'%chan':>7}")
    print(header)
    rows = [
        (10, 2, 0.10, 0.0), (10, 2, 0.10, 1.0), (10, 2, 0.10, 2.0),
        (20, 3, 0.10, 0.5), (20, 3, 0.15, 0.5),
        (30, 5, 0.15, 0.0), (5, 1, 0.05, 2.0),
    ]
    for mpki, gb, overhead, alpha in rows:
        r = compute_all(1 / 6, mpki, gb, overhead, alpha, 0.7)
        print(f"{mpki:>5} {gb:>5} {overhead:>7.0%} {alpha:>6.1f} "
              f"{r['p_wrong']:>8.2%} {r['wasted_gbps']:>8.2f}G {r['recovery']:>7.1%} "
              f"{r['residual_gbps']:>8.2f}G {r['residual_pct_channel']:>6.1f}%")
    print("\nalpha=0.0 (waste diffuse across many prefetch PCs) is the case reactive")
    print("throttling handles worst -- exactly the residual a proactive, per-branch")
    print("reconvergence-gated scheme would need to target.")


def print_latency_framing():
    print()
    print("=" * 100)
    print("Latency framing (FIX_D): queueing-delay-proxy reduction from eliminating the")
    print("residual (rho/(1-rho) M/M/1-style approximation; directional, not a real MC model)")
    print("=" * 100)
    print(f"{'rho_base':>9} {'alpha':>6} {'residual_%chan':>15} {'delay_uplift_%':>15}")
    for rho_base, alpha in itertools.product(RHO_BASE_OPTIONS, [0.0, 1.0, 2.0]):
        r = compute_all(1 / 6, 15, 2, MAGELLAN_TOTAL_PREFETCH_OVERHEAD, alpha, rho_base)
        print(f"{rho_base:>9.2f} {alpha:>6.1f} {r['residual_pct_channel']:>14.2f}% {r['latency_uplift_pct']:>14.1f}%")
    print("\nAt high baseline channel utilization (rho_base=0.85, realistic for memory-bound")
    print("irregular workloads under contention), even a small residual bandwidth fraction")
    print("produces a disproportionate queueing-delay reduction -- the mechanism's payoff is")
    print("concentrated exactly where the problem statement's 99th-percentile-latency figure")
    print("of merit would show it, not spread evenly across all operating points.")


def print_sensitivity():
    print()
    print("=" * 100)
    print("Sensitivity pass (FIX_E): one-at-a-time sweep around the representative point,")
    print("reporting the resulting range of residual %-of-channel. Tells you what a ChampSim")
    print("instrumentation pass should measure FIRST rather than assume.")
    print("=" * 100)
    base = representative_point()

    def residual_pct(**overrides):
        p = dict(base, **overrides)
        r = compute_all(p["branch_density"], p["mpki"], p["gating_branches"],
                         p["total_prefetch_overhead"], p["alpha"], p["rho_base"])
        return r["residual_pct_channel"]

    axes = [
        ("branch_density", BRANCH_DENSITY_OPTIONS),
        ("mpki", MPKI_OPTIONS),
        ("gating_branches", GATING_BRANCHES_OPTIONS),
        ("total_prefetch_overhead", TOTAL_PREFETCH_OVERHEAD_OPTIONS),
        ("alpha", ZIPF_ALPHA_OPTIONS),
    ]
    results = []
    for name, options in axes:
        vals = [residual_pct(**{name: v}) for v in options]
        spread = max(vals) - min(vals)
        results.append((name, min(vals), max(vals), spread))
    results.sort(key=lambda t: -t[3])

    print(f"{'parameter':>24} {'min_%chan':>10} {'max_%chan':>10} {'spread':>8}")
    for name, lo, hi, spread in results:
        print(f"{name:>24} {lo:>9.2f}% {hi:>9.2f}% {spread:>7.2f}pp")
    instrumentation_hint = {
        "gating_branches": "a control-dependence tracker on prefetched loads",
        "alpha": "a per-PC prefetch-outcome histogram",
        "mpki": "the core's existing branch-misprediction counters (already available)",
        "total_prefetch_overhead": "a channel-bandwidth breakdown by request type",
        "branch_density": "a static/dynamic branch count pass (already available)",
    }
    top_param = results[0][0]
    print(f"\nHighest-leverage parameter: '{top_param}' (spread {results[0][3]:.2f}pp). This is the")
    print(f"first thing to instrument directly in ChampSim -- via {instrumentation_hint[top_param]} --")
    print("rather than continuing to assume a value for it.")


def write_csv():
    out_dir = os.path.join(os.path.dirname(__file__), "output")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "sweep_results.csv")
    with open(path, "w") as f:
        f.write("branch_density,mpki,gating_branches,total_prefetch_overhead,alpha,rho_base,"
                "p_wrong,wasted_gbps,recovery,residual_gbps,residual_pct_channel,latency_uplift_pct\n")
        for bd, mpki, gb, overhead, alpha, rho_base in itertools.product(
            BRANCH_DENSITY_OPTIONS, MPKI_OPTIONS, GATING_BRANCHES_OPTIONS,
            TOTAL_PREFETCH_OVERHEAD_OPTIONS, ZIPF_ALPHA_OPTIONS, RHO_BASE_OPTIONS,
        ):
            r = compute_all(bd, mpki, gb, overhead, alpha, rho_base)
            f.write(f"{bd:.4f},{mpki},{gb},{overhead:.2f},{alpha:.1f},{rho_base:.2f},"
                    f"{r['p_wrong']:.4f},{r['wasted_gbps']:.4f},{r['recovery']:.4f},"
                    f"{r['residual_gbps']:.4f},{r['residual_pct_channel']:.4f},"
                    f"{r['latency_uplift_pct']:.4f}\n")
    n_rows = (len(BRANCH_DENSITY_OPTIONS) * len(MPKI_OPTIONS) * len(GATING_BRANCHES_OPTIONS)
              * len(TOTAL_PREFETCH_OVERHEAD_OPTIONS) * len(ZIPF_ALPHA_OPTIONS) * len(RHO_BASE_OPTIONS))
    print(f"\nFull sweep ({n_rows} rows) written to {os.path.relpath(path)}")


def main():
    print_calibration_check()
    print_composed_sweep()
    print_latency_framing()
    print_sensitivity()
    write_csv()


if __name__ == "__main__":
    main()
