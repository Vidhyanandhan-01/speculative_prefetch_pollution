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
instructions), L2C-level prefetcher, vs. the `no`-prefetcher baseline:

| | baseline (`no`) | `loop_guided` |
|---|---|---|
| IPC | 0.397 | 0.537 (+35%) |
| L2C LOAD MISS | 106,783 | 81,005 (−24%) |
| L2C PREFETCH ISSUED | 0 | 88,430 |
| L2C PREFETCH USELESS | — | 31 |

Confirms the periodic-delta detection is doing real, sensible work (not a
no-op) before moving to Phase 2's instrumentation pass.

## Phase 2: instrumentation

See `PHASE2_RESULTS.md` for the full writeup. Summary: the instrumentation
plumbing works and is purely additive (identical IPC/prefetch counts to
Phase 1), but the current `gating_branches` measurement counts *every*
conditional branch retiring between two occurrences of a tracked PC, not
just branches actually control-dependent on that specific prefetch — likely
inflating the measured waste fractions (56–99% observed, vs. the analytical
model's 63% worst case). **Not yet ready to feed back into
`analytical_model/model.py`** — needs a loop-scoping refinement first
(tracking branch IP, not just outcome, and restricting the count to
branches near the tracked load rather than the whole retirement stream).
