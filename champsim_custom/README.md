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
  prefetcher module. See its header comment for the full design rationale;
  short version: ChampSim traces record only addresses, never the register/
  memory VALUES a real indirect-memory-access prefetcher would need, so
  genuine `A[B[i]]`-style address computation can't be reproduced here.
  Instead it detects PERIODIC address-delta patterns per load PC (a proxy
  for "this load revisits the same relative pattern every loop iteration")
  and prefetches several periods ahead by replaying the learned delta
  cycle — producing a real population of long-distance prefetches to
  instrument in Phase 2, without requiring data values the trace can't
  provide.
- `configs/loop_guided_l2c.json` — copy of `champsim_config.json` with the
  L2C prefetcher pointed at `loop_guided`. Build with:
  ```bash
  cd champsim
  ./config.sh ../champsim_custom/configs/loop_guided_l2c.json
  make
  bin/champsim_loop_guided --warmup-instructions <N> --simulation-instructions <M> <trace>
  ```

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

## Next: Phase 2

Additive instrumentation in `champsim/src/ooo_cpu.cc` (not a rewrite) to
tag each issued prefetch with its gating branch (via the existing
`prefetch_metadata` field), and on that branch's resolution, record whether
the prefetch was already issued and whether the branch mispredicted —
yielding empirical `gating_branches` and per-PC waste distributions
(`alpha`) to replace the swept assumptions in `analytical_model/model.py`.
