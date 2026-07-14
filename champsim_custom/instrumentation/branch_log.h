#ifndef SPECULATIVE_POLLUTION_BRANCH_LOG_H
#define SPECULATIVE_POLLUTION_BRANCH_LOG_H

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <optional>
#include <utility>

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
 * v2: scoped_stats() counts only occurrences of ONE SPECIFIC branch IP
 * within a window, instead of v1's any_mispredicted_in_range() which
 * counted EVERY branch in the window regardless of ip (see
 * champsim_custom/PHASE2_RESULTS.md for why that inflated waste fractions).
 *
 * v3 (post code-review fixes):
 *  - The log is now a bounded-retention std::deque instead of an
 *    unboundedly-growing std::vector (code review finding: multi-GB growth
 *    over a real, long experiment run). BRANCH_LOG_RETENTION is a generous
 *    margin over realistic issue-to-use windows; scoped_stats() clamps and
 *    counts truncations via truncated_window_count() so silent data loss
 *    from eviction is at least observable, even though it can't be avoided
 *    without unbounded memory.
 *  - last_branch_ip() now returns std::optional<uint64_t> instead of using
 *    0 as a sentinel (code review finding: 0 is theoretically a valid IP;
 *    also brings this in line with detect_period()'s existing use of
 *    std::optional in loop_guided.cc for the same "value or not yet known"
 *    shape).
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

// Bounded retention window for the branch log. Generous relative to
// realistic issue-to-use gaps (loop_guided.h's PENDING_QUEUE_CAP already
// bounds outstanding prefetches per PC to 64, and once a PC's gating branch
// is locked -- see loop_guided.cc v3 -- windows should stay well under this)
// while keeping worst-case memory bounded (~32MB at 16 bytes/entry) instead
// of growing without limit for the life of the process.
constexpr std::size_t BRANCH_LOG_RETENTION = 2'000'000;

struct BranchLogState {
  std::deque<BranchEvent> log;
  uint64_t base_seq = 0;          // seq of log.front(); entries before this have been evicted
  uint64_t truncated_windows = 0; // diagnostic: how many scoped_stats() calls had their begin_seq clamped by eviction
};

inline BranchLogState& branch_log_state()
{
  static BranchLogState state;
  return state;
}

// Called from the patched ooo_cpu.cc for every conditional/other branch
// evaluated. Returns this event's sequence number.
inline uint64_t record_branch(uint64_t ip, bool mispredicted)
{
  auto& state = branch_log_state();
  state.log.push_back(BranchEvent{ip, mispredicted});
  uint64_t seq = state.base_seq + state.log.size() - 1;
  if (state.log.size() > BRANCH_LOG_RETENTION) {
    state.log.pop_front();
    ++state.base_seq;
  }
  return seq;
}

// The seq the NEXT recorded branch will get, i.e. "how many conditional/
// other branches have retired so far" (monotonic regardless of retention
// eviction, since base_seq + log.size() is an invariant). Read by a
// prefetcher module at prefetch-issue time and again when the corresponding
// real access occurs.
inline uint64_t current_seq()
{
  auto& state = branch_log_state();
  return state.base_seq + state.log.size();
}

// The IP of the most recently retired conditional/other branch, or
// std::nullopt if none has retired yet (including: none retired since the
// caller last checked -- e.g. during warmup, when the patched ooo_cpu.cc
// never calls record_branch() at all). Used to build a per-load "which
// branch usually immediately precedes this load" candidate table.
inline std::optional<uint64_t> last_branch_ip()
{
  auto& state = branch_log_state();
  if (state.log.empty())
    return std::nullopt;
  return state.log.back().ip;
}

// Among branches in [begin_seq, end_seq) whose ip == target_ip, how many
// were there, and did any mispredict? If begin_seq has already been evicted
// by the retention window, it's clamped up to the oldest still-retained seq
// and truncated_windows is incremented so this is at least observable.
inline std::pair<uint64_t, bool> scoped_stats(uint64_t target_ip, uint64_t begin_seq, uint64_t end_seq)
{
  auto& state = branch_log_state();
  uint64_t available_end = state.base_seq + state.log.size();
  end_seq = std::min(end_seq, available_end);
  if (begin_seq < state.base_seq) {
    begin_seq = state.base_seq;
    ++state.truncated_windows;
  }
  uint64_t count = 0;
  bool any_mispredicted = false;
  for (uint64_t seq = begin_seq; seq < end_seq; ++seq) {
    const auto& e = state.log[seq - state.base_seq];
    if (e.ip == target_ip) {
      ++count;
      any_mispredicted = any_mispredicted || e.mispredicted;
    }
  }
  return {count, any_mispredicted};
}

inline uint64_t truncated_window_count()
{
  return branch_log_state().truncated_windows;
}

} // namespace speculative_pollution

#endif
