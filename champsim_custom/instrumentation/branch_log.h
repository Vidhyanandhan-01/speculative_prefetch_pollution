#ifndef SPECULATIVE_POLLUTION_BRANCH_LOG_H
#define SPECULATIVE_POLLUTION_BRANCH_LOG_H

#include <algorithm>
#include <cstdint>
#include <utility>
#include <vector>

/*
 * Shared, header-only event log correlating ChampSim's real per-branch
 * prediction outcomes against champsim_custom prefetcher modules' own
 * prefetch-issue timing.
 *
 * Why this exists instead of tracking genuine wrong-path instruction fetch:
 * ChampSim's core does not model wrong-path fetch at all (confirmed by
 * reading champsim/src/ooo_cpu.cc and champsim/src/register_allocator.cc's
 * own "once wrong path is implemented" comment) -- every branch's
 * correctness is checked against the trace's oracle-known outcome the
 * instant it is fetched, so no instructions ever get fetched past a
 * to-be-mispredicted branch. Reproducing genuine wrong-path prefetch
 * dispatch is therefore not possible without a much larger change to
 * ChampSim's front-end.
 *
 * This project's two open analytical-model parameters (gating_branches,
 * alpha) don't actually need that, though: both are answerable from the
 * CORRECT-PATH trace alone, by asking "was the branch this prefetch's
 * target implicitly depended on mispredicted by the time the corresponding
 * real access happened?" -- exactly the mechanism Magellan's own paper
 * describes (Sec 4.4: a mispredicted inner-loop branch means the loop's
 * induction variable was speculatively wrong, so any prefetch computed from
 * it during that window would have been wrong too). That only requires
 * knowing real per-branch outcomes and real dynamic timing, both of which
 * ChampSim already models correctly -- this log is the plumbing that lets
 * a cache-side prefetcher module (which has no native visibility into
 * O3_CPU's branch resolution) read that information.
 *
 * Populated by a small, additive patch to ooo_cpu.cc's do_predict_branch
 * (see champsim_custom/patches/ooo_cpu_branch_instrumentation.patch),
 * logging every CONDITIONAL/OTHER branch's real IP and outcome. Read by
 * champsim_custom/prefetcher/loop_guided to correlate its own prefetch
 * issue timing against real misprediction events.
 *
 * v2 (post Phase-2-results audit): the first version's
 * any_mispredicted_in_range() counted EVERY conditional branch retiring
 * between a prefetch's issue and use, regardless of whether it had anything
 * to do with the loop containing the prefetched load -- in a large program
 * this window can include branches from unrelated loops/functions, which
 * measurably inflated the observed waste fractions (see
 * champsim_custom/PHASE2_RESULTS.md). scoped_stats() below instead counts
 * only occurrences of ONE SPECIFIC branch IP within the window -- the branch
 * a prefetcher module has identified as that load's likely loop-gating
 * branch (see loop_guided.cc's gating_branch_candidates: the conditional
 * branch most often seen immediately preceding an occurrence of that load,
 * a proxy for "the loop's own back-edge/continuation check"). This is still
 * an approximation (no real control-dependence analysis), but it is scoped
 * to the actual loop instead of the whole retirement stream.
 *
 * Header-only: the function-local `static` below is guaranteed to be a
 * single, shared instance across every translation unit that includes this
 * header (standard C++ inline-function + local-static idiom), so no
 * separate .cc file or build-system change is needed to share state between
 * a patched champsim/src/ooo_cpu.cc and champsim_custom's prefetcher modules.
 * Single-threaded only -- fine for ChampSim's single-core simulation model.
 */
namespace speculative_pollution
{

struct BranchEvent {
  uint64_t ip;
  bool mispredicted;
};

inline std::vector<BranchEvent>& branch_log()
{
  static std::vector<BranchEvent> log;
  return log;
}

// Called from the patched ooo_cpu.cc for every conditional/other branch
// evaluated. Returns this event's sequence number (its index in the log).
inline uint64_t record_branch(uint64_t ip, bool mispredicted)
{
  auto& log = branch_log();
  log.push_back(BranchEvent{ip, mispredicted});
  return log.size() - 1;
}

// The seq the NEXT recorded branch will get, i.e. "how many conditional/
// other branches have retired so far". Read by a prefetcher module at
// prefetch-issue time and again when the corresponding real access occurs.
inline uint64_t current_seq()
{
  return branch_log().size();
}

// The IP of the most recently retired conditional/other branch, or 0 if
// none has retired yet. Used to build a per-load "which branch usually
// immediately precedes this load" candidate table.
inline uint64_t last_branch_ip()
{
  auto& log = branch_log();
  return log.empty() ? 0 : log.back().ip;
}

// Among branches in [begin_seq, end_seq) whose ip == target_ip, how many
// were there, and did any mispredict? This is the scoped replacement for
// v1's any_mispredicted_in_range(), which counted ALL branches in range
// regardless of ip.
inline std::pair<uint64_t, bool> scoped_stats(uint64_t target_ip, uint64_t begin_seq, uint64_t end_seq)
{
  auto& log = branch_log();
  end_seq = std::min(end_seq, static_cast<uint64_t>(log.size()));
  uint64_t count = 0;
  bool any_mispredicted = false;
  for (uint64_t i = begin_seq; i < end_seq; ++i) {
    if (log[i].ip == target_ip) {
      ++count;
      any_mispredicted = any_mispredicted || log[i].mispredicted;
    }
  }
  return {count, any_mispredicted};
}

} // namespace speculative_pollution

#endif
