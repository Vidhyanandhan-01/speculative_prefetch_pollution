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
 * measures (a) how many conditional/other branches retired in between --
 * an empirical gating_branches sample -- and (b) whether any of them
 * mispredicted -- the "this prefetch would have been wasted on a wrong
 * path" proxy, aggregated per PC for an empirical waste-concentration
 * (alpha) measurement. Replaces the two swept assumptions in
 * analytical_model/model.py with real data from this workload.
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

  constexpr static std::size_t PENDING_QUEUE_CAP = 64; // bound memory if issues outpace real occurrences
  constexpr static unsigned GATING_HISTOGRAM_CAP = 32; // bucket anything >= this into one overflow bucket

  champsim::msl::lru_table<tracker_entry> table{TRACKER_SETS, TRACKER_WAYS};
  std::optional<lookahead_entry> active_lookahead;

  // Phase 2 instrumentation state, keyed by source PC's raw address value.
  std::unordered_map<uint64_t, std::deque<uint64_t>> pending_issue_seqs;
  std::unordered_map<uint64_t, uint64_t> per_pc_total;
  std::unordered_map<uint64_t, uint64_t> per_pc_wasted;
  std::unordered_map<unsigned, uint64_t> gating_branches_histogram;

  static std::optional<unsigned> detect_period(const std::vector<champsim::block_number::difference_type>& hist);
  void record_prefetch_outcome(uint64_t ip_key, uint64_t use_seq);

public:
  using champsim::modules::prefetcher::prefetcher;

  uint32_t prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                    uint32_t metadata_in);
  uint32_t prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in);
  void prefetcher_cycle_operate();
  void prefetcher_final_stats();
};

#endif
