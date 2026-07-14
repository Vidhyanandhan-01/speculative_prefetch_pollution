#ifndef LOOP_GUIDED_H
#define LOOP_GUIDED_H

#include <cstdint>
#include <deque>
#include <optional>
#include <unordered_map>
#include <vector>

#include "address.h"
#include "champsim.h"
#include "modules.h"
#include "msl/lru_table.h"

#include "../../instrumentation/branch_log.h"

/*
 * Simplified, Magellan-inspired ("loop-guided") software-prefetcher proxy.
 *
 * Real Magellan (ISCA'25) extracts a compiler-level dependence graph across
 * loop nests to compute indirect addresses (A[B[i]]) ahead of use. ChampSim's
 * trace format (trace_instruction.h) records only addresses, never register
 * or memory VALUES -- so a load's index value (e.g. B[i]) is never visible
 * here, and genuine indirect-address computation cannot be reproduced.
 *
 * This module instead detects PERIODIC address-delta patterns per load PC:
 * the proxy for "this static load revisits the same relative access pattern
 * every loop iteration", which is the same structural property (loop
 * periodicity) Magellan's own dependence-graph extraction exploits, just
 * without the index-value-based address computation. Once a period is
 * locked, it prefetches PREFETCH_DISTANCE_ITERS periods ahead by replaying
 * the learned delta cycle -- producing the property this project's
 * analytical model actually needs to instrument (a population of real
 * prefetches issued many dynamic instances ahead of their use), without
 * requiring data-value visibility ChampSim's trace format doesn't provide.
 *
 * period=1 degenerates to a constant stride (already covered by ip_stride);
 * this module's interesting cases are period>=2, which ip_stride cannot
 * detect at all since it only ever tracks a single last-stride value.
 *
 * Phase 2 instrumentation (see instrumentation/branch_log.h): every issued
 * prefetch's branch_log sequence number is recorded per source PC; the next
 * real occurrence of that PC pops the oldest pending sequence number and
 * measures (a) how many times this PC's IDENTIFIED gating branch retired in
 * between -- an empirical gating_branches sample -- and (b) whether any of
 * those specific occurrences mispredicted -- the "this prefetch would have
 * been wasted on a wrong path" proxy, aggregated per PC for an empirical
 * waste-concentration (alpha) measurement.
 *
 * "Identified gating branch" (v2, see gating_branch_candidates below): each
 * tracked PC's likely loop-continuation branch is estimated as whichever
 * conditional/other branch IP most often immediately precedes an occurrence
 * of that PC in the retirement stream -- a proxy for "the loop's own
 * back-edge check", since a load at a consistent position in a loop body
 * will consistently be preceded by that loop's own branch across
 * iterations. This replaces v1's approach of counting/checking ALL
 * conditional branches in the issue-to-use window, which measurably
 * inflated waste fractions by picking up unrelated branches from other
 * loops/functions that happened to retire in the same window (see
 * champsim_custom/PHASE2_RESULTS.md).
 *
 * v3 (post code-review fixes) -- still an approximation, no real
 * control-dependence analysis, but addresses several concrete correctness
 * gaps the v2 measurement had:
 *  - Candidate votes (gating_branch_candidates) are now bounded to branches
 *    within GATING_BRANCH_MAX_IP_DISTANCE of the tracked load's own IP, a
 *    coarse "same function" proxy, instead of "whatever branch retired last
 *    anywhere in the program". The IDENTIFICATION step itself was still
 *    effectively unscoped in v2 (only the later SCORING step was) -- this
 *    is a partial mitigation, not a full fix; real control-dependence or
 *    call-stack-aware analysis would be the complete fix.
 *  - Once a PC's gating branch is identified, it is LOCKED IN
 *    (locked_gating_branch) and reused for the rest of the run, instead of
 *    recomputing the plurality winner from a continuously-mutating vote
 *    table on every sample. Fixes two bugs this caused: different samples
 *    of the same PC silently being scored against different branches over
 *    time (with the final CSV only reporting the last one), and a sample's
 *    own vote being cast before it was used to identify the branch that
 *    scores it (self-referential bias). Tie-breaking when identifying is
 *    now deterministic (smallest branch ip wins) instead of depending on
 *    unordered_map iteration order.
 *  - prefetcher_cache_operate/prefetcher_cycle_operate's instrumentation
 *    bookkeeping is now gated on `!intern_->warmup`, matching
 *    branch_log()'s own warmup gating in the ooo_cpu.cc patch. v2 recorded
 *    real issue_seq values during warmup while branch_log stayed empty
 *    (current_seq()==0 throughout), so a prefetch issued in warmup and
 *    matched just after it ended got scored against a window spanning the
 *    entire simulation-so-far instead of the true small gap.
 *  - Samples with no identified gating branch yet, and pending-queue
 *    entries evicted by PENDING_QUEUE_CAP overflow, are now counted
 *    (per_pc_dropped_no_candidate, per_pc_queue_evictions) and reported in
 *    the output CSV instead of being silently discarded. Queue eviction
 *    still introduces a real bias (the discarded entries are systematically
 *    the ones with the largest gaps) -- counting it makes that bias
 *    visible rather than fixing it outright.
 */
struct loop_guided : public champsim::modules::prefetcher {
  constexpr static std::size_t TRACKER_SETS = 256;
  constexpr static std::size_t TRACKER_WAYS = 4;
  constexpr static unsigned MAX_PERIOD = 8;          // longest loop period considered
  constexpr static std::size_t HISTORY_LEN = 32;     // deltas retained per PC
  constexpr static int PREFETCH_DISTANCE_ITERS = 4;  // periods to prefetch ahead once locked
  constexpr static unsigned MIN_REPEATS_TO_LOCK = 2; // consecutive matching cycles required to lock a period

  struct tracker_entry {
    champsim::address ip{};
    champsim::block_number last_cl_addr{};
    std::vector<champsim::block_number::difference_type> history{}; // recent deltas, oldest first
    unsigned locked_period = 0;                                      // 0 = not yet locked

    auto index() const
    {
      using namespace champsim::data::data_literals;
      return ip.slice_upper<2_b>();
    }
    auto tag() const
    {
      using namespace champsim::data::data_literals;
      return ip.slice_upper<2_b>();
    }
  };

  struct lookahead_entry {
    champsim::address owner_ip{}; // which tracked PC this lookahead belongs to
    champsim::address last_address{};
    std::vector<champsim::block_number::difference_type> period_deltas;
    std::size_t next_delta_idx = 0;
    int iters_remaining = 0;
  };

  // Result of identifying a PC's likely loop-gating branch: see
  // identify_gating_branch() / locked_gating_branch below.
  struct GatingBranchInfo {
    uint64_t ip;
    uint64_t votes;       // votes for `ip` at identification time
    uint64_t total_votes; // total votes across all candidates for that PC at identification time
  };

  // v3: pending-queue size is a MEMORY bound, not a staleness bound -- raised
  // from the original 64 (each entry is 8 bytes, so a few thousand per PC is
  // still negligible memory). Initially raising this alone looked like an
  // improvement, but measurement showed it just let genuinely stale entries
  // (issued thousands of branches ago, in an early burst from
  // active_lookahead re-arming) survive to be matched much later -- scoring
  // those against a huge, unrepresentative window reintroduced the same
  // "wide window inflates waste" problem this whole v3 pass exists to fix,
  // just via staleness instead of the warmup bug. MAX_VALID_GAP_SEQ below is
  // the real fix: it bounds how OLD a match is allowed to be, independent of
  // how large the queue is allowed to grow.
  constexpr static std::size_t PENDING_QUEUE_CAP = 4096; // bound memory if issues outpace real occurrences
  constexpr static unsigned GATING_HISTOGRAM_CAP = 32;  // bucket anything >= this into one overflow bucket
  constexpr static uint64_t GATING_BRANCH_MAX_IP_DISTANCE = 4096; // v3: coarse "same function" bound on candidate votes
  // v3: a pending prefetch matched against a real occurrence more than this
  // many branch_log entries later is considered stale/effectively abandoned
  // rather than a realistic prefetch-to-use gap, and is dropped (counted in
  // per_pc_stale_dropped) instead of scored.
  //
  // 4096 was chosen empirically, not derived: 512 was tried first and
  // rejected because it dropped 94% of all real matches on 429.mcf as
  // "stale" -- most of this workload's genuine prefetch-to-use gaps are
  // just larger than that. 4096 keeps more real data but does NOT fully
  // resolve the underlying issue (see champsim_custom/PHASE2_RESULTS.md,
  // "v3 addendum"): for 3 of 5 tracked PCs, active_lookahead's continuous
  // full re-arm on every occurrence issues prefetches far faster than real
  // occurrences can close them out, so even at this threshold those PCs
  // still show pathological queue_evictions/stale_dropped counts and their
  // remaining "fresh" samples are too few (and too close to 100% waste) to
  // trust. The 2 PCs with low eviction/staleness counts (checkable via
  // those CSV columns) are the ones whose wasted_fraction is currently
  // meaningful; the other 3 need a fix to the PREFETCHER's re-arm policy
  // (e.g. topping up remaining budget instead of fully resetting it), not
  // another instrumentation threshold, before their numbers can be trusted.
  constexpr static uint64_t MAX_VALID_GAP_SEQ = 4096;

  champsim::msl::lru_table<tracker_entry> table{TRACKER_SETS, TRACKER_WAYS};
  std::optional<lookahead_entry> active_lookahead;

  // Phase 2 instrumentation state, keyed by source PC's raw address value.
  std::unordered_map<uint64_t, std::deque<uint64_t>> pending_issue_seqs;
  std::unordered_map<uint64_t, uint64_t> per_pc_total;
  std::unordered_map<uint64_t, uint64_t> per_pc_wasted;
  std::unordered_map<uint64_t, uint64_t> per_pc_dropped_no_candidate; // v3: samples discarded, no gating branch identified yet
  std::unordered_map<uint64_t, uint64_t> per_pc_queue_evictions;      // v3: pending issues dropped by PENDING_QUEUE_CAP overflow
  std::unordered_map<uint64_t, uint64_t> per_pc_stale_dropped;        // v3: matches dropped for exceeding MAX_VALID_GAP_SEQ
  std::unordered_map<unsigned, uint64_t> gating_branches_histogram;
  // ip_key -> {candidate gating-branch ip -> times seen immediately preceding this PC}
  std::unordered_map<uint64_t, std::unordered_map<uint64_t, uint64_t>> gating_branch_candidates;
  // ip_key -> the gating branch identified and locked in for that PC (v3: see class header comment)
  std::unordered_map<uint64_t, GatingBranchInfo> locked_gating_branch;

  static std::optional<unsigned> detect_period(const std::vector<champsim::block_number::difference_type>& hist);
  void record_prefetch_outcome(uint64_t ip_key, uint64_t use_seq);
  // Returns the identified gating branch for ip_key (deterministic argmax
  // over candidate votes, ties broken by smallest branch ip), or
  // std::nullopt if no candidate has been observed yet.
  std::optional<GatingBranchInfo> identify_gating_branch(uint64_t ip_key) const;

public:
  using champsim::modules::prefetcher::prefetcher;

  uint32_t prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                    uint32_t metadata_in);
  uint32_t prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in);
  void prefetcher_cycle_operate();
  void prefetcher_final_stats();
};

#endif
