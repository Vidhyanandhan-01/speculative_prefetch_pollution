# Analytical Model

Back-of-envelope model answering one question before any gem5/ChampSim time
is spent: **after existing reactive prefetch throttling, how much wrong-path
software-prefetch bandwidth survives, and is it large enough to justify a
proactive, per-branch reconvergence-gated mechanism?**

## Files

- `model.py` — the model. Run with `python3 model.py`.
- `spike_v1/` — the original exploratory spike and its independent audit.
  Kept for provenance. Two problems the audit found in it motivated this
  version: an unanchored bandwidth number that came out ~4x larger than
  Magellan's own measured overhead, and a reactive-throttling recovery
  curve that was asserted (a tuned linear decay) rather than derived.

## What `model.py` does differently

1. **Calibrated, not free-floating bandwidth.** Total prefetch-bandwidth
   overhead is anchored to Magellan's own measured ~10% (ISCA'25, Fig. 19),
   swept ±50%, instead of derived from unanchored PPKI/IPC assumptions. The
   model's wasted-bandwidth ceiling can therefore never exceed what's
   actually been measured on a real system of this class.
2. **Derived, not asserted, recovery curve.** Reactive per-PC accuracy-
   counter throttling's recovery fraction is derived from a Zipf/power-law
   model of how wrong-path waste is spread across static prefetch PCs, with
   a concentration parameter (`alpha`) that is independent of misprediction
   rate — fixing the earlier model's conflation of the two.
3. **One composed, end-to-end number.** `end_to_end_residual_gbps()`
   multiplies wrong-path rate × wasted bandwidth × (1 − recovery) into the
   single figure that actually matters: bandwidth a proactive scheme would
   still need to recover.
4. **Latency framing.** Residual bandwidth is translated into a queueing-
   delay-proxy uplift (M/M/1-style ρ/(1−ρ)), since the problem statement's
   figure of merit is tail latency, not raw GB/s.
5. **Sensitivity pass.** A one-at-a-time sweep ranks which parameter the
   composed output is most sensitive to — i.e., what to instrument in
   ChampSim first rather than continue to assume.

## Headline result (see script output for full tables)

At representative parameters (MPKI 10–20, 2–3 gating branches, Magellan's
measured 10% prefetch overhead), residual wasted bandwidth after reactive
throttling lands at **0.1%–2.0% of one DRAM channel**, concentrated in the
`alpha` (waste diffuseness) low-end where reactive per-PC throttling has
nothing to grab onto. This is an order of magnitude below spike_v1's
uncalibrated 40% headline number, and consistent with Magellan's own
reported total overhead — a materially more defensible starting point.

The sensitivity pass currently ranks `gating_branches` (how many
control-dependent branches actually gate a given prefetch's usefulness) as
the highest-leverage unknown — this is the first thing a ChampSim pass
should measure directly (a control-dependence tracker on prefetched loads)
rather than continue to sweep as an assumption.

## Next step: ChampSim

This model motivates instrumenting, in order:

1. A control-dependence tracker to measure real `gating_branches`
   distributions on prefetch-eligible loads (highest sensitivity).
2. A per-PC prefetch-outcome histogram to measure real Zipf `alpha`
   (second-highest sensitivity) instead of assuming a concentration.
3. Channel-bandwidth breakdown by request type, to replace the Magellan-
   anchored overhead assumption with a workload-specific measurement.

Only once real distributions replace these three swept assumptions is it
worth building the actual reconvergence-gated throttling mechanism in
`champsim_custom/prefetcher/`.
