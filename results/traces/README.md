# Traces

Gitignored (large, externally hosted, regenerable). Used so far:

```bash
curl -sL -o 429.mcf-22B.champsimtrace.xz \
  https://dpc3.compas.cs.stonybrook.edu/champsim-traces/speccpu/429.mcf-22B.champsimtrace.xz
```

Chosen because mcf is the same irregular/pointer-chasing SPEC workload class
the literature survey anchored MPKI ranges to (Ahead Prediction / LLBP
evaluations) — see `analytical_model/model.py`'s parameter comments.
