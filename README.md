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
- `gem5/` — upstream gem5 simulator (git submodule, unmodified in git), added
  because ChampSim is structurally unable to model wrong-path instruction
  fetch (it's trace-driven from always-correct-path traces); gem5's O3CPU is
  execution-driven and speculates for real, which is what the actual gating
  mechanism (Phase 5) needs.
- `gem5_custom/` — this project's own gem5 configs, instrumentation, and
  patches, kept outside the submodule the same way `champsim_custom/` is.
  See `gem5_custom/README.md` — currently just the rationale and a planned
  Phase 0 pilot (not yet run) to directly confirm wrong-path software
  prefetches reach gem5's memory system before a squash.
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

gem5 build instructions aren't written up yet — `gem5_custom/README.md`'s
planned pilot hasn't been run. See `gem5/README.md` (upstream) in the
meantime for general build instructions (`scons` based).

## Status

Literature survey and a calibrated analytical model are done (see
`analytical_model/FINDINGS.md`). Empirical validation in ChampSim has gone
through Phases 0–4 (environment setup, a working prefetcher, instrumentation,
a calibration cross-check against Magellan's own measured bandwidth overhead,
and real ChampSim data fed back into the analytical model) — see
`champsim_custom/PHASE2_RESULTS.md`, `PHASE3_RESULTS.md`, and
`analytical_model/PHASE4_RESULTS.md` for the full writeups. A composed
prediction made before any real data existed (0.1%–2.0% typical residual
bandwidth) was corroborated by real measured data (1.62%) fed into the same
model — the strongest evidence so far that the model's magnitude estimate is
right.

The actual gating mechanism (Phase 5 in the original plan) has not been
started. It's gated on resolving whether vanilla ChampSim can represent
wrong-path prefetch dispatch at all — it structurally can't, being
trace-driven from always-correct-path traces (see `champsim_custom/
PHASE3_RESULTS.md` §"Interpretation"). `gem5/` and `gem5_custom/` were added
to evaluate gem5's execution-driven O3CPU as the simulator for Phase 5
specifically; see `gem5_custom/README.md` for the rationale and the planned
pilot to confirm this before porting anything over.
