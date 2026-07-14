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

## v3: code-review fixes

A 7-finder-angle + 3-verifier-agent code review of the v1→v2 diff surfaced 10
findings, all CONFIRMED or PLAUSIBLE against the actual code (see the review
transcript in-conversation; not separately filed). All 10 were fixed:

1. **Warmup boundary bug (most severe).** `branch_log`'s only writer
   (`record_branch`, in the ooo_cpu.cc patch) is gated on `!warmup`, but
   `loop_guided`'s own instrumentation calls were not — so a prefetch issued
   during warmup got `issue_seq=0` (the log's "nothing happened yet" value),
   and if matched just after warmup ended was scored against a window
   spanning the *entire simulation so far*. Fixed by gating
   `prefetcher_cache_operate`/`prefetcher_cycle_operate`'s instrumentation on
   `!intern_->warmup` too.
2. **Queue-eviction bias.** `PENDING_QUEUE_CAP` overflow dropped the *oldest*
   (highest-gap) pending entry, systematically undercounting waste. Not
   eliminable without unbounded memory or a redesign (see "v3 addendum"
   below) — made visible instead via a new `queue_evictions` column.
3. **Time-varying identification.** The "identified gating branch" was
   recomputed from a live-mutating vote table on every sample, so different
   samples of the same PC could be scored against different branches while
   the CSV reported only the final one. Fixed: once identified, a PC's
   gating branch is **locked in** (`locked_gating_branch`) and reused for
   the rest of the run.
4. **Self-referential voting.** A sample's own vote was recorded before it
   was used to identify the branch that scores that same sample. Fixed by
   reordering: `record_prefetch_outcome` now runs before the current
   occurrence's vote is added.
5. **Silent data loss.** Matched-but-unscoreable samples (no gating branch
   identified yet) were discarded with no counter. Fixed: counted in
   `dropped_no_candidate`.
6. **Non-deterministic tie-breaking.** Ties in vote count depended on
   `unordered_map` iteration order. Fixed: deterministic tie-break (smallest
   branch ip wins).
7. **0-as-sentinel / std::optional.** `last_branch_ip()` and the identified-
   branch lookup used `0` to mean "not found," risking collision with a
   genuine value and inconsistent with `detect_period`'s existing
   `std::optional` use in the same file. Fixed: both now return
   `std::optional`.
8. **"Scoped" claim overstated.** v2's doc comments claimed the fix was
   "scoped to the actual loop," but only the *scoring* step was scoped —
   *identification* (which branch to pick) still voted from the whole
   global retirement stream. Partial mitigation: candidate votes are now
   bounded to branches within `GATING_BRANCH_MAX_IP_DISTANCE` (4096 bytes)
   of the tracked load's own ip, a coarse "same function" proxy. Still not
   real control-dependence analysis.
9. **Unbounded `branch_log` growth.** The log vector was never bounded,
   risking multi-GB growth on a real long run. Fixed: converted to a
   bounded-retention `std::deque` (`BRANCH_LOG_RETENTION` = 2M entries,
   ~32MB), with truncation counted (`truncated_window_count()`) so silent
   data loss from eviction is at least observable.
10. Covered by fix 7 (same underlying sentinel-value issue).

### Rebuild dependency-tracking gotcha

While testing these fixes, `make` twice silently relinked a stale
`loop_guided.o` after a header-only change (no `.d` dependency entry for
`loop_guided.h` was found). If you edit `loop_guided.h` without touching
`loop_guided.cc`, force a rebuild:
`rm champsim/.csconfig/modules/externUPdir/champsim_custom/prefetcher/loop_guided/loop_guided.o`
before `make`, or you'll silently run stale code.

### v3 results (429.mcf, same 1M/5M warmup/simulation window)

Purely additive confirmed again: IPC **0.5368**, prefetch issued **88,430**
— identical to v1/v2 and Phase 1. `truncated_window_count()` = 0 (2M
retention was never approached in this run).

With the warmup-boundary fix alone (before re-tuning the queue), the
overflow bucket dropped from v2's 39% to **2.0%** — confirming finding 1 was
the dominant contributor to v2's still-high waste fractions. Gating-branch
confidence also jumped from 31–45% to 50–100%, confirming the IP-distance
bound (finding 8's mitigation) meaningfully sharpened identification.

### v3 addendum: a deeper issue discovered while validating the fixes

Raising `PENDING_QUEUE_CAP` to reduce eviction (naively — just making the
queue bigger) made things *worse*, not better: it let genuinely ancient
pending entries (issued tens of thousands of branches earlier) survive to
be matched, reintroducing a wide-window bias from a different angle (the
overflow bucket jumped back to 89%). Adding a separate staleness bound
(`MAX_VALID_GAP_SEQ`, matches older than this are dropped as
`stale_dropped` rather than scored) fixes *that*, but reveals the real
problem underneath: for 3 of the 5 tracked PCs, `active_lookahead` fully
re-arms (issuing a fresh full batch of prefetches) on *every* occurrence
rather than topping up remaining budget — so for hot/frequently-revisited
PCs, prefetches get issued far faster than real occurrences can close them
out, and the pending queue grows essentially without bound regardless of
`MAX_VALID_GAP_SEQ`'s value. This is a **Phase 1 prefetcher-design property
surfacing as a Phase 2 measurement problem**, not something an
instrumentation threshold can fix.

Current data (`MAX_VALID_GAP_SEQ=4096`, `PENDING_QUEUE_CAP=4096`):

| PC | total | wasted_fraction | confidence | queue_evictions | stale_dropped | trust? |
|---|---|---|---|---|---|---|
| 0x40166d | 496 | 30.2% | 50% | 0 | 0 | **yes** — clean |
| 0x401660 | 3,476 | 72.7% | 100% | 0 | 0 | **yes** — clean |
| 0x401682 | 242 | 97.1% | 75% | 5,074 | 10,493 | no — pathological queue |
| 0x401671 | 170 | 100% | 100% | 14,762 | 11,536 | no — pathological queue |
| 0x401669 | 165 | 100% | 100% | 18,188 | 11,540 | no — pathological queue |

The two PCs with zero eviction/staleness are exactly the two with the most
plausible waste fractions (30%, 73% — inside or near the analytical model's
range); the three with runaway queues show near-100% waste on tiny,
survivorship-biased samples and should not be trusted. This is now visible
and diagnosable (that was the point of fixes 2/5/6), but not fixed — fixing
it means changing `loop_guided`'s re-arm policy (Phase 1 territory), not
another Phase 2 instrumentation change.

## Status

Phase 2's instrumentation plumbing is correct, purely additive (validated
across v1/v2/v3), and now honestly diagnosable — every known source of
measurement bias is either fixed or exposed as a counted, visible CSV
column rather than hidden. Two of five tracked PCs now produce numbers
plausible enough to take seriously (30–73% waste, in the analytical model's
range); the other three are visibly unreliable due to a Phase 1 prefetcher
re-arm-policy issue this pass surfaced but did not fix. **Still not ready
to bulk-replace `analytical_model/model.py`'s swept assumptions** — but for
the first time, the reasons why are fully instrumented rather than
suspected.
