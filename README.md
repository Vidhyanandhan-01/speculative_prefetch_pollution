# Speculative Prefetch Pollution

Research project on gating/throttling long-distance software prefetches
(Magellan-style) using a per-branch dynamic reconvergence/confidence signal
(CHESS-style), to reduce wrong-path prefetch traffic before a branch
misprediction is caught.

## Layout

- `literature_survey/` — survey notes, problem statement, and source papers
  (`papers/`) underpinning the idea.
- `analytical_model/` — calibrated back-of-envelope model (`model.py`) used
  to size the effect and identify what to instrument in ChampSim first,
  before committing to simulation. `spike_v1/` holds the earlier exploratory
  version and its audit, kept for provenance.
- `champsim/` — upstream ChampSim simulator (git submodule, unmodified in
  git; a small instrumentation patch is applied at build time, see below).
- `champsim_custom/` — this project's own prefetcher module, instrumentation,
  build configs, and the ooo_cpu.cc patch, kept outside the submodule so
  `git submodule update` never wipes it. See `champsim_custom/README.md` for
  layout and `champsim_custom/PHASE2_RESULTS.md` for the full experimental
  writeup (Phase 0 setup → Phase 1 prefetcher → Phase 2 instrumentation,
  through 4 rounds of fixes).
- `proposal/` — the written project proposal.
- `results/` — simulation outputs, traces, and analysis (gitignored;
  regenerable — see `results/traces/README.md` for how to fetch the trace
  used so far).

## Setup

```bash
git clone --recurse-submodules <this-repo-url>
# or, if already cloned:
git submodule update --init --recursive

cd champsim
../champsim_custom/patches/apply_patches.sh
./config.sh ../champsim_custom/configs/loop_guided_l2c.json
make
```

See `champsim/README.md` (upstream) for general ChampSim build/trace
instructions, and `champsim_custom/README.md` for how to run the actual
`loop_guided` prefetcher + instrumentation build used in this project.

## Status

Literature survey and a calibrated analytical model are done (see
`analytical_model/FINDINGS.md`). Empirical validation in ChampSim has gone
through Phases 0–2 (environment setup, a working prefetcher, and
instrumentation now producing trustworthy real data for most tracked load
sites) — see `champsim_custom/PHASE2_RESULTS.md` for the full writeup and
current numbers. The actual gating mechanism (Phase 5 in the original plan)
has not been started; the next step is feeding the real measured
distributions back into `analytical_model/model.py`.
