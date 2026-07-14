#ifndef SPECULATIVE_POLLUTION_BRANCH_LOG_H
#define SPECULATIVE_POLLUTION_BRANCH_LOG_H

#include <algorithm>
#include <cstdint>
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
 * logging every CONDITIONAL/OTHER branch's real outcome. Read by
 * champsim_custom/prefetcher/loop_guided to correlate its own prefetch
 * issue timing against real misprediction events.
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

inline std::vector<bool>& branch_log()
{
  static std::vector<bool> log;
  return log;
}

// Called from the patched ooo_cpu.cc for every conditional/other branch
// evaluated. Returns this event's sequence number (its index in the log).
inline uint64_t record_branch(bool mispredicted)
{
  auto& log = branch_log();
  log.push_back(mispredicted);
  return log.size() - 1;
}

// The seq the NEXT recorded branch will get, i.e. "how many conditional/
// other branches have retired so far". Read by a prefetcher module at
// prefetch-issue time and again when the corresponding real access occurs;
// the difference between the two readings is an empirical gating_branches
// sample.
inline uint64_t current_seq()
{
  return branch_log().size();
}

// Did any branch in [begin_seq, end_seq) mispredict? Used as the "would
// this prefetch have been wasted on a wrong path" proxy.
inline bool any_mispredicted_in_range(uint64_t begin_seq, uint64_t end_seq)
{
  auto& log = branch_log();
  end_seq = std::min(end_seq, static_cast<uint64_t>(log.size()));
  for (uint64_t i = begin_seq; i < end_seq; ++i) {
    if (log[i])
      return true;
  }
  return false;
}

} // namespace speculative_pollution

#endif
