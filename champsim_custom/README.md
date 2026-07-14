# Custom ChampSim modules

Work-in-progress prefetcher/branch-predictor code implementing the
speculative-prefetch-pollution gating idea, kept outside the `champsim/`
submodule so it isn't wiped by `git submodule update`.

Copy or symlink finished modules into `champsim/prefetcher/<name>/` per
ChampSim's module conventions before building
(see `champsim/config/README.md` upstream).
