#include "loop_guided.h"

#include "cache.h"

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
        active_lookahead = lookahead_entry{champsim::address{cl_addr}, std::move(cycle), 0, PREFETCH_DISTANCE_ITERS * static_cast<int>(*period)};
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
