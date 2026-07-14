# Phase 4 Results: Feeding Real Data Back Into the Analytical Model

Goal: replace the swept assumptions in `model.py` (`GATING_BRANCHES_OPTIONS`,
`ZIPF_ALPHA_OPTIONS`, `N_PREFETCH_PCS`) with the real distributions measured
in ChampSim (Phase 2 v4 + Phase 3), and check whether the composed
residual-bandwidth conclusion survives. Script:
`analytical_model/phase4_empirical_calibration.py` (reuses `model.py`'s own
functions directly, so this isn't a re-derivation — it's the same
methodology, fed real inputs). Run it yourself to reproduce every number
below.

Real inputs, all from a single fresh run of `loop_guided` v4
(`PREFETCH_DISTANCE_ITERS=1`) on 429.mcf, 1M warmup / 5M simulation
instructions — only the 4 PCs Phase 2 itself judged trustworthy
(0x40166d, 0x401660, 0x401671, 0x401669) are used; 0x401682 stays excluded.

## 1. Real aggregate wrong-path rate

| PC | total | wasted | p_wrong |
|---|---|---|---|
| 0x40166d | 377 | 140 | 37.1% |
| 0x401660 | 2,936 | 2,013 | 68.6% |
| 0x401671 | 9,480 | 1,739 | 18.3% |
| 0x401669 | 9,509 | 1,742 | 18.3% |
| **Aggregate** | **22,302** | **5,634** | **25.3%** |

Sits inside the original v2 model's swept p_wrong range (2.5%–88.9%),
toward the low-to-middle of it.

## 2. Cross-check against the model's own formula — and a real discrepancy worth flagging

Using ChampSim's own reported branch stats for this exact run (MPKI 2.456,
accuracy 97.17% — printed by the simulator, not derived by us), the model's
formula `p_wrong = 1 - p_correct^gating_branches` at the REAL measured mean
`gating_branches` (1.843, from the histogram: median 1, mean 1.843, 82.7% of
samples in buckets 0–1) predicts only **5.2% wrong-path** — about **4.9x
below** the 25.3% actually measured.

Solving backwards: matching 25.3% at gating=1.843 requires a *local* branch
accuracy of ~85.4% for the identified gating branch (0x40169e), well below
the 97.2% *program-wide* average the formula uses. Plausible reading: the
formula's single global accuracy hides that the specific branches gating
long-distance prefetches are disproportionately hard-to-predict (H2P)
branches, not average ones — consistent with both the literature survey's
own H2P framing and this project's core premise that *per-branch*, not
program-average, confidence is what's worth gating on. This is a real,
measured discrepancy, not resolved here — a genuine per-PC branch-accuracy
measurement (not available from ChampSim's aggregate MPKI stat) would be
the natural next instrumentation step if this project continues.

## 3. Real waste concentration

| PC | wasted | share of total wasted |
|---|---|---|
| 0x401660 | 2,013 | 35.7% |
| 0x401669 | 1,742 | 30.9% |
| 0x401671 | 1,739 | 30.9% |
| 0x40166d | 140 | 2.5% |

Only **4 distinct prefetch-issuing PCs** were found in this workload/
prefetcher combination — far fewer than the model's originally assumed
`N_PREFETCH_PCS=32`. Reactive per-PC throttling catching just the single
worst real PC (0x401660) recovers 35.7% of total measured waste, leaving
64.3% residual. The Zipf alpha that best reproduces this at the real n=4 is
**alpha ≈ 0.49** — squarely inside the originally swept range (0.0–2.0),
roughly mid-range between "diffuse" and "concentrated."

## 4. Composed residual bandwidth: does the model's prediction survive?

Two scenarios, both using the real p_wrong (25.3%) and real recovery
(35.7%) above, differing only in the total-prefetch-overhead input:

- **(A) Fully our own measured numbers** (overhead = 0.002%, from Phase 3):
  residual = **0.0003% of channel**. Reflects Phase 3's already-flagged
  caveat that our own conservatively-tuned prefetcher's total traffic isn't
  representative of a production system — this number mostly measures that
  tuning choice, not the phenomenon's real-world magnitude.
- **(B) Real wrong-path rate + real concentration, Magellan-anchored
  overhead** (10%, since Phase 3 showed our own overhead measurement
  understates a real system): residual = **1.62% of channel** (0.42 GB/s).

**The original v2 model's swept composed range was 0.1%–2.0% typical, 7.7%
stress case (`FINDINGS.md`). Scenario B's real-data result (1.62%) lands
inside that predicted range.**

This is the headline result: a composed prediction made *before any real
ChampSim data existed* is corroborated, not contradicted, by feeding real
measured inputs into the same formula in place of the swept assumptions.

## 5. Latency-uplift framing with the real residual

| rho_base | delay-proxy uplift from eliminating the real (1.62%) residual |
|---|---|
| 0.50 | 6.3% |
| 0.70 | 7.6% |
| 0.85 | 12.5% |

Consistent with the original swept latency framing (`FINDINGS.md` §3):
real data, not just swept assumptions, produces the same "payoff
concentrates under contention" shape.

## Verdict

The composed model's central prediction survives contact with real data —
scenario B's 1.62% lands inside the range the model predicted from swept
assumptions alone, and the real waste-concentration (alpha≈0.49) and
gating-branches distribution (mean 1.843) both land inside their originally
swept ranges too. Two things were newly learned rather than confirmed:
`N_PREFETCH_PCS` is much smaller in reality (4, not 32) for this workload,
and the model's global-branch-accuracy formula understates real per-branch
wrong-path rates by ~5x, pointing at H2P-branch-specific behavior as a
concrete, testable follow-up.

This is still one workload, one short window, and one caveat-carrying
prefetcher configuration (Phase 3). It's real corroboration, not proof —
but it's the strongest evidence this project has produced that the
composed analytical model's magnitude estimate is in the right ballpark.
