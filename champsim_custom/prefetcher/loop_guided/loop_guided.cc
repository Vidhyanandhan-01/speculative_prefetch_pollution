#include "loop_guided.h"

#include <algorithm>
#include <fstream>

#include <fmt/core.h>

#include "cache.h"

void loop_guided::record_prefetch_outcome(uint64_t ip_key, uint64_t use_seq)
{
  auto queue_it = pending_issue_seqs.find(ip_key);
  if (queue_it == pending_issue_seqs.end() || queue_it->second.empty())
    return; // no outstanding prefetch was issued for this PC to close out

  uint64_t issue_seq = queue_it->second.front();
  queue_it->second.pop_front();

  if (use_seq < issue_seq)
    return; // defensive: shouldn't happen, but don't underflow if it does

  auto gating_branches = use_seq - issue_seq;
  bool wasted = speculative_pollution::any_mispredicted_in_range(issue_seq, use_seq);

  per_pc_total[ip_key] += 1;
  if (wasted)
    per_pc_wasted[ip_key] += 1;

  unsigned bucket = static_cast<unsigned>(std::min<uint64_t>(gating_branches, GATING_HISTOGRAM_CAP));
  gating_branches_histogram[bucket] += 1;
}

std::optional<unsigned> loop_guided::detect_period(const std::vector<champsim::block_number::difference_type>& hist)
{
  for (unsigned p = 1; p <= MAX_PERIOD; ++p) {
    std::size_t window = static_cast<std::size_t>(p) * MIN_REPEATS_TO_LOCK;
    if (hist.size() < window + p)
      continue; // not enough history to confirm this period yet

    bool matches = true;
    for (std::size_t offset = 0; offset < window && matches; ++offset) {
      std::size_t i = hist.size() - window + offset;
      matches = (hist[i] == hist[i - p]);
    }
    if (matches)
      return p;
  }
  return std::nullopt;
}

uint32_t loop_guided::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                               uint32_t metadata_in)
{
  if (type != access_type::LOAD) // only track demand loads; ignore stores/prefetches/RFO/translation
    return metadata_in;

  champsim::block_number cl_addr{addr};
  auto found = table.check_hit(tracker_entry{ip});

  // Phase 2 instrumentation: this real access is the "use" for whichever
  // pending prefetch was issued furthest in the past for this PC, if any.
  record_prefetch_outcome(ip.to<uint64_t>(), speculative_pollution::current_seq());

  tracker_entry entry = found.has_value() ? *found : tracker_entry{ip};

  if (found.has_value()) {
    auto delta = champsim::offset(entry.last_cl_addr, cl_addr);
    if (delta != 0) {
      entry.history.push_back(delta);
      if (entry.history.size() > HISTORY_LEN)
        entry.history.erase(entry.history.begin());

      auto period = detect_period(entry.history);
      if (period.has_value()) {
        entry.locked_period = *period;
        std::vector<champsim::block_number::difference_type> cycle(entry.history.end() - static_cast<long>(*period), entry.history.end());
        active_lookahead = lookahead_entry{ip, champsim::address{cl_addr}, std::move(cycle), 0, PREFETCH_DISTANCE_ITERS * static_cast<int>(*period)};
      }
    }
  }

  entry.last_cl_addr = cl_addr;
  table.fill(entry);

  return metadata_in;
}

void loop_guided::prefetcher_cycle_operate()
{
  if (!active_lookahead.has_value())
    return;

  auto& la = *active_lookahead;
  if (la.iters_remaining <= 0) {
    active_lookahead.reset();
    return;
  }

  auto delta = la.period_deltas[la.next_delta_idx];
  champsim::address pf_address{champsim::block_number{la.last_address} + delta};

  if (intern_->virtual_prefetch || champsim::page_number{pf_address} == champsim::page_number{la.last_address}) {
    const bool mshr_under_light_load = intern_->get_mshr_occupancy_ratio() < 0.5;
    const bool success = prefetch_line(pf_address, mshr_under_light_load, 0);
    if (success) {
      la.last_address = pf_address;
      la.next_delta_idx = (la.next_delta_idx + 1) % la.period_deltas.size();
      la.iters_remaining -= 1;

      // Phase 2 instrumentation: record this issue's branch_log position so
      // a future real occurrence of la.owner_ip can measure how many
      // conditional/other branches intervened and whether any mispredicted.
      auto& pending = pending_issue_seqs[la.owner_ip.to<uint64_t>()];
      pending.push_back(speculative_pollution::current_seq());
      if (pending.size() > PENDING_QUEUE_CAP)
        pending.pop_front();
    }
    // if the request was rejected (e.g. PQ full), try again next cycle without advancing
  } else {
    active_lookahead.reset(); // crossed a page boundary; stop rather than guess across pages
  }
}

uint32_t loop_guided::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr,
                                            uint32_t metadata_in)
{
  return metadata_in;
}

void loop_guided::prefetcher_final_stats()
{
  // Phase 2 instrumentation dump: replaces analytical_model/model.py's swept
  // gating_branches and alpha assumptions with empirical measurements from
  // this run. Written to the current working directory -- run champsim from
  // wherever you want these to land.
  {
    std::ofstream out("pf_per_pc_waste.csv");
    out << "pc_hex,total,wasted,wasted_fraction\n";
    for (const auto& [ip_key, total] : per_pc_total) {
      auto wasted_it = per_pc_wasted.find(ip_key);
      uint64_t wasted = (wasted_it != per_pc_wasted.end()) ? wasted_it->second : 0;
      double wasted_fraction = total > 0 ? static_cast<double>(wasted) / static_cast<double>(total) : 0.0;
      out << fmt::format("{:#x},{},{},{:.6f}\n", ip_key, total, wasted, wasted_fraction);
    }
  }
  {
    std::ofstream out("pf_gating_branches_histogram.csv");
    out << "gating_branches_bucket,count\n";
    for (const auto& [bucket, count] : gating_branches_histogram) {
      out << bucket << "," << count << "\n";
    }
  }
}
