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
  - `gating_branches` = how many times this PC's **identified gating
    branch** retired between issue and use.
  - `wasted` = whether any of those specific occurrences mispredicted — the
    proxy for "this prefetch would have been on a wrong path in a deep-ROB
    machine" (see `loop_guided.h` and `analytical_model/README.md` for why
    this proxy doesn't require modeling genuine wrong-path instruction
    fetch, which ChampSim's core doesn't do at all).
  - Aggregated per static prefetch PC (for `alpha`) and as a histogram (for
    `gating_branches`), dumped to CSV in `prefetcher_final_stats()`.

  **v2 refinement:** each tracked PC's "identified gating branch" is
  estimated as whichever conditional/other branch IP most often immediately
  precedes an occurrence of that PC (a proxy for the loop's own back-edge
  check). `gating_branches`/`wasted` are now scoped to only that specific
  branch IP within the issue-to-use window, replacing v1's count of *every*
  conditional branch in that window (see "v1 finding" below for why that
  was necessary).

## Validation: purely additive

Same trace, same config, same warmup/simulation window as Phase 1:
IPC **0.5368** (identical to Phase 1's 0.537), L2C prefetch issued **88,430**
(identical). Confirms the instrumentation changes nothing about simulated
behavior — it only observes.

## v1 finding: unscoped counting inflated everything (fixed, kept for record)

The first pass counted *every* conditional branch retiring between two
occurrences of a tracked PC, not just branches actually control-dependent on
that prefetch's target. Result: 87.8% of matched prefetches hit a 32-branch
overflow cap, and per-PC waste fractions were 56–99% — far above the
analytical model's swept ceiling (63% worst case). mcf is a large,
multi-function program; between two visits to the same hot loop load, the
CPU plausibly retires branches from unrelated loops/subroutines, and with a
wide enough unscoped window "at least one mispredict occurred" becomes true
almost by default. This is why the branch-identification refinement (above)
was built.

## v2 measured data (429.mcf, 1M warmup / 5M simulation instructions, scoped)

Same run parameters. Of 88,430 issued prefetches, 37,629 (43%) were matched
to a real future occurrence within this window (the rest still pending at
simulation end — a left-censoring artifact of the short window).

**`gating_branches` histogram**, now scoped to each PC's identified gating
branch: the 32+ overflow bucket dropped from 87.8% to **39%** (14,698 of
37,629) — a real reduction, but still the single largest bucket, with the
remainder spread broadly across 0–31 rather than concentrated near the
literature-anchored 1–3 range.

**Per-PC waste fraction, with the identified gating branch and how
confidently it was identified** (`gating_branch_confidence` = the
identified branch's share of all candidate votes for that PC — low means
several different branches compete to precede that load, not one dominant
back-edge):

| PC | total | wasted_fraction | gating_branch | confidence |
|---|---|---|---|---|
| 0x401671 | 11,710 | 96.7% | 0x401685 | 41% |
| 0x401682 | 10,243 | 99.0% | 0x401685 | 45% |
| 0x401669 | 11,710 | 95.7% | 0x401685 | 41% |
| 0x40166d | 488 | 28.1% | 0x401678 | 31% |
| 0x401660 | 3,478 | 91.8% | 0x401685 | 40% |

## Still not trustworthy enough to feed into the analytical model

Two things the v2 data itself flags:

1. **Confidence is low (31–45%).** The "most common preceding branch" only
   accounts for a minority of candidate observations for every PC — no
   single branch dominates as cleanly as the "loop back-edge" model assumes.
   Plausible reasons: the load is reachable via more than one control-flow
   path (an inner conditional sometimes intervenes), or mcf's actual loop
   structure is less regular than a single clean back-edge. Either way, an
   identification this uncertain shouldn't be trusted as ground truth yet.
2. **Waste fractions are still very high (28–99%)** even after scoping,
   still above the analytical model's range for 4 of 5 PCs. Three PCs
   (0x401671, 0x401682, 0x401669) share the same identified gating branch
   (0x401685) — a plausible sign they're genuinely in the same loop body —
   but that also means a single hot, apparently-frequently-mispredicted
   branch is driving most of this data, which needs independent
   corroboration (e.g. cross-checking that branch's own MPKI against
   ChampSim's core branch stats) before trusting it.

**Verdict: real, measurable improvement over v1 (overflow bucket 88%→39%),
but not yet ready to replace `analytical_model/model.py`'s swept
`gating_branches`/`alpha` ranges.** The confidence field is the concrete
signal to chase next — a more reliable gating-branch identification (e.g.
proper control-dependence analysis instead of "most common preceding
branch") is the blocking piece, not more instrumentation plumbing.

## Status

Phase 2's instrumentation plumbing works correctly and is purely additive
(validated: identical IPC 0.5368 and prefetch counts 88,430 to Phase 1, both
before and after the v2 scoping refinement). Its output is real and
internally consistent (histogram and per-PC totals cross-check), but not
yet reliable enough to feed back into `analytical_model/model.py`.
