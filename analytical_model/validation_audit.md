# Validation Audit: run_010 — "Speculative Prefetch Pollution"

Follow-up, independent thorough validation of the existing literature survey and
analytical spike. Two tracks: (1) re-execute and audit the analytical model's
logic, (2) independently re-verify the literature claims rather than trusting
the survey's own citations at face value.

## 1. Code audit — `run_010_validation_model.py`

**Re-ran the script.** Every printed number matches the figures quoted in
`run_010_literature_survey.md` exactly (Q1: 2.5%-88.87%; Q2: 0.4%-40.1% of
channel; Q3: 19.25%/36.25%/100% residual). Hand-verified the arithmetic for
one full row (mpki=10, gating=2, ppki=30, bd=1/6 → p_wrong=11.64% → 1.14 GB/s
→ 4.5% of a 25.6 GB/s channel) — all formulas are dimensionally and
numerically consistent. No bugs in the executed code.

**Two methodological gaps found that the original spike doesn't flag:**

1. **Q3's recovery curve is asserted, not derived, and it's the load-bearing
   number.** `reactive_recovery_fraction()` is a straight linear decay from an
   arbitrary `base_recovery = 0.85` down to 0 as H2P fraction increases to its
   max swept value. Nothing in the literature survey grounds this curve shape
   — it's a clean, monotonic function built to produce exactly the "reactive
   throttling fails at high H2P density" conclusion the idea needs. That's a
   fair *illustrative* sweep for a spike, but the survey's Q3 narrative reads
   more confidently than "we assumed a linear decay and it produced the
   result we wanted" — worth being explicit about in any follow-on write-up.

2. **H2P fraction and "waste concentration" are conflated.** The model treats
   "fraction of branches that are hard-to-predict" as a direct proxy for
   "how diffuse the resulting wrong-path prefetch waste is across static
   PCs" — but these are different axes. A workload could have a small H2P
   fraction where those branches are still scattered across many distinct
   prefetch sites (diffuse, reactive-throttling-resistant), or a large H2P
   fraction concentrated at a few sites (which reactive throttling handles
   fine). The model silently assumes these move together.

3. **Q1/Q2/Q3 are never composed into one end-to-end number.** The script
   never computes "wasted bandwidth (Q2) × residual after throttling (Q3)" —
   the single most policy-relevant figure (how much *actually survives*
   existing mitigation, in GB/s) is never actually produced. Each question is
   answered in isolation.

`PREFETCH_DISTANCE_OPTIONS` is defined but never consumed by any sweep
(intentional per the in-code comment — distance was deliberately decoupled
from gating-branch count after the earlier saturation bug — but it's dead
data now, not a bug).

## 2. Literature re-verification (independent of the survey's own citations)

| Claim | Verdict | Notes |
|---|---|---|
| Magellan is real, ISCA 2025 | **Confirmed** | Fu, Xia, Yin, Nair, Lis, Ren, ACM DOI 10.1145/3695053.3731054, pp. 601–615. |
| CHESS is real, ISCA 2025, reconvergence signal never connected to prefetch gating | **Confirmed** | Volos, Vassiliou, Antoniou, Bartolini, Sazeides. Description (history+static-hint+similarity predictor using a reference trace) matches survey; no prefetch-gating design discussed in any summary found. |
| "LLBP-X" / "False Path Utility" is a real LLBP finding | **Not found — survey's fabrication flag corroborated.** | Real LLBP (Schall, Sandberg, Grot, MICRO 2024) is about last-level branch-predictor context lookup/prefetching of predictor metadata itself — nothing resembling a "false path utility" finding turned up anywhere. Independent search agrees with the survey: this citation should be dropped. |
| Mutlu et al. wrong-path-usefulness paper is real and a valid replacement citation | **Confirmed, and slightly better than the survey stated** | It's not just an HPS tech report — it was also published at **HPCA 2008** (Lee, Kim, Mutlu, Patt, "Performance-Aware Speculation Control Using Wrong Path Usefulness Prediction," pp. 39–49). Cite the peer-reviewed HPCA version, not just the TR. |
| US Patent 6438656 describes early-only prefetch cancellation | **Confirmed** | Cancel command is honored *unless* the bus cycle has already progressed to driving address lines (or the bus is in a wait state) — exactly the "before memory-controller commit" boundary the survey describes. |

### Update: primary source obtained and checked directly (2026-07-13)

The user supplied the actual Magellan PDF (`3695053.3731054.pdf`, now in this
folder). Extracted with `pdftotext -layout` and grepped directly — both
open questions below are now **confirmed from primary text**, not inferred
from search snippets.

**Bandwidth overhead — confirmed exactly as flagged.** §5.5 ("Memory
Bandwidth Usage"), Fig. 19:

> "Fig.19 depicts the DRAM bandwidth usage of Magellan, which slightly boosts
> bandwidth, reaching an average of 1.1× bandwidth than the non-prefetching
> baseline... Magellan does not impose significant extra pressure on the
> memory bandwidth, which means that most prefetching requests turn into
> useful memory accesses for demand loads."

This is a geomean across all 11 benchmarks (bfs, sssp, bc, cc, dc, is, cg,
symgs, spmv, pr). It measures **all** bandwidth Magellan's prefetches add
over a no-prefetch baseline — useful and wasted, right-path and wrong-path,
combined. **The calibration concern stands and is now confirmed, not just
probable:** the analytical spike's upper-range Q2 output (up to 40% of one
DRAM channel's budget wasted by wrong-path prefetches *alone*) is roughly
4× larger than the *total* prefetch-bandwidth overhead (~10%) that Magellan's
own paper reports for a real system of exactly the class this idea targets.
Any gem5/ChampSim follow-up must reconcile its wrong-path fraction against
this ~10%-total ceiling — the swept parameter combinations that produce 40%
in the spike are not just conservative, they're inconsistent with the
primary source's own measurements unless wrong-path waste in Magellan's
benchmarks is a near-total share of an already-small overhead budget.

**Misprediction discussion — found, and it *strengthens* rather than
undermines the idea.** §4.4 (memory-allocation extension), discussing why
prefetched addresses must stay in bounds even on a mispredicted path:

> "branch misprediction deteriorates this situation. Though speculative
> accesses on mispredicted paths are not architecturally viable, recently
> advanced side-channel attacks... demonstrate that load instructions on
> mispredicted paths can be utilized to load illegal data into cache state...
> Therefore, Magellan needs to ensure that loads on the mispredicted path
> also target the safe memory space. Because if the branch predictor
> mistakenly predicts the direction of the inner loop branch, j would
> increase mistakenly, and prefetched index j+pref_d would increase
> accordingly."

This is a **correctness/security concern** (avoiding out-of-bounds accesses
and side-channel exposure from wrong-path loads) — never framed as a
bandwidth-waste/performance problem. This is, almost verbatim, the mechanism
the problem statement's own `INSPIRATION_TRACE` item 1 names: *"Magellan's
'Allocation Extension for Fault Avoidance': ...transforming a correctness
problem (faults on wrong-path prefetches) into a performance problem (wasted
bandwidth)."* That inspiration claim is now **directly confirmed from primary
text**, which is a real point in the idea's favor.

It also means the survey's phrasing — "**no evidence** Magellan['s] ...
discusses wrong-path speculation, branch-misprediction interaction... at
all" — was too absolute and is now supersedable: Magellan discusses
mispredicted-path prefetches at some length, just exclusively for
correctness/safety, never once connecting it to DRAM bandwidth or
performance. **The novelty gap itself survives** (the specific
correctness→performance reframing is confirmed absent from Magellan's own
framing), it just needs the more precise sentence above instead of "zero
discussion at all."

## 3. Overall verdict

- **Code:** correct and internally consistent; no computational bugs. Two
  documented, non-fatal methodological gaps (arbitrary Q3 recovery curve
  conflating H2P fraction with waste concentration; no composed end-to-end
  residual-bandwidth number).
- **Citations:** four of five independently re-checked and confirmed exactly
  as characterized; the fifth ("LLBP-X") is corroborated as fabricated/should
  be dropped, with a confirmed, slightly-upgraded real replacement (HPCA'08,
  not just a TR).
- **Novelty gap:** still holds after independent re-check, with one small
  wording correction (Magellan mentions misprediction once, for a different
  reason than the idea's mechanism).
- **New risk surfaced by this audit:** the analytical spike's headline
  worst-case bandwidth-waste number (~40%) is unvalidated against a real,
  probable data point (Magellan's own ~10% total prefetch-bandwidth overhead)
  that would have been a natural sanity check. Recommend adding this
  cross-check explicitly before any gem5/ChampSim infrastructure investment,
  and citing the HPCA 2008 Mutlu et al. paper (not the TR) if this survives
  to a proposal document.
