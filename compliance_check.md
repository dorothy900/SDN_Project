# Dissertation Requirements Compliance Check

## Summary

**Overall Status: ✅ Weeks 1–6 implemented and passing (48/48 tests), structurally aligned with
requirements — and, as of 2026-08-11, also verified against a real Mininet/OVS network, not just
the offline simulation (see §4a).** This banner originally read "100% ALIGNED" when Weeks 3–6 were
still placeholders (2026-07); see §"Final Verdict" at the bottom and
`results/reports/experiment_validation_report.md` for what that claim didn't cover at the time and
what's been verified against actual code since.

---

## 1. Research Questions (Rq) Coverage

| Research Question | Status | Support in Code |
|------------------|--------|----------------|
| **Rq1**: How determine rerouting necessity? | ✅ Ready | Stability mechanisms designed in `config/decision.yaml` |
| **Rq2**: How stability reduce congestion? | ✅ Ready | Hold-down, hysteresis, change budget all configured |
| **Rq3**: Compare with conventional approaches? | ✅ Ready | Static shortest path & dynamic baselines in `src/routing/` |

---

## 2. Concept Notes Requirements

### A. Traffic Classes ✅

**Configuration File**: [`config/policies.yaml`](file:///home/vboxuser/sdn-project/config/policies.yaml)

| Class | Services | Priority | QoS Threshold | Immediate Reroute? |
|-------|----------|----------|---------------|-------------------|
| High Priority | VoIP, Video | 1 | 0.05 | Yes ✅ |
| Medium Priority | Web | 2 | 0.15 | No ✅ |
| Low Priority | File Transfer | 3 | 0.25 | No ✅ |

**Implementation Location**: [`src/stability/traffic_policy.py`](file:///home/vboxuser/sdn-project/src/stability/traffic_policy.py)

---

### B. Network Change Scenarios ✅

**Implementation Location**: [`experiments/`](file:///home/vboxuser/sdn-project/experiments)

| Scenario | File Created | Purpose |
|----------|--------------|---------|
| Increasing Traffic Demand | `scenario_increasing_load.py` | ✅ Exists as planned |
| Local Link Congestion | `scenario_congestion.py` | ✅ Exists as planned |
| Link Failure & Recovery | `scenario_failure_recovery.py` | ✅ Exists as planned |
| (Stale Stats) | `scenario_stale_stats.py` | ✅ Exists as planned |
| Baseline Comparison | `run_baseline_comparison.py` | ✅ Exists as planned |

---

### C. OpenDaylight Measurements ✅

**Module**: [`src/monitor/odl_client.py`](file:///home/vboxuser/sdn-project/src/monitor/odl_client.py)

| Metric | Support in Model |
|--------|-----------------|
| Link utilization | ✅ `LinkStatistics.utilization` |
| Port statistics | ✅ `PortStatistics` complete |
| Flow statistics | ✅ Framework ready |
| Link status (up/down) | ✅ `LinkStatistics.status` |

**Model**: [`src/monitor/models.py`](file:///home/vboxuser/sdn-project/src/monitor/models.py)

---

### D. Path Cost Function ✅

**Configuration**: [`config/decision.yaml`](file:///home/vboxuser/sdn-project/config/decision.yaml#L28-L33)

```yaml
path_cost_weights:
  alpha: 0.4    # Utilization weight
  beta: 0.3     # Delay weight
  gamma: 0.2    # Loss weight
  delta: 0.05   # Priority weight
  epsilon: 0.05 # Reliability weight
```

**Formula**: Cost = α·Utilization + β·Delay + γ·Loss + δ·Priority + ε·Reliability  
**Implementation Location**: [`src/decision/path_cost.py`](file:///home/vboxuser/sdn-project/src/decision/path_cost.py) — implemented and unit-tested (`tests/test_path_cost.py`, `results/stage4/path_cost_unit_tests.txt`)

---

### E. Stability Mechanisms ✅

**Configuration**: [`config/decision.yaml`](file:///home/vboxuser/sdn-project/config/decision.yaml)

| Mechanism | Parameters | Status |
|-----------|------------|--------|
| **Threshold-based Decision** | utilization: 0.7, delay: 100ms, loss: 0.01 | ✅ |
| **Hysteresis/Persistence** | persistence_seconds: 5, cooldown_seconds: 10 | ✅ |
| **Hold-down Timer** | duration_seconds: 10, enabled: true | ✅ |
| **Change Budget** | max_updates_per_minute: 10 | ✅ |
| **Minimum Improvement** | absolute: 0.1, relative: 0.15 | ✅ |

**Implementations**:
- [`src/decision/threshold_detector.py`](file:///home/vboxuser/sdn-project/src/decision/threshold_detector.py)
- [`src/decision/persistence_checker.py`](file:///home/vboxuser/sdn-project/src/decision/persistence_checker.py)
- [`src/decision/change_budget.py`](file:///home/vboxuser/sdn-project/src/decision/change_budget.py)
- [`src/stability/stability_manager.py`](file:///home/vboxuser/sdn-project/src/stability/stability_manager.py)

---

### F. Comparison Baselines ✅

**Module**: [`src/routing/`](file:///home/vboxuser/sdn-project/src/routing)

| Baseline | File | Status |
|----------|------|--------|
| Static Shortest Path | `static_shortest_path.py` | ✅ |
| Dynamic Link-cost Routing | `dynamic_baseline.py` | ✅ |

**Additional**: [`src/routing/graph_builder.py`](file:///home/vboxuser/sdn-project/src/routing/graph_builder.py) for weighted graph construction

---

### G. Evaluation Metrics ✅

**Module**: [`evaluation/`](file:///home/vboxuser/sdn-project/evaluation)

| Category | Metrics | Files |
|----------|---------|-------|
| **Network Performance** | End-to-end delay, Throughput, Packet loss | `calculate_metrics.py` |
| **Routing Stability** | Reroute count, Flow update count | `calculate_metrics.py` |
| **Controller Efficiency** | Decision time | `calculate_metrics.py` |

**Parser**: [`evaluation/parse_results.py`](file:///home/vboxuser/sdn-project/evaluation/parse_results.py)

---

## 3. Project Structure Alignment ✅

```
sdn-dissertation/
├── README.md                       ✅ Complete
├── requirements.txt                ✅ Exists
├── .gitignore                      ✅ Exists
│
├── scripts/                        ✅ Complete
│   ├── start_odl.sh
│   └── start_topology.sh
│
├── config/                         ✅ Complete
│   ├── topology.yaml
│   ├── links.yaml
│   ├── policies.yaml              ✅ Traffic classes
│   └── decision.yaml              ✅ Stability params
│
├── topology.py                     ✅ Complete
│
├── src/                            ✅ Complete
│   ├── monitor/                   ✅ Week 1-2 done
│   ├── routing/                   ✅ Baselines ready
│   ├── decision/                  ✅ Stability modules
│   └── stability/                 ✅ Stability mechanisms
│
├── experiments/                    ✅ Scenarios ready
├── evaluation/                     ✅ Metrics ready
├── tests/                          ✅ Unit tests
├── run_experiment.py               ✅ Single entry point
└── results/                        ✅ Output organized
```

---

## 4. Week-by-Week Task Verification

### Week 1: System Validation ✅ 100% Done
- [x] Topology verification — `run_experiment.py --stage 1` now actually loads
      `data/Geant2012.graphml` and computes node/link counts, connectivity, diameter, and average
      degree (previously `stage1()` just printed and wrote hardcoded strings; fixed 2026-08-09 via
      `experiments/run_topology_validation.py`, reusing the real logic that already existed in
      `verify_week1.py` but was never wired into the main entry point)
- [x] Connected nodes check
- [x] Alternative paths
- [x] Monitor module separation
- [x] Statistics model format
- [x] Integration complete

### Week 2: Network State ✅ 100% Done — `run_experiment.py --stage 2` now actually runs
`experiments/run_network_state_validation.py` (rate calc, utilization, history window, link status,
`get_network_state()` interface, integration report) and produces every file
`README.md` promises, including `rate_validation.csv` which `stage2()` previously never generated at
all (it was a no-op — three `print()` lines and nothing else; fixed 2026-08-09, reusing the real
logic that already existed in `verify_week2.py`).
- [x] Real-time rate calculation
- [x] Link utilization mapping
- [x] Rolling history window
- [x] Link status detection
- [x] Network state interface
- [x] Integration complete

### Week 3: Baseline Routing ✅ Done
- [x] Graph builder / candidate paths
- [x] Static shortest path (deterministic)
- [x] Flow installer (real bidirectional OpenFlow-style rules)
- [x] Dynamic baseline (immediate reroute, now also reacts to link failure/recovery events)
- [x] Baseline comparison + repeated trials

### Week 4: Decision Engine ✅ Done
- [x] Threshold detection, persistence, path cost, minimum benefit, change budget, decision logging
- [x] Stage integration — `DecisionEngine` now actually combines hysteresis, hold-down, emergency
      failure bypass, and recovery-window switch-back (previously missing; fixed 2026-08-02)

### Week 5: Stability Control ✅ Done
- [x] Hysteresis, hold-down, emergency reroute, recovery protection, priority-aware policy
      (all individually validated in `results/stage5/`)
- [x] Priority policy is now also wired into `DecisionEngine.evaluate_service_congestion()`
      and exercised end-to-end in Stage 6 (previously validated only in isolation; fixed 2026-08-02)

### Week 6: Result Pipeline ✅ Done
- [x] Traffic generator, metrics parser, repeated-run aggregation, full-system validation
- [x] All 4 scenario scripts (increasing load, congestion, failure/recovery, stale stats) now
      drive the real `src/routing`/`src/decision`/`src/stability` code against the real GEANT
      topology — previously every outcome was a hardcoded `if algorithm == X` branch; fixed 2026-08-02
- [x] Added `scenario_priority_policy.py` (Experiment E) to close the one Part-2 hypothesis
      (H-E2) that had no dedicated Stage 6 run

### Known open items (see `results/reports/experiment_validation_report.md`)
- `src/monitor/odl_client.py::get_port_statistics()` is still a stub (`return []`, even on a
  successful response) and isn't called anywhere in the current pipeline. This is **not a
  functional gap**: `StatisticsCollector.parse_ovs_port_stats()` already gets the same port/link
  traffic data directly from OVS (`ovs-ofctl dump-ports`) and is what actually feeds rate →
  utilization → `NetworkState` → path-cost-based routing. `ODLClient` is kept as a documented,
  intentionally-unfinished alternative (controller-mediated rather than OVS-direct) — decision
  made 2026-08-02: leave as-is, documented, rather than guess at ODL's RESTCONF schema without a
  live controller to verify against.
- `scripts/start_odl.sh` is an explicit placeholder — the real startup commands depend on
  which ODL distribution/version is installed on the actual testbed. This remains open: the
  project's actual chosen deployment path is direct OVS control (`ovs-ofctl`), not
  controller-mediated ODL, so this was deprioritized rather than fixed — see §4a.
- ~~T-001–T-004 (Mininet clean-startup, `pingall`, real rule-push checks) require the real
  testbed and have not been run in this (offline) environment.~~ **No longer accurate as of
  2026-08-11** — done against a real Mininet/OVS network; see §4a. The ODL-specific half of this
  item (an actual ODL controller instance) is still untested, consistent with the `ODLClient`
  decision above.

---

## 4a. Real Mininet/OVS Deployment Verification (2026-08-11, extends Week 6)

Everything above this section runs entirely offline (`NetworkState` seeded with synthetic
statistics, no real network ever built). This section covers the one piece that can't be verified
that way: whether the project's actual deployment mechanism — computing a path, then pushing it as
`ovs-ofctl` rules with no controller — works against a real running network at all. Two bugs
surfaced only once this was actually tried, neither visible from the offline simulation:

- **`FlowInstaller` switch/host naming didn't match `topology.py`'s real naming.** It guessed
  `s{node_id+1}`/`h{node_id+1}`; `topology.py` actually numbers switches/hosts by sorted *string*
  order of the GEANT graph's node IDs. These coincide only for single-digit node IDs — 38 of 40
  GEANT nodes were wrong. Fixed by adding an optional `node_mapping` parameter (backward-compatible;
  the guess formula remains the default since the offline simulation never checks these names
  against anything real). Regression-tested in `tests/test_flow_installer.py`.
- **The generated rule strings were never valid OpenFlow syntax.** e.g.
  `ovs-ofctl add-flow s13 priority=100,h13->h38,actions=output:s1` — `h13->h38` isn't a real match
  field, and `output:s1` needs a numeric port, not a switch name. This was always intentional as
  human-readable text for `dump_flows()`'s offline display, not something meant to be executed —
  but that intent was never documented, so it looked like a real (broken) deployment path.
  `scripts/mininet_path_verification.py` now does the real translation once a network is actually
  running: real port numbers queried live via `ovs-vsctl ... ofport`, real `nw_dst=<ip>` match
  fields, correct `-O OpenFlow13`.

A third issue was found and fixed during verification itself, not from reading code: switches were
first brought up with `failMode=standalone`, whose implicit table-miss action floods unmatched
traffic via plain L2 learning with no loop prevention. GEANT is cyclic (61 edges over 40 nodes),
so this caused a real broadcast storm (one switch's fallback rule processed 16 million packets in
about two minutes) — the likely root cause of an earlier session where system load forced a
reboot. Switched to `failMode=secure` (drops unmatched traffic by default), which also matches the
project's actual design better: no reliance on switch auto-learning, only explicit pushed rules.

**Results, both against the full real 40-switch `GeantTopology`:**

| Script | What it checks | Result |
|--------|-----------------|--------|
| `scripts/mininet_path_verification.py` | A `GraphBuilder`-computed path, translated to real OpenFlow rules and pushed with no controller, is actually followed by real ICMP traffic | 0% packet loss |
| `scripts/mininet_failure_recovery_demo.py` | A real link failure (Mininet interface brought down, not just a removed rule) is detected via `TopologyState.mark_link_failed`, `GraphBuilder` reroutes around it, stale rules are purged and replaced, traffic recovers, and the graph reflects the link's return once restored | 0% packet loss before failure, 0% after reroute, recomputed path after recovery == original path |

This closes the "does this actually deploy" question the offline simulation structurally can't
answer, without replacing the offline experiments — those remain the evidence for whether the
stability-aware algorithm itself performs well, which these two scripts don't re-test (see the
scripts' own docstrings for that distinction).

---

## 5. Minimum Viable Contribution ✅

### From Concept Notes:
> The minimum contribution of this project is to design and implement a stability-aware rerouting decision mechanism for SDN traffic engineering.

**Status**: ✅ Framework COMPLETE
- Stability mechanism configuration: `config/decision.yaml`
- Decision modules in `src/decision/`
- Traffic policies in `config/policies.yaml`
- Network state interface: `get_network_state()`

---

## 6. Check Summary

| Area | Status | Notes |
|------|--------|-------|
| Research Questions | ✅ Covered | All Rq1-Rq3 |
| Traffic Classes | ✅ Exact | High/Medium/Low as specified |
| Scenarios | ✅ Exact | 4 scenarios + baseline |
| Stability Mechanisms | ✅ Exact | All 5 mechanisms configured |
| Parameter justification | ⚠️ Now measured, not optimal | Every threshold in `decision.yaml` was a fixed value set once at the first commit with no tuning process. Added `experiments/run_sensitivity_analysis.py` (real sweeps against `DecisionEngine`/`StabilityManager`) so the reaction-latency-vs-churn trade-off is measured, not asserted — see `results/reports/experiment_validation_report.md` §12. This does not claim the defaults are optimal, only that their cost is now known. |
| Cost Function | ✅ Exact | α-β-γ-δ-ε weights set |
| Baselines | ✅ Exact | Static + Dynamic |
| Metrics | ✅ Exact | All required |
| Project Structure | ✅ Cleaned up | `src/analysis/` and `src/traffic/` were empty scaffolding directories from the original 2026-07-18 skeleton, never populated (no `__init__.py`, nothing ever imported from them) — removed. The functionality those names implied already lives elsewhere: "traffic" in `experiments/traffic_generator.py` + `src/stability/traffic_policy.py`; "analysis" in the top-level `evaluation/` package. |
| Test coverage | ✅ Closed three gaps | Added `tests/test_decision_engine.py` (7 tests), `tests/test_calculate_metrics.py` (9 tests), and `tests/test_flow_installer.py` (4 tests, added 2026-08-11 after the real-deployment naming bug in §4a) — `DecisionEngine`, `evaluation/`, and `FlowInstaller` were previously only exercised indirectly, with no dedicated, fast, isolated unit tests despite every sibling module having one. 48/48 tests now passing. |
| Real deployment | ✅ Verified 2026-08-11 | Static path push and dynamic failure/recovery both confirmed against a real 40-switch Mininet/OVS network with no controller, 0% packet loss — see §4a. |

---

## Final Verdict

**✅ Weeks 1–6 are implemented and passing (48/48 tests), Stage 6's comparative
experiments are driven by the real routing/decision/stability code rather than
hardcoded outcomes, and — as of 2026-08-11 — the actual deployment mechanism (path
computation → real OpenFlow rule push, no controller) has been verified end to end
against a real 40-switch Mininet/OVS network, including a live link failure/recovery
run (§4a).** See `results/reports/experiment_validation_report.md` for the full
account of what was found broken/incomplete and fixed. Remaining open items are
ODL-specific (the `odl_client.py` stub and `scripts/start_odl.sh` — the project's
actual deployment path is direct OVS control, not controller-mediated ODL, so these
were deprioritized rather than fixed). This document was last verified against the
actual code on 2026-08-11 — treat any date after that as unverified.
