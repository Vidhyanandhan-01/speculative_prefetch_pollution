# Speculative Prefetch Pollution

Research project on gating/throttling long-distance software prefetches
(Magellan-style) using a per-branch dynamic reconvergence/confidence signal
(CHESS-style), to reduce wrong-path prefetch traffic before a branch
misprediction is caught.

## Layout

- `literature_survey/` — survey notes, problem statement, and source papers
  (`papers/`) underpinning the idea.
- `analytical_model/` — early back-of-envelope validation model and audit,
  used to sanity-check the idea before committing to simulation.
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
