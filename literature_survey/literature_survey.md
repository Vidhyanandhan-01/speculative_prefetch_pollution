# Literature Survey: run_010 — "Speculative Prefetch Pollution" (Confidence-Gated Software Prefetching)

## 1. Project Summary

The idea proposes closing a gap between two independently-evolving front-end/memory-system techniques: (1) aggressive compiler-inserted **long-distance software prefetching** for irregular/indirect-memory workloads (graph analytics, sparse ML), which must place prefetch instructions hundreds-to-thousands of instructions ahead of their use to hide DRAM latency, and (2) deep speculative **out-of-order execution**, which dispatches instructions (including those prefetches) down paths that are frequently later found to be mispredicted. The core claim: once a wrong-path prefetch is dispatched to the memory controller, the resulting DRAM request is real, unrecallable, wasted traffic — a pipeline squash discards the *instructions* but not the *memory request already in flight*. The proposed fix is to gate/throttle prefetch dispatch using a dynamic, per-branch confidence/reconvergence signal (borrowed from CHESS) rather than today's reactive, PC-level accuracy counters.

## 2. Literature Findings

### Magellan — the software-prefetcher half of the idea, confirmed real (ISCA 2025)
**Magellan: A High-Performance Loop-Guided Prefetcher for Indirect Memory Access**, Fu, Xia, Yin, Nair, Lis, Ren, ISCA 2025. [ACM](https://dl.acm.org/doi/10.1145/3695053.3731054).
Confirmed real, correctly characterized: a loop-guided software prefetcher that extracts cross-loop-level dependence graphs to prefetch indirect memory accesses (graph/sparse workloads), reducing cache misses 25% and giving up to 1.41x speedup over the best prior software IMA prefetcher. **No evidence Magellan's own paper discusses wrong-path speculation, branch-misprediction interaction, or DRAM bandwidth waste from squashed prefetches at all** — it is purely about prefetch-pattern detection and scheduling accuracy. This confirms the idea is proposing a genuinely new interaction, not something the source paper already covers.

### CHESS's reconvergence signal — confirmed real and independently checked for prefetch discussion (ISCA 2025)
Already fully verified in the run_047 survey (same paper, `3695053.3731059.pdf`, held locally). Re-checked here specifically for prefetch-related content: `pdftotext` search across the entire paper turns up exactly one match for "prefetch," in Table 1's baseline hardware config (standard Next-Line/IP-Stride prefetchers listed as part of the simulated system) — **zero design discussion connecting CHESS's dynamic reconvergence/divergence detection to prefetch dispatch or memory-bandwidth implications.** CHESS's own authors never made this connection, so the specific combination this idea proposes (reconvergence signal -> prefetch gate) does not already exist in either source paper.

### "LLBP-X" / "False Path Utility" — likely a fabricated citation
The idea's inspiration trace cites "LLBP-X's False Path Utility" finding as a constraint on the design. No paper by this name could be located. This is the same class of citation error caught in the original run_005 survey (MICRO-2017 vs. PACT-2013 for Jigsaw). The closest *real* adjacent work is older and more general:

### Wrong-path usefulness / speculation control — real, older, adjacent (not identical)
**Performance-Aware Speculation Control Using Wrong Path Usefulness Prediction**, Mutlu et al. (ETH/CMU HPS group technical report). Confirmed to exist (could not extract full text — PDF parsing failed both via WebFetch and locally); based on search-indexed summaries, this is a general mechanism for deciding whether continued *speculation itself* (not specifically prefetch dispatch) is likely to be useful. It's an adjacent, older idea in the same conceptual family (using a prediction of "is this speculative work going to matter" to gate hardware behavior), but targets overall speculation control, not compiler-inserted prefetch instructions specifically. A real proposal built on this idea would need to cite this correctly instead of the apparently-fabricated "LLBP-X."

### Confidence/utility-based prefetch throttling — real, well-established, but a different mechanism class
Patent and industry literature confirm **decades of prior art on confidence/accuracy-based throttling for hardware (stream/next-line) prefetchers** — e.g. throttling next-line prefetchers after a run of incorrect predictions, congestion- and accuracy-aware throttling logic. These are all **reactive**: they learn a static prefetch site's or stream's historical accuracy and back off over time. This matches the idea's own `KNOWN_STATE` characterization of "utility-based prefetch throttling" as insufficient because it can't proactively prevent the very next, single, high-cost dispatch tied to one specific future branch misprediction. This class of prior art constrains the idea (it must clearly differentiate "reactive, per-stream" from "proactive, per-branch-event") but doesn't invalidate it — it targets a different prefetcher class (hardware stream prefetchers, not compiler-injected long-distance IMA prefetches) and a different granularity (historical/aggregate vs. single dynamic event).

### Wrong-path prefetch/load cancellation — real, and partially challenges the idea's "cannot be recalled" premise
Older patents (e.g. "Method and system for cancelling speculative cache prefetch requests") describe canceling a speculative bus request **before the address lines are driven** — i.e., while still early in the local memory pipeline, not yet committed to a remote/DRAM-facing queue. This is a real, partial mitigation already in some designs. **It does not fully undercut the idea**, because the idea's specific claim is about requests that have already progressed past this early-cancellation window (queued at the memory controller, targeting DRAM) — but it does mean the *exact point* past which cancellation becomes impossible is a load-bearing, checkable microarchitectural fact, not something to assume.

## 3. The MTE-equivalent check

**No single existing mechanism kills this outright.** Unlike run_005 (Hawkeye), run_011 (the ahead-prediction paper's own Table 1), and run_047 (Ignite, explicitly assumed as CHESS's own baseline), every adjacent mechanism found here solves a *neighboring* sub-problem, not the specific combination:
- Reactive utility-based throttling → targets hardware stream prefetchers, aggregate/historical granularity.
- Wrong-path usefulness prediction → targets general speculation control, not prefetch dispatch specifically.
- Early-cancellation patents → only cover the window before a request commits to the memory controller.
- Neither Magellan nor CHESS (both real, both ISCA 2025, both directly cited) connects a per-branch dynamic confidence signal to software-prefetch gating.

This is structurally the same "survives, narrows the scope" outcome as run_027, not a clean kill.

## 4. Novelty Gap Assessment

- Versus Magellan (no wrong-path awareness): gap is real and confirmed by reading the actual paper's stated scope.
- Versus CHESS (reconvergence signal never connected to prefetching): gap is real and confirmed directly from the full paper text.
- Versus reactive hardware-prefetcher throttling: gap is real *if* the waste is diffuse/data-dependent (see Q3 below) rather than concentrated in a few bad static sites — this is a checkable, not assumed, distinction.
- Versus wrong-path cancellation patents: partially open question — depends on exactly how far a request travels before it's uncancelable in a modern design; this is a concrete fact to establish early, not a proposal-killing risk.
- The "LLBP-X" citation should be dropped/replaced with the real Mutlu wrong-path-usefulness work before this goes into any proposal document.

## 5. Validation Spike Results (Analytical Model)

Ran a Python analytical model (`run_010_validation_model.py`, same folder) to put numbers on the three open questions before committing gem5/ChampSim time.

**Important self-correction during the spike:** an initial version of the model derived "how many branches can invalidate this prefetch" as `prefetch_distance × branch_density` (i.e., treating every branch in the whole lookahead window as gating). That saturates to ~100% wrong-path probability at any realistic software-prefetch distance — which would imply long-distance software prefetching can never work at all, contradicting Magellan's own measured speedups. This is the same "few branches actually gate control flow, most reconverge quickly" insight independently confirmed in the Ahead Prediction paper's Table 1 (Section 3, "Predictable Intermediate Branches"). Fixed by sweeping a small, explicit `GATING_BRANCHES` count (1-8) directly instead of deriving it from distance — this number is exactly what a real gem5/ChampSim characterization would need to measure, not assume, and is now the single most important open empirical question for this idea.

**Q1 — Is the wrong-path prefetch rate itself large?**
Across realistic sweeps (1-8 gating branches, 5-30 branch-MPKI matching graph/irregular-workload code), wrong-path prefetch probability ranges **2.5% - 89%**. At moderate, defensible parameters (2 gating branches, 10 MPKI) it's already **~12-15%** — non-negligible.

**Q2 — Does this translate into real DRAM bandwidth waste?**
Yes, and it scales meaningfully: from **0.4% of one DRAM channel's budget** at the conservative end up to **~40%** at higher MPKI/prefetch-density combinations. This is comparable in scale to bandwidth-waste numbers that motivate published prefetch-throttling work generally, i.e., plausible, not exaggerated.

**Q3 — Does existing reactive throttling already recover most of it?**
This is the load-bearing question, and it depends entirely on how *concentrated* vs. *diffuse* the wrong-path waste is: modeled recovery ranges from **~81% recovered (19% residual)** at low H2P density down to **0% recovered (100% residual)** at high H2P density, since reactive per-PC/per-stream accuracy counters have nothing to grab onto when misprediction sources are diffuse and data-dependent (the same H2P concept quantified in the Bullseye/LLBP literature) rather than concentrated in a few chronically-bad static sites.

### Revised framing after the spike

The idea survives, but its motivating claim should be narrowed to a specific, checkable condition: **the proactive confidence-gating mechanism only has real value in the high-H2P-density regime** where reactive throttling structurally can't help. The first concrete task before committing further infrastructure time should be a real (not analytical) gem5/ChampSim characterization of two numbers: (1) the actual average number of control-dependent "gating branches" per software prefetch in real graph/sparse workloads (this determines Q1's realistic operating point), and (2) how much of the resulting waste a standard reactive utility-based throttle already recovers in practice. If reactive throttling already recovers most of it (likely true for workloads with concentrated/systematic misprediction sources), this collapses the same way run_005/run_011/run_047 did. If a large residual survives (likely in workloads with genuinely diffuse, data-dependent H2P branches — exactly the graph-analytics/database domain both Magellan and CHESS target), the proactive mechanism has a real, quantified reason to exist.

## 6. Sources

- [Magellan: A High-Performance Loop-Guided Prefetcher for Indirect Memory Access (ACM DL, ISCA 2025)](https://dl.acm.org/doi/10.1145/3695053.3731054)
- [Magellan (ResearchGate)](https://www.researchgate.net/publication/392881294_Magellan_A_High-Performance_Loop-Guided_Prefetcher_for_Indirect_Memory_Access)
- [Leveraging control-flow similarity to reduce branch predictor cold effects in microservices (CHESS, ACM DL, ISCA 2025)](https://dl.acm.org/doi/10.1145/3695053.3731059) — full text held locally at `3695053.3731059.pdf`, re-checked for prefetch-related content via `pdftotext`.
- [Performance-Aware Speculation Control Using Wrong Path Usefulness Prediction (Mutlu et al., HPS tech report)](https://people.inf.ethz.ch/omutlu/pub/TR-HPS-2006-010.pdf) — existence confirmed, full text not extractable.
- Wrong-path prefetch cancellation prior art: "Method and system for cancelling speculative cache prefetch requests" (US Patent 6438656) and related patents on prefetch/load cancellation before bus commit.
- Confidence/utility-based hardware prefetch throttling: general industry/patent literature on accuracy-counter-driven next-line prefetcher throttling.
