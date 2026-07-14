# Phase 2 Results: Instrumentation

Goal: replace `analytical_model/model.py`'s two swept, unmeasured assumptions
(`gating_branches`, `alpha`) with empirical measurements from ChampSim.

## What was built

- `champsim_custom/instrumentation/branch_log.h` — header-only, shared event
  log. Records every conditional/other branch's real predicted-vs-actual
  outcome with a monotonic sequence number.
- `champsim_custom/patches/ooo_cpu_branch_instrumentation.patch` — a ~20-line
  additive patch to `champsim/src/ooo_cpu.cc`'s `do_predict_branch`, logging
  every conditional/other branch via the header above. Apply with
  `champsim_custom/patches/apply_patches.sh` after any fresh
  `git submodule update` (the submodule itself is never modified in git —
  see that script and the patch file for why this is tracked as a patch
  rather than a submodule commit).
- `champsim_custom/prefetcher/loop_guided/` (extended) — on each issued
  prefetch, records the branch-log sequence number into a per-PC pending
  queue. On the next real occurrence of that PC, pops the oldest pending
  sequence number and computes:
  - `gating_branches` = (branch-log seq at use) − (branch-log seq at issue):
    how many conditional/other branches retired in between.
  - `wasted` = whether any of those branches mispredicted — the proxy for
    "this prefetch would have been on a wrong path in a deep-ROB machine"
    (see `loop_guided.h` and `analytical_model/README.md` for why this proxy
    doesn't require modeling genuine wrong-path instruction fetch, which
    ChampSim's core doesn't do at all).
  - Aggregated per static prefetch PC (for `alpha`) and as a histogram (for
    `gating_branches`), dumped to CSV in `prefetcher_final_stats()`.

## Validation: purely additive

Same trace, same config, same warmup/simulation window as Phase 1:
IPC **0.5368** (identical to Phase 1's 0.537), L2C prefetch issued **88,430**
(identical). Confirms the instrumentation changes nothing about simulated
behavior — it only observes.

## Measured data (429.mcf, 1M warmup / 5M simulation instructions)

Of 88,430 issued prefetches, 42,353 (48%) were matched to a real future
occurrence within this short window (the rest were still pending at
simulation end — a left-censoring artifact of the short window, not a bug).

**`gating_branches` histogram** (bucketed, capped at 32+): strongly bimodal —
11.6% of matched prefetches (4,924) had **0** intervening conditional
branches (immediate reuse), while 87.8% (37,195) hit the **32+ cap**. Very
little mass in between.

**Per-PC waste fraction** (5 static prefetch PCs observed):

| PC | total | wasted | wasted_fraction |
|---|---|---|---|
| 0x401682 | 10,300 | 10,239 | 99.4% |
| 0x401660 | 3,557 | 3,382 | 95.1% |
| 0x40166d | 493 | 276 | 56.0% |
| 0x401669 | 14,044 | 11,459 | 81.6% |
| 0x401671 | 13,959 | 11,551 | 82.7% |

## Important caveat: these numbers are very likely inflated, not yet trustworthy as-is

The wasted fractions (56–99%) are far higher than the analytical model's
swept range (worst case 63% at MPKI=30/gating=5). The `gating_branches`
bimodal spike at the 32+ cap is the reason why, and it points to a real
methodological gap in this measurement, not a finding about the phenomenon
itself:

**`gating_branches` here counts every conditional branch that retires
between two occurrences of a tracked PC — not just branches that are
actually control-dependent on that specific prefetch's target.** mcf is a
large, multi-function program; between two visits to the same hot loop load,
the CPU very plausibly retires branches from other, unrelated loops or
subroutines that have nothing to do with whether *this* prefetch's target is
valid. That inflates the measured window far beyond the "1–3 gating
branches" the analytical model's literature-anchored sweep assumed, and with
a wide enough window, "at least one mispredict occurred" becomes true almost
by default — explaining the nearly-universal waste rates observed.

**This is not yet a result that should replace the analytical model's swept
assumptions.** The next necessary refinement, before trusting this data:
scope the branch count to only branches plausibly inside the *same* loop as
the tracked load (e.g. by also logging each branch's IP in `branch_log` and
restricting the count to branches within a bounded IP distance of the
tracked PC, or by tracking the loop's own specific back-edge branch instead
of "all conditional branches retired in the window"). That's a contained
change to `branch_log.h` (add an IP field) and `loop_guided.cc`'s counting
logic — not a new phase, but real work still needed on Phase 2's own
methodology before its output is usable.

## Status

Phase 2's instrumentation plumbing works correctly and is purely additive
(validated above). Its current output is not yet reliable enough to feed
back into `analytical_model/model.py` — that requires the loop-scoping
refinement described above first.
