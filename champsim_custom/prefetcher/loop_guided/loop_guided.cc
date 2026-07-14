#include "loop_guided.h"

#include <algorithm>
#include <fstream>

#include <fmt/core.h>

#include "cache.h"

std::optional<loop_guided::GatingBranchInfo> loop_guided::identify_gating_branch(uint64_t ip_key) const
{
  auto cand_it = gating_branch_candidates.find(ip_key);
  if (cand_it == gating_branch_candidates.end())
    return std::nullopt;

  uint64_t identified_ip = 0;
  uint64_t identified_votes = 0;
  uint64_t total_votes = 0;
  bool have_candidate = false;
  for (const auto& [branch_ip, votes] : cand_it->second) {
    total_votes += votes;
    // v3: deterministic tie-break (smallest branch ip wins) instead of
    // relying on unordered_map iteration order, which is unspecified and
    // can vary across library/compiler versions for equal vote counts.
    if (!have_candidate || votes > identified_votes || (votes == identified_votes && branch_ip < identified_ip)) {
      identified_ip = branch_ip;
      identified_votes = votes;
      have_candidate = true;
    }
  }
  if (!have_candidate)
    return std::nullopt;
  return GatingBranchInfo{identified_ip, identified_votes, total_votes};
}

void loop_guided::record_prefetch_outcome(uint64_t ip_key, uint64_t use_seq)
{
  auto queue_it = pending_issue_seqs.find(ip_key);
  if (queue_it == pending_issue_seqs.end() || queue_it->second.empty())
    return; // no outstanding prefetch was issued for this PC to close out

  uint64_t issue_seq = queue_it->second.front();
  queue_it->second.pop_front();

  if (use_seq < issue_seq)
    return; // defensive: shouldn't happen, but don't underflow if it does

  if (use_seq - issue_seq > MAX_VALID_GAP_SEQ) {
    // v3: this match is against a prefetch issued too long ago to represent
    // a realistic prefetch-to-use gap (see MAX_VALID_GAP_SEQ comment in
    // loop_guided.h) -- drop it rather than scoring it, which would inflate
    // gating_branches/wasted the same way v1's unscoped window did.
    per_pc_stale_dropped[ip_key] += 1;
    return;
  }

  // v3: once a PC's gating branch is identified, lock it in and reuse it for
  // the rest of the run instead of recomputing the plurality winner from a
  // continuously-mutating vote table on every sample. This keeps every
  // sample for a given PC scored against the SAME branch (fixing samples
  // silently being scored against different branches as the vote leader
  // shifted over time), and this call happens strictly BEFORE the current
  // occurrence's own vote is recorded (see prefetcher_cache_operate), so a
  // sample is never scored using a candidate table that already includes
  // its own vote.
  auto locked_it = locked_gating_branch.find(ip_key);
  if (locked_it == locked_gating_branch.end()) {
    auto identified = identify_gating_branch(ip_key);
    if (!identified.has_value()) {
      per_pc_dropped_no_candidate[ip_key] += 1; // v3: make the drop visible instead of silent
      return;
    }
    locked_it = locked_gating_branch.emplace(ip_key, *identified).first;
  }

  auto [gating_branches, wasted] = speculative_pollution::scoped_stats(locked_it->second.ip, issue_seq, use_seq);

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

  // v3: instrumentation bookkeeping is skipped during warmup, matching
  // branch_log()'s own warmup gating in the ooo_cpu.cc patch (record_branch
  // is only called when !warmup). Previously this ran unconditionally, so a
  // prefetch issued during warmup got issue_seq=0 (current_seq() is stuck
  // at 0 for all of warmup) and, if matched just after warmup ended, was
  // scored against a window spanning the entire simulation-so-far.
  if (!intern_->warmup) {
    uint64_t ip_key = ip.to<uint64_t>();

    // Score any pending prefetch for this PC using ONLY history strictly
    // prior to this occurrence -- this must happen BEFORE this occurrence's
    // own vote is added to gating_branch_candidates below, otherwise a
    // sample could be scored using a candidate table that already includes
    // its own vote (self-referential bias).
    record_prefetch_outcome(ip_key, speculative_pollution::current_seq());

    // Record which conditional/other branch most recently retired
    // immediately before this occurrence -- across many occurrences, the
    // mode of this is a proxy for this load's loop-gating branch. v3:
    // bounded to branches within GATING_BRANCH_MAX_IP_DISTANCE of this
    // load's own ip, a coarse "same function" proxy -- a partial mitigation
    // for candidate identification otherwise being drawn from the whole,
    // unscoped global retirement stream (see class header comment).
    auto preceding_branch_ip = speculative_pollution::last_branch_ip();
    if (preceding_branch_ip.has_value()) {
      uint64_t distance = (*preceding_branch_ip > ip_key) ? (*preceding_branch_ip - ip_key) : (ip_key - *preceding_branch_ip);
      if (distance <= GATING_BRANCH_MAX_IP_DISTANCE)
        gating_branch_candidates[ip_key][*preceding_branch_ip] += 1;
    }
  }

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

        // v4: only (re)arm a fresh lookahead for this PC if it doesn't
        // already have one still in progress -- previously this ran
        // unconditionally on every re-detected period (i.e. nearly every
        // occurrence), discarding whatever budget hadn't been issued yet
        // and letting several tracked PCs in the same loop repeatedly
        // stomp each other's single shared lookahead slot. An in-progress
        // lookahead is left alone to keep draining at its own pace.
        uint64_t owner_key = ip.to<uint64_t>();
        auto la_it = active_lookaheads.find(owner_key);
        bool needs_new_lookahead = (la_it == active_lookaheads.end()) || (la_it->second.iters_remaining <= 0);
        if (needs_new_lookahead) {
          std::vector<champsim::block_number::difference_type> cycle(entry.history.end() - static_cast<long>(*period), entry.history.end());
          active_lookaheads[owner_key] = lookahead_entry{ip, champsim::address{cl_addr}, std::move(cycle), 0, PREFETCH_DISTANCE_ITERS * static_cast<int>(*period)};
        }
      }
    }
  }

  entry.last_cl_addr = cl_addr;
  table.fill(entry);

  return metadata_in;
}

void loop_guided::prefetcher_cycle_operate()
{
  // v4: advance ALL active per-PC lookaheads each cycle (was: a single
  // shared lookahead), since several tracked PCs in the same loop are
  // legitimately in flight at once -- see class header comment / the
  // active_lookaheads member comment in loop_guided.h.
  for (auto it = active_lookaheads.begin(); it != active_lookaheads.end();) {
    auto& la = it->second;
    if (la.iters_remaining <= 0) {
      it = active_lookaheads.erase(it);
      continue;
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

        // v3: only record issue timing outside warmup -- branch_log() is
        // itself empty throughout warmup, so a seq recorded here during
        // warmup would collide with the "nothing has happened yet" value 0.
        if (!intern_->warmup) {
          uint64_t owner_key = la.owner_ip.to<uint64_t>();
          auto& pending = pending_issue_seqs[owner_key];
          pending.push_back(speculative_pollution::current_seq());
          if (pending.size() > PENDING_QUEUE_CAP) {
            pending.pop_front();
            per_pc_queue_evictions[owner_key] += 1; // v3: make the eviction (and its bias) visible
          }
        }
      }
      // if the request was rejected (e.g. PQ full), try again next cycle without advancing
      ++it;
    } else {
      it = active_lookaheads.erase(it); // crossed a page boundary; stop rather than guess across pages
    }
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
    out << "pc_hex,total,wasted,wasted_fraction,gating_branch_ip_hex,gating_branch_confidence,dropped_no_candidate,queue_evictions,stale_dropped\n";
    for (const auto& [ip_key, total] : per_pc_total) {
      auto wasted_it = per_pc_wasted.find(ip_key);
      uint64_t wasted = (wasted_it != per_pc_wasted.end()) ? wasted_it->second : 0;
      double wasted_fraction = total > 0 ? static_cast<double>(wasted) / static_cast<double>(total) : 0.0;

      // v3: report the LOCKED gating branch (what actually scored this PC's
      // samples), not a fresh recompute -- keeps the reported branch/
      // confidence consistent with what's behind wasted_fraction above.
      uint64_t identified_ip = 0;
      double confidence = 0.0;
      auto locked_it = locked_gating_branch.find(ip_key);
      if (locked_it != locked_gating_branch.end()) {
        identified_ip = locked_it->second.ip;
        confidence = locked_it->second.total_votes > 0
            ? static_cast<double>(locked_it->second.votes) / static_cast<double>(locked_it->second.total_votes)
            : 0.0;
      }

      auto dropped_it = per_pc_dropped_no_candidate.find(ip_key);
      uint64_t dropped = (dropped_it != per_pc_dropped_no_candidate.end()) ? dropped_it->second : 0;
      auto evict_it = per_pc_queue_evictions.find(ip_key);
      uint64_t evictions = (evict_it != per_pc_queue_evictions.end()) ? evict_it->second : 0;
      auto stale_it = per_pc_stale_dropped.find(ip_key);
      uint64_t stale = (stale_it != per_pc_stale_dropped.end()) ? stale_it->second : 0;

      out << fmt::format("{:#x},{},{},{:.6f},{:#x},{:.6f},{},{},{}\n", ip_key, total, wasted, wasted_fraction, identified_ip, confidence, dropped, evictions,
                          stale);
    }
  }
  {
    std::ofstream out("pf_gating_branches_histogram.csv");
    out << "gating_branches_bucket,count\n";
    for (const auto& [bucket, count] : gating_branches_histogram) {
      out << bucket << "," << count << "\n";
    }
  }
  fmt::print("loop_guided: branch_log windows truncated by retention eviction: {}\n", speculative_pollution::truncated_window_count());
}
