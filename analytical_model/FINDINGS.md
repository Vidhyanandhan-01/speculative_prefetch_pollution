# Findings: Analytical Model v2

Run: `python3 analytical_model/model.py`, 2026-07-13. Captures the actual
output of the calibrated model (see `README.md` for methodology, `model.py`
for the derivations). This document exists so the numbers below are a
durable record, not just terminal output that scrolls away.

## 1. Calibration check

```
Worst-case wasted bandwidth across the full sweep: 13.3% of channel.
Bounded above by max(TOTAL_PREFETCH_OVERHEAD_OPTIONS) = 15% by construction (p_wrong <= 1).
```

**Finding:** by anchoring total prefetch-bandwidth overhead to Magellan's own
measured ~10% (ISCA'25 Fig. 19) instead of deriving it from unanchored
PPKI/IPC assumptions, the model's ceiling is now structurally incapable of
repeating spike_v1's ~40% headline (which the spike_v1 audit had flagged as
~4x larger than what a real system of this class actually measures).

## 2. Composed end-to-end residual bandwidth

The single most policy-relevant number: wrong-path bandwidth that **survives
existing reactive per-PC throttling**.

```
 mpki  gate  overhd  alpha  p_wrong    wasted   recov  residual   %chan
   10     2     10%    0.0   11.64%     0.30G   18.8%     0.24G    0.9%
   10     2     10%    1.0   11.64%     0.30G   60.4%     0.12G    0.5%
   10     2     10%    2.0   11.64%     0.30G   92.4%     0.02G    0.1%
   20     3     10%    0.5   31.85%     0.82G   36.6%     0.52G    2.0%
   20     3     15%    0.5   31.85%     1.22G   36.6%     0.78G    3.0%
   30     5     15%    0.0   62.93%     2.42G   18.8%     1.96G    7.7%
    5     1      5%    2.0    3.00%     0.04G   92.4%     0.00G    0.0%
```

**Finding:** across representative parameter points, residual bandwidth
after reactive throttling ranges **0.1%–7.7% of one DRAM channel**, with the
representative "typical" cases (MPKI 10–20, 2–3 gating branches) clustering
at **0.1%–2.0%**. The 7.7% row is a stress case (MPKI 30, 5 gating branches,
15% overhead, fully diffuse waste) — plausible for the worst irregular
workloads, not representative of the average case.

**Finding:** recovery fraction is the dominant swing factor within a fixed
`(mpki, gate)` pair — e.g. at mpki=10/gate=2, recovery alone moves residual
from 0.9% to 0.1% of channel (9x) purely as a function of `alpha`
(concentration). This confirms the spike_v1 audit's concern was real: H2P
rate and waste-concentration are genuinely separate axes, and conflating
them (as spike_v1 did) would have hidden this.

## 3. Latency framing

```
 rho_base  alpha  residual_%chan  delay_uplift_%
     0.50    0.0           1.40%            5.4%
     0.50    1.0           0.68%            2.7%
     0.50    2.0           0.13%            0.5%
     0.70    0.0           1.40%            6.5%
     0.70    1.0           0.68%            3.2%
     0.70    2.0           0.13%            0.6%
     0.85    0.0           1.40%           10.8%
     0.85    1.0           0.68%            5.3%
     0.85    2.0           0.13%            1.0%
```

**Finding:** the same residual bandwidth buys a disproportionately larger
queueing-delay-proxy reduction as baseline channel utilization rises (5.4%→
10.8% delay uplift for the same 1.40%-of-channel residual, going from
ρ=0.50 to ρ=0.85). This is the expected behavior of a ρ/(1−ρ) queue and is
the mechanism's strongest argument: **the payoff concentrates exactly where
contention is worst**, i.e. where the problem statement's own 99th-
percentile-latency figure of merit would be measured. This is a directional
argument, not a quantitative latency prediction — see Limitations.

## 4. Sensitivity ranking

```
               parameter  min_%chan  max_%chan   spread
         gating_branches      0.36%      2.10%    1.74pp
                   alpha      0.13%      1.40%    1.27pp
                    mpki      0.23%      1.30%    1.06pp
 total_prefetch_overhead      0.34%      1.02%    0.68pp
          branch_density      0.57%      0.89%    0.32pp
```

**Finding:** `gating_branches` (how many control-dependent branches actually
gate a given prefetch's usefulness) has the largest swing (1.74 percentage
points) of any swept parameter — larger than misprediction rate itself. This
is the parameter this analytical model is weakest on (it's a small swept
constant, not derived from any trace), and is therefore the top priority for
direct ChampSim measurement (a control-dependence tracker on prefetched
loads) before further analytical refinement is worthwhile.

`alpha` (waste concentration) ranks second — also unmeasured, requiring a
per-PC prefetch-outcome histogram in ChampSim to replace with real data.

## 5. Overall verdict

- The phenomenon is real but **modest in absolute bandwidth terms**
  (well under Magellan's own ~10-15% total prefetch overhead ceiling, by
  construction) — this model does not support a "prefetching wastes a third
  of the memory channel" framing; it supports a "small but latency-
  concentrated residual survives existing mitigation" framing.
- The strongest quantitative argument for the idea is the **latency framing
  under high contention** (§3), not the raw bandwidth number (§2) — a
  proposal/write-up should lead with that, not with %-of-channel alone.
- Two parameters (`gating_branches`, `alpha`) are unmeasured assumptions
  driving most of the model's spread. Both are directly measurable in
  ChampSim without building the actual mechanism first — do that
  measurement pass before writing any throttling logic.

## Limitations (carried forward, not resolved by this model)

- The ρ/(1−ρ) queueing relationship is a directional M/M/1 approximation,
  not a model of a real memory controller's request queue (which has
  finite depth, request reordering, multiple channels/ranks, etc.). Treat
  latency-uplift percentages as "which direction and roughly how much this
  effect concentrates," not as a predicted IPC or tail-latency number.
- `alpha`, `N_PREFETCH_PCS`, and `REACTIVE_CATCH_FRACTION` are new
  assumptions this version introduces (not literature-anchored); they are
  swept and their impact is quantified in §4, but they remain assumptions
  until replaced by ChampSim-measured distributions.
- This model still says nothing about the *proposed mechanism's own
  overhead* (extra hardware/signal-routing cost of gating prefetch dispatch
  on a reconvergence signal) — only about the size of the problem it would
  address. That cost-side analysis is out of scope until a ChampSim
  prototype exists to measure it against.
