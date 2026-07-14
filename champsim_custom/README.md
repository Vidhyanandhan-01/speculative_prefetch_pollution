# Custom ChampSim modules

Prefetcher/branch-predictor code implementing the speculative-prefetch-
pollution measurement pass, kept outside the `champsim/` submodule so it
isn't wiped by `git submodule update`.

ChampSim's config resolver (`champsim/config/modules.py`) accepts an
absolute or relative filesystem path directly in a config's `"prefetcher"`
field — no need to copy modules into `champsim/prefetcher/`. All configs
here reference modules by relative path (`../champsim_custom/...`),
assuming `config.sh`/`make` are run from within `champsim/` as upstream's
own README documents.

## Layout

- `prefetcher/loop_guided/` — Phase 1's simplified, Magellan-inspired
  prefetcher module, extended in Phase 2 with instrumentation. See its
  header comment for the full design rationale; short version: ChampSim
  traces record only addresses, never the register/memory VALUES a real
  indirect-memory-access prefetcher would need, so genuine `A[B[i]]`-style
  address computation can't be reproduced here. Instead it detects PERIODIC
  address-delta patterns per load PC (a proxy for "this load revisits the
  same relative pattern every loop iteration") and prefetches several
  periods ahead by replaying the learned delta cycle.
- `instrumentation/branch_log.h` — Phase 2's shared, header-only event log.
  See its own header comment and `PHASE2_RESULTS.md` for what it measures
  and why.
- `patches/` — a small, additive patch to `champsim/src/ooo_cpu.cc` (the
  submodule is never modified in git; apply with `patches/apply_patches.sh`
  after any fresh `git submodule update`).
- `configs/loop_guided_l2c.json` — copy of `champsim_config.json` with the
  L2C prefetcher pointed at `loop_guided`. Build with:
  ```bash
  cd champsim
  ../champsim_custom/patches/apply_patches.sh   # only needed after a fresh submodule checkout
  ./config.sh ../champsim_custom/configs/loop_guided_l2c.json
  make
  bin/champsim_loop_guided --warmup-instructions <N> --simulation-instructions <M> <trace>
  ```
  `loop_guided`'s `prefetcher_final_stats()` writes `pf_per_pc_waste.csv` and
  `pf_gating_branches_histogram.csv` to the current working directory.

## Phase 1 validation result

Run against `429.mcf-22B.champsimtrace.xz` (1M warmup / 5M simulation
instructions), L2C-level prefetcher, vs. the `no`-prefetcher baseline.
Current (v4) numbers, after the per-PC lookahead fix and the
`PREFETCH_DISTANCE_ITERS=1` tradeoff described in `PHASE2_RESULTS.md`:

| | baseline (`no`) | `loop_guided` |
|---|---|---|
| IPC | 0.397 | 0.5127 (+29%) |
| L2C PREFETCH ISSUED | 0 | 44,507 |
| L2C PREFETCH USEFUL | — | 25,821 |

(An earlier, more aggressive config reached IPC 0.5476/+36% but left most
tracked PCs' Phase 2 measurements unreliable — see `PHASE2_RESULTS.md`
"v4" for the full tradeoff and why measurement reliability was prioritized.)
Confirms the periodic-delta detection is doing real, sensible work (not a
no-op).

## Phase 2: instrumentation

See `PHASE2_RESULTS.md` for the full writeup (v1 → v2 scoping → v3
code-review fixes → v4 prefetcher re-arm fix). Summary: the instrumentation
plumbing works and is purely additive (identical IPC/prefetch counts to
Phase 1's config through v1–v3; v4 deliberately changes the prefetcher's own
behavior, confirmed and disclosed, not an instrumentation side effect). v3
fixed 10 code-review findings and, in validating them, surfaced a further
issue: `loop_guided`'s re-arm policy issued prefetches faster than real
occurrences could close them out. v4 fixed the root cause (per-PC lookahead
state, no longer a single slot shared/stomped across 5 tracked PCs) and
tuned `PREFETCH_DISTANCE_ITERS` down to make 4 of 5 tracked PCs' waste
measurements trustworthy. **Still not ready to bulk-replace
`analytical_model/model.py`'s swept assumptions** (single workload, short
window) but two of the four clean PCs carry large sample sizes (~9,500
each) at an 18.3% waste rate — real, corroborating data inside the
analytical model's predicted range.
