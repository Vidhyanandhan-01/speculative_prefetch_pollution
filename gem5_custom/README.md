# Custom gem5 modules

Mirrors the `champsim_custom/` layout: `gem5/` is the upstream gem5 submodule
(unmodified in git), and this directory holds this project's own configs,
patches, and instrumentation, kept outside the submodule so
`git submodule update` never wipes it.

## Why this exists

`champsim_custom/PHASE2_RESULTS.md` and `PHASE3_RESULTS.md` establish that
vanilla ChampSim is trace-driven: its input traces are built from the
retired, always-correct instruction stream of a real run, so wrong-path
instructions (including wrong-path software prefetches) never exist in the
data ChampSim consumes. There is no patch that fixes this — it's structural.

gem5's O3CPU is execution-driven instead: it runs a real binary with a real
branch predictor, fetches down the *predicted* path, and only detects a
misprediction later at Execute. Loads and software prefetches between the
branch and detection have already gone through fetch/decode/rename/issue,
and the LSQ has already dispatched their memory requests to the cache
hierarchy before the squash signal arrives.

Published precedent that this is real, not just structurally plausible:
- **CacheSquash** (arXiv 2406.12110) — baseline gem5 lets wrong-path memory
  requests reach the caches and alter cache state before a squash can
  cancel them; their contribution is a cancellation signal sent to the
  cache hierarchy specifically because that doesn't happen by default.
- **Correct Wrong Path** (arXiv 2408.05912) and **Exposing Shadow Branches**
  (arXiv 2408.12592) — both use gem5's O3CPU execution-driven model to
  measure wrong-path instructions' impact on L1i/L1d/L2/L3 caches.

Two things those papers don't confirm, and that this project needs to check
directly before committing further engineering time:
1. None of them discuss *software* prefetch instructions specifically on
   the wrong path (they focus on demand loads and hardware/FTQ-driven
   prefetch). No structural reason a compiler-inserted prefetch would
   behave differently from any other load-class instruction, but that's an
   inference, not confirmed in print.
2. "Exposing Shadow Branches" notes gem5 "has been extended to model
   branch-misprediction-based wrong path execution" — some of what these
   papers measure may rely on custom instrumentation on top of gem5, not
   something visible from a stock stat dump.

## Planned pilot (Phase 0, not yet run)

Before porting any of the `champsim_custom/loop_guided` prefetcher logic or
the analytical model's inputs over: build vanilla gem5 O3CPU in SE mode, run
a tiny workload containing a software prefetch instruction, and check via
gem5's debug flags/stats whether a wrong-path prefetch's memory request
shows up in the LSQ/cache stats before the squash. This is a half-day check,
not a rebuild — it answers the open question directly instead of assuming
an answer from the literature above.

## Layout (empty until the pilot above is run)

- `configs/` — gem5 SE-mode run scripts (`configs/example/se.py`-style),
  once the pilot workload is chosen.
- `instrumentation/` — any stat-collection or debug-flag additions needed to
  observe wrong-path memory requests directly (gem5 exposes considerably
  more of this via `--debug-flags` than ChampSim does out of the box, so
  this may end up much smaller than `champsim_custom/instrumentation/`).
- `patches/` — additive patches to `gem5/` source, if the pilot finds stock
  gem5 stats insufficient to answer the question above. Applied the same
  way as `champsim_custom/patches/apply_patches.sh` — submodule stays
  unmodified in git.
