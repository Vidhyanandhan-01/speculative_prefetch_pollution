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
- `champsim/` — ChampSim simulator (git submodule) plus any custom
  prefetcher/branch-predictor modules implementing this idea.
- `proposal/` — the written project proposal.
- `results/` — simulation outputs and analysis (gitignored; regenerable).

## Setup

```bash
git clone --recurse-submodules <this-repo-url>
# or, if already cloned:
git submodule update --init --recursive
```

See `champsim/README.md` (upstream) for build instructions.
