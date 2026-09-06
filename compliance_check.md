# Dissertation Requirements Compliance Check

## Summary

**Overall Status: ✅ Weeks 1–6 implemented and passing (178 tests as of 2026-09-06; 48/48 when
this banner was written), structurally aligned with requirements — and, from 2026-08-11 onward,
also verified against a real Mininet/OVS network, not just the offline simulation (see §4a, and
the 2026-09-06 real `tc netem` loss-signal validation in the last "Final Verdict" update).** This
banner originally read "100% ALIGNED" when Weeks 3–6 were still placeholders (2026-07). The
"Final Verdict" section at the bottom has three dated updates (2026-08-11, 08-24, 09-06) — read
all three; earlier subsection dates and file paths are preserved as-written (dated journal),
current paths are in `README.md`. See also `results/reports/experiment_validation_report.md`.

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
| Increasing Traffic Demand | `increasing_load.py` | ✅ Exists as planned |
| Local Link Congestion | `congestion.py` | ✅ Exists as planned |
| Link Failure & Recovery | `failure_recovery.py` | ✅ Exists as planned |
| (Stale Stats) | `stale_stats.py` | ✅ Exists as planned |
| Baseline Comparison | `baseline_comparison.py` | ✅ Exists as planned |

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
  delta: 0.05   # Link instability/churn weight (was "priority" until 2026-08-12, see below)
  epsilon: 0.05 # Reliability weight
```

**Formula**: Cost = α·Utilization + β·Delay + γ·Loss + δ·Link-instability + ε·Reliability  
**Implementation Location**: [`src/decision/path_cost.py`](file:///home/vboxuser/sdn-project/src/decision/path_cost.py) — implemented and unit-tested (`tests/path_cost.py`, `results/decision_engine/path_cost_unit_tests.txt`)

**Fixed 2026-08-12 (identified 2026-08-11): self-influence / offered-load accounting.**
Every edge's utilization term used to be the link's *currently observed* utilization
(`GraphBuilder._calculate_edge_cost`, fed by `NetworkState.get_link_stats()`) for *both* the current
path and any candidate — but the current path already really carries this flow (its observed
utilization already includes it), while a candidate doesn't yet. Comparing "current path with the
flow" against "candidate path without it" systematically made candidates look cheaper than they'd
actually be once the flow moved there.

Fixed by adding `offered_load_utilization` (a utilization fraction representing this flow's own
bandwidth demand) to `PathCost.calculate_path_cost()`/`is_improvement()`/`compare_paths()`, applied
asymmetrically: added to a candidate path's per-edge utilization before pricing it (`dataclasses.
replace()` on a copy of each edge's real `LinkStatistics`, clamped at 1.0, then priced through
`GraphBuilder._calculate_edge_cost()` directly — the precomputed whole-graph weights can't be reused
since only this path's specific edges need the bump), left untouched for the current path (which
already reflects reality). Threaded through the real decision chain end to end, all backward-compatible
(`offered_load_utilization: Optional[float] = None`, so every existing caller's behavior is unchanged
unless it opts in): `DecisionEngine.evaluate_pair/evaluate_service_congestion/evaluate_failure/
evaluate_recovery_switchback/_evaluate_congestion/_execute_reroute` → `PathCost` → `ProposedDriver`
(`experiments/simulation_common.py`, new constructor parameter) → `make_drivers()`.

4 new tests in `tests/path_cost.py` demonstrate the actual bug and the fix: a candidate that looks
like a strong improvement (accepted under the minimum-improvement gate) when its own future load is
ignored can flip to rejected once that load is correctly priced in — the concrete failure mode this
was about. Full suite 96/96 passing.

**Wired into a real scenario the same day.** Added `simulation_common.py::
resolve_offered_load_utilization(link_id, offered_load_mbps)`: converts a flow's real Mbit demand
into a utilization fraction using the same real per-link GEANT bandwidth data `topology.py` resolves
for the live Mininet deployment (`src/monitor/link_capacity.py`) — one real capacity source, not an
arbitrary assumed one. `experiments/increasing_load.py` now uses this for `flow-video-1` (24Mbps, the
real flow mapped onto `PRIMARY_PAIR` — see `simulation_common.py`'s `PRIMARY_PAIR` comment), setting
`ProposedDriver.offered_load_utilization` before running the scenario.

Verified live, directly comparing with/without on the same seeded run (sample 12, load=0.90): the
real hotspot link resolves to 150Mbit real capacity (a real "10 Gbps" GEANT edge), so 24Mbps -> 0.16
utilization. `PathCost.compare_paths` at that point: old_cost=0.5392 (current path `2->32->34->7`),
new_cost without the fix=0.2709 (candidate `2->0->34->7`, accepted), new_cost with the fix (offered_
load=0.16)=0.4259 (**still accepted** — margin survives, just smaller). Pushed further to find the
actual flip point: new_cost=0.5400 at offered_load=0.30 (**rejected** — this specific candidate's
margin over the current path was large enough that flow-video-1's real 24Mbps didn't flip the outcome
this run, but a modestly larger flow, or the same flow on a lower-capacity link, does). Confirms the
fix has real teeth in the actual decision pipeline, not just in `tests/path_cost.py`'s synthetic
cases — this run's own outcome happens not to be one of the cases it flips.

Other scenario experiments (`congestion.py`, `failure_recovery.py`, `stale_stats.py`,
`priority_policy.py`) still call with the default `None` — extending this wiring to them is a natural
follow-up, not done as part of this pass. Real traffic-engineering systems (e.g. MPLS-TE) typically
get this value from admission-control reservation, which this project still does not implement.

**Known limitation (found 2026-08-12, both now fixed): delay_ms and packet_loss had no
real data source anywhere in the monitor pipeline.** `grep -rn "delay_ms=" src/` returned zero
matches before this pass — nothing in `src/` ever assigned it a value; `StatisticsCollector.
aggregate_link_statistics()` (the function that builds `LinkStatistics` from real OVS data) never
passed `packet_loss` either, so both fields silently defaulted to `None`. `GraphBuilder.
_calculate_edge_cost()` treats `None` as `0.0`, meaning in a genuine live deployment (not the
offline simulation, which fabricates these values directly) **β and γ's terms would always evaluate
to zero** — utilization was the only signal actually driving routing decisions. Two different root
causes, two different fixes:
- **packet_loss — fixed.** Real `ovs-ofctl dump-ports` output carries a `drop=` counter on both the
  rx and tx lines (confirmed against a live OVS bridge), which `parse_ovs_port_stats()` simply never
  parsed. Now parses it, and `StatisticsCollector.calculate_loss_rate()` (`tx_dropped /
  (tx_packets + tx_dropped)`) feeds a real loss rate into `aggregate_link_statistics()`. Covered by
  `tests/statistics_collector.py`.
- **delay_ms — fixed via active probing.** Port byte/packet counters cannot yield a latency
  measurement, so this needed a different mechanism: `src/monitor/delay_prober.py` parses real
  `ping` RTT output into a one-way link-delay estimate (subtract known host-link overhead, halve —
  Mininet's `TCLink` applies the same `tc netem` delay symmetrically to both directions of a link,
  confirmed via `inspect.getsource(TCLink.__init__)`); `scripts/mininet_delay_measurement.py`
  installs temporary explicit OpenFlow rules to force host-to-host traffic across exactly one real
  GEANT link at a time, pings, and writes the measured value into a real `NetworkState`/
  `LinkStatistics` record — the same pipeline `StatisticsCollector` feeds, not a disconnected
  number. Verified live on the full 40-switch topology: 61/61 real GEANT links measured
  successfully, average one-way delay 12.28ms against a configured 10ms per inter-switch link (the
  ~2ms gap is real queueing/htb overhead, not simulation noise). Unit-tested in
  `tests/delay_prober.py` (pure parsing logic, no Mininet dependency).

**Bug found and fixed while building the above (2026-08-12): `link=TCLink` was missing from every
`Mininet()` constructor call in this project.** `topology.py`'s `addLink(..., bw=100, delay=...)`
calls only take effect if `Mininet()` is built with `link=TCLink`; without it, Mininet silently uses
plain, unshaped `Link` (no bandwidth cap, no delay) and the `bw=`/`delay=` parameters are discarded.
Caught by diagnosing why every link's measured delay came back exactly 0.0ms on the first live run —
`tc qdisc show dev <intf>` on a switch interface showed `qdisc noqueue` (nothing installed) instead
of a `netem` qdisc. This affected all three Mininet orchestration scripts (`mininet_path_
verification.py`, `mininet_failure_recovery_demo.py`, `mininet_delay_measurement.py`), meaning every
prior real-network experiment in this project ran on ideal (0ms, unlimited-bandwidth) links despite
`topology.py` claiming otherwise. All three now pass `link=TCLink` and were re-run to confirm
results still hold: path verification's 4-hop ping now shows a real ~70ms RTT (previously would have
been near-zero); the failure-recovery demo's before/after/recovery pings and path decisions are
unchanged in outcome (still SUCCESS / SUCCESS / path restored), now over genuinely shaped links.

Separately, the *offline simulation's* `experiments/simulation_common.py::set_link_condition()` had
its own, independent version of this problem: every pilot scenario only ever passed `utilization=`
(never `delay_bump_ms`/`loss_bump`), so delay/loss stayed completely flat regardless of how
congested the simulated link became — unrealistic, since utilization/delay/loss are correlated
symptoms of the same congestion in a real network, not independent quantities. Fixed 2026-08-12:
delay/loss are now derived from current utilization via `congestion_delay_bump_ms()` (M/M/1-inspired
queueing delay, grows as `utilization/(1-utilization)`) and `congestion_loss_bump()` (near-zero below
70% utilization, then rises quadratically) — chosen, documented models, not universal laws.

**A consequence worth flagging explicitly: this made the additive cost formula double-count the
same congestion signal.** Once delay and loss are functions of utilization, `∂cost/∂u = α +
β·(∂delay/∂u)/1000 + γ·(∂loss/∂u)`, not just α. Evaluated near u=0.9 at the default weights: 0.4
(direct) + 0.24 (via delay) + 0.044 (via loss) = 0.684 total — utilization's real influence on cost
was ~1.7x the α term alone, because delay and loss were re-punishing the same underlying signal
they're derived from. This is a real form of multicollinearity in the cost formula.

**Fixed 2026-08-12: β/γ now price residuals, not raw delay/loss.** Chosen over documenting-as-a-
known-limitation (the user explicitly picked the more rigorous fix over leaving it as-is). Added
`src/routing/congestion_model.py::predicted_delay_ms()`/`predicted_loss()` — the same
utilization -> expected-delay/loss curves the offline simulator already used to *generate* synthetic
values (`congestion_delay_bump_ms`/`congestion_loss_bump`, now defined once here and imported by
both `experiments/simulation_common.py` and `GraphBuilder`, so the two can't drift apart). `_calculate_
edge_cost` now computes `delay_residual = link_stats.delay_ms - predicted_delay_ms(utilization)` and
`loss_residual = link_stats.packet_loss - predicted_loss(utilization)`, and feeds *those* into β/γ
instead of the raw measurements. A link whose delay/loss exactly match what its utilization already
predicts now contributes zero extra cost from β/γ — only genuinely anomalous congestion (worse than
utilization explains: a real queueing spike, a physically longer link) gets priced beyond α. Residuals
are signed, not clamped at zero, so a link doing *better* than predicted is rewarded rather than
merely never penalized — this is deliberate (see `tests/graph_builder.py::
test_link_performing_better_than_predicted_costs_less_not_just_never_penalized`), consistent with
treating the formula as pricing genuine information, not one-directional punishment. Covered by
`tests/graph_builder.py`'s three residual tests (exact match -> zero extra cost, anomalous delay ->
higher cost, better-than-predicted -> lower cost).

Note this changes the numeric cost values (and therefore possibly path choices) reported by every
experiment that goes through `GraphBuilder` with real or simulator-derived delay/loss —
`baseline_comparison.py`, `sensitivity_analysis.py`, `pilot_experiments.py`, `weight_search_
comparison.py` — since they previously priced raw values. Results documented elsewhere in this file
and in prior week's reports reflect the pre-fix formula; re-running those experiments to refresh the
recorded numbers is a natural follow-up, not done automatically as part of this fix.

**Fixed 2026-08-12: δ's cost term repurposed from a dead "priority" placeholder to a real link
instability/churn signal.** `_calculate_edge_cost` previously multiplied δ by a hardcoded local
`priority = 0.0`, never connected to anything — structurally inert regardless of δ's value.
Priority doesn't fit this formula cleanly anyway: it's inherently a per-flow concept (VoIP vs. Web
vs. File Transfer), but `GraphBuilder` builds one shared graph for every flow, so there was no
natural per-link "priority" value to plug in without building a separate graph per service type (a
bigger change, and arguably redundant with `TrafficPolicy`'s existing per-service effective
thresholds at the decision layer). δ now multiplies a genuine link-level property instead: how
often a link has recently been added to or removed from an installed path
(`NetworkState`/`LinkChurnTracker.get_link_churn_score()`, a rolling-window count in the same style
`ChangeBudget` already uses), recorded at the single choke point every real reroute passes through
(`DecisionEngine._execute_reroute()`). This is complementary to, not redundant with, the existing
timing gates (persistence/hold-down/change budget) — those control *when* a reroute is allowed;
this makes recently-churned links look less attractive in the *cost comparison itself*, discouraging
oscillation back onto a link that was just swapped out. Covered by `tests/link_churn_tracker.py`
and `tests/graph_builder.py`; full pytest suite (60/60) and `experiment.py --stage 1`–`6` re-verified
after the change.

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
├── experiment.py               ✅ Single entry point
└── results/                        ✅ Output organized
```

---

## 4. Week-by-Week Task Verification

### Week 1: System Validation ✅ 100% Done
- [x] Topology verification — `experiment.py --stage 1` now actually loads
      `data/Geant2012.graphml` and computes node/link counts, connectivity, diameter, and average
      degree (previously `stage1()` just printed and wrote hardcoded strings; fixed 2026-08-09 via
      `experiments/topology_check.py`, which now owns this logic)
- [x] Connected nodes check
- [x] Alternative paths
- [x] Monitor module separation
- [x] Statistics model format
- [x] Integration complete

### Week 2: Network State ✅ 100% Done — `experiment.py --stage 2` now actually runs
`experiments/network_state_check.py` (rate calc, utilization, history window, link status,
`get_network_state()` interface, integration report) and produces every file
`README.md` promises, including `rate_validation.csv` which `stage2()` previously never generated at
all (it was a no-op — three `print()` lines and nothing else; fixed 2026-08-09, which now owns
this logic).
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
      (all individually validated in `results/stability/`)
- [x] Priority policy is now also wired into `DecisionEngine.evaluate_service_congestion()`
      and exercised end-to-end in Stage 6 (previously validated only in isolation; fixed 2026-08-02)

### Week 6: Result Pipeline ✅ Done
- [x] Traffic generator, metrics parser, repeated-run aggregation, full-system validation
- [x] All 4 scenario scripts (increasing load, congestion, failure/recovery, stale stats) now
      drive the real `src/routing`/`src/decision`/`src/stability` code against the real GEANT
      topology — previously every outcome was a hardcoded `if algorithm == X` branch; fixed 2026-08-02
- [x] Added `priority_policy.py` (Experiment E) to close the one Part-2 hypothesis
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
  against anything real). Regression-tested in `tests/flow_installer.py`.
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
| Parameter justification | ⚠️ Now measured, not optimal | Every threshold in `decision.yaml` was a fixed value set once at the first commit with no tuning process. Added `experiments/sensitivity_analysis.py` (real sweeps against `DecisionEngine`/`StabilityManager`) so the reaction-latency-vs-churn trade-off is measured, not asserted — see `results/reports/experiment_validation_report.md` §12. This does not claim the defaults are optimal, only that their cost is now known. |
| Cost Function | ✅ Exact | α-β-γ-δ-ε weights set |
| Baselines | ✅ Exact | Static + Dynamic |
| Metrics | ✅ Exact | All required |
| Project Structure | ✅ Cleaned up | `src/analysis/` and `src/traffic/` were empty scaffolding directories from the original 2026-07-18 skeleton, never populated (no `__init__.py`, nothing ever imported from them) — removed. The functionality those names implied already lives elsewhere: "traffic" in `experiments/traffic_generator.py` + `src/stability/traffic_policy.py`; "analysis" in the top-level `evaluation/` package. |
| Test coverage | ✅ Closed three gaps | Added `tests/decision_engine.py` (7 tests), `tests/calculate_metrics.py` (9 tests), and `tests/flow_installer.py` (4 tests, added 2026-08-11 after the real-deployment naming bug in §4a) — `DecisionEngine`, `evaluation/`, and `FlowInstaller` were previously only exercised indirectly, with no dedicated, fast, isolated unit tests despite every sibling module having one. 48/48 tests now passing. |
| Real deployment | ✅ Verified 2026-08-11 | Static path push and dynamic failure/recovery both confirmed against a real 40-switch Mininet/OVS network with no controller, 0% packet loss — see §4a. |

---

## Independence check on the cost formula's variables (2026-08-12)

The residual fix (above, and the "double-counting" note earlier in this document)
assumes utilization is correlated with delay/loss in a real network. This was
tested twice against real Mininet traffic, not asserted.

**First pass** (`scripts/mininet_correlation_check.py`, `results/correlation_check/`):
single link (s1-s2), 10 samples, monotonic utilization sweep. Found ρ(u,delay)=0.90,
but on review the design was flawed — effective sample size closer to 3 than 10
(low-utilization trials nearly duplicated each other; the 0.7/0.9 target levels'
achieved-utilization ranges overlapped), test order was swept monotonically by
utilization (confounding utilization with time/warm-up drift, never randomized),
no significance test computed, and achieved utilization never exceeded ~60%.

**Redesigned second pass** (`scripts/mininet_independence_check.py`,
`experiments/independence_stats.py`, `results/independence_check/`): 60 samples
across 3 real links (s1-s2, s5-s6, s13-s35), fully randomized (link, rate, trial)
execution order via a seeded shuffle, and a from-scratch numpy toolkit (no
scipy/statsmodels available in this environment) — Spearman + permutation test
(9999 permutations, not an asymptotic p-value table), distance correlation (dCor,
zero iff independent — catches non-monotonic dependence Spearman would miss), and
VIF (joint, not pairwise, collinearity across utilization/delay/loss together).
Tested both raw delay/loss and their residuals against `congestion_model.py`'s
prediction (residual result doubles as a diagnostic for whether that curve is
calibrated). All three statistical functions are unit-tested against synthetic
cases with a known analytic answer in `tests/independence_stats.py` (10/10 passing).

**Results:**
- utilization vs delay (raw): ρ=0.469, **p=0.0003** — real, statistically
  significant monotonic dependence. Lower than the first pass's 0.90 but far more
  trustworthy given the fixed design; the first pass's number was inflated by its
  flaws, not a more accurate estimate.
- utilization vs delay_residual: ρ=0.433, p=0.0010 — barely lower than the raw
  correlation. **This means `congestion_model.py`'s current curve (hand-picked
  `scale_ms=8.0`, never fit to real data) is doing very little of the decorrelation
  job the residual fix relies on** — most of the raw utilization-delay relationship
  is still present in what beta is supposed to price as "anomalous" delay. This
  elevates pending task 3 (fit the curve to real data) from a nice-to-have to a
  real gap in the residual fix's current effectiveness.
- dCor confirms the Spearman finding (0.646 raw, 0.628 residual) and VIF shows no
  problematic joint collinearity among the three (1.0–1.4, well under the
  conventional 5 concern threshold) — the delay finding is not an artifact of a
  hidden multivariate relationship Spearman alone would miss.
- **utilization vs loss: ρ=0.000 in both passes, loss was exactly 0.0 across all
  60 samples in the second pass, including requests deliberately made to exceed
  the link's 100Mbit cap (110/130Mbit).** Achieved utilization on those samples
  still topped out around 0.50-0.55 — the same ceiling as the first pass, on a
  different link, under randomized order. This is now a reproducible pattern
  across two independent experiments, not an artifact of one flawed run: the
  bottleneck is very likely the test VM's own iperf UDP generation throughput
  (probably CPU-bound), not the link's tc/htb shaping, which was assumed
  (incorrectly) to guarantee saturation once requested rate exceeds link capacity.
  **The utilization-loss relationship remains empirically unverified after two
  attempts** — not evidence it doesn't exist, evidence this environment hasn't
  yet generated enough real load to observe it.
- delta (churn) and epsilon (reliability) are still untested — both experiments
  inject traffic via static OpenFlow rules with no DecisionEngine running, so churn
  stays at 0 throughout; testing those needs a differently-shaped experiment driven
  by real DecisionEngine reroute events, not raw traffic load.

**Follow-up needed:** a loss-focused experiment that sidesteps the generation
ceiling (e.g., testing against a link configured with a much lower `bw=` so that
the test VM's achievable throughput, whatever it tops out at, actually exceeds
that link's capacity) rather than assuming request-rate overshoot alone forces
saturation.

### Refitting scale_ms, and a real bug that refit caused (2026-08-12)

Acted on the `delay_residual` finding above: refit `congestion_delay_bump_ms`'s
`scale_ms` by ordinary least squares against the 60 real samples
(`delay - BASELINE_DELAY_MS = scale_ms * u/(1-u)`), giving `scale_ms=113.603`
(was 8.0, hand-picked). On the same 60 samples this dropped the residual-vs-
utilization Pearson correlation from 0.495 to -0.084 — close to fully decorrelated,
within the range the data actually covers.

**That range is the catch: the 60 samples never exceeded ~0.55 achieved
utilization** (the same generation-ceiling limitation noted above), so the fit
has zero real evidence past that point. `u/(1-u)` diverges hyperbolically as
u→1, and with a scale_ms 14x larger than before, extrapolating the old clamp
(0.99) out to the full range produced absurd predicted delays — 1028ms at
u=0.9, 11253ms at u=0.99. Applying this directly broke `tests/
baseline_comparison_integration.py`: a background link's real (small) delay
value fell far enough below this inflated prediction at high utilization that
`delay_residual_ms` was large and negative, dragging the whole edge's cost
below zero — `networkx`'s Dijkstra correctly raised `ValueError("Contradictory
paths found: negative weights?")` rather than silently computing a wrong
shortest path.

**Two fixes, addressing two different problems:**
- `congestion_delay_bump_ms` now clamps its utilization input at
  `MAX_UTILIZATION_FOR_EXTRAPOLATION = 0.6` (just above the real data's range)
  instead of 0.99 — past that point the predicted delay holds at the u=0.6
  plateau (176.4ms) rather than continuing to extrapolate into untested
  territory. This fixes *this specific* cause, honestly, by refusing to trust
  the fit where there's no evidence for it.
- `GraphBuilder._calculate_edge_cost` now floors its return value at
  `GraphBuilder.MIN_EDGE_COST` (0.001) regardless of the above. This is a
  correctness fix, not a calibration one: an edge weight going negative is a
  hard bug for any shortest-path algorithm, and beta/gamma's residual terms
  are deliberately signed (rewarding better-than-predicted links, not just
  refusing to penalize them further — see `_calculate_edge_cost`'s docstring),
  so nothing about the cost formula's design prevents a large enough negative
  residual from recurring under some *other* future curve or data combination.
  The floor makes that structurally impossible rather than relying on every
  future curve being well-behaved.

Regression-tested: full suite passing (78/78) after both fixes, including the
pre-existing `tests/graph_builder.py` residual-direction tests (unaffected,
since they use utilization in the well-supported [0, 0.6] range).

### Real per-link bandwidth from data/Geant2012.graphml (2026-08-12)

`topology.py` previously applied one flat `bw=100` (Mbit) to every one of the 61
real GEANT links, ignoring that 39 of those edges carry a real `LinkLabel`
attribute in the source GraphML (e.g. "10 Gbps", "155 Mbps") — sourced from
GEANT's own published map (`Provenance=Primary`, `Source=geant.net`, see the
graph-level metadata dump earlier in this project's history). The other 22
edges have no `LinkLabel` in the source data (missing from GEANT's own map,
not something this project removed).

Real values (155Mbps-10Gbps) aren't directly usable for traffic generation on
this test VM: a single iperf UDP stream already tops out around ~50-55Mbit/s
here (see the independence-check ceiling, above), so a link literally
configured at 10Gbps would never be reachable by any traffic this environment
can generate. Added `src/monitor/link_capacity.py::resolve_link_bw_mbps()` --
a pure function (no mininet import, so it's actually unit-testable in this
project's venv, unlike `topology.py` itself) mapping each real tier to a
scaled-down Mininet value that preserves the real *relative* ordering (10Gbps
links get more simulated bandwidth than 155Mbps links) while staying within a
range real generated traffic can saturate: 155Mbps->20, 1Gbps->50,
2.5Gbps->80, 10Gbps->150, "Lit Fibre" (dark fibre, no explicit capacity in the
source data) treated as the top tier (150). Links with no real label keep the
existing flat default (100) unchanged -- deliberately not guessed.
`topology.py`'s `build()` now looks up each edge's real `LinkLabel` and
resolves it per-link instead of using one constant for all 61 links. 5 new
unit tests in `tests/link_capacity.py`; full suite 83/83 passing; the real
`build()` verified live (`/usr/bin/python3`, which has the mininet package)
against the actual GraphML file: 40 switches, real per-edge bandwidth
distribution 150Mbit x27 / 100Mbit(default) x21 / 50Mbit x6 / 80Mbit x5 /
20Mbit x2 = 61.

**Known consequence, not yet addressed:** `scripts/mininet_correlation_check.py`
and `scripts/mininet_independence_check.py` both hardcode
`LINK_CAPACITY_MBPS = 100.0` for their utilization calculation
(`achieved_u = tx_mbps / LINK_CAPACITY_MBPS`). One of the three links tested
in the independence check, s5-s6, is a real "10 Gbps" edge and is now
configured at 150Mbit (not 100) by this change. The 60 samples already
collected and used for the `scale_ms` refit above predate this topology
change (all three links were still uniformly 100Mbit when that data was
gathered), so that refit is unaffected. But **any future re-run of either
script needs to read each tested link's actual configured bandwidth instead
of assuming 100** — currently a stale, silently-wrong assumption for s5-s6
specifically if these scripts are ever run again as-is.

---

## delta/epsilon independence, and a real bug this exposed (2026-08-12)

Extended the independence check to the two variables never tested before:
delta (link instability/churn) and epsilon (reliability). Unlike
utilization/delay/loss, these are properties of the *decision system's own
bookkeeping* (`NetworkState.record_link_churn()`, called from
`DecisionEngine._execute_reroute()` whenever a reroute happens), not physical
network measurements — so this was built as an **offline** experiment
(`experiments/decision_churn_independence.py`), driving the real
`ProposedDriver`/`DecisionEngine` through a randomized sequence of
congestion/failure/recovery/quiet events across 6 real GEANT pairs, not a
Mininet script. This matches the project's established split: decision-logic
correctness is tested offline, real physical relationships need Mininet.

**A real, previously-undiscovered bug surfaced on the first run: churn score
read back as exactly 0.0 for every sample, despite reroutes visibly
happening.** Root cause: `NetworkState.record_link_churn()` accepts an
explicit `timestamp=`, but `NetworkState.get_link_churn_score()` had **no
`now` parameter at all** and always read back against real wall-clock time.
Any caller recording churn against a synthetic clock — which is exactly what
every offline scenario experiment in this project does (`now_s` starting
from 0, not real `time.time()`) — would record correctly but always read
back 0.0, since the window-eviction check compared the small synthetic
timestamp against real time and evicted it as (falsely) 60+ seconds stale on
every single read. `tests/graph_builder.py`'s existing churn test didn't
catch this because it happens to use `now = time.time()` (real time) when
recording, so it never exercised the mismatch.

**Consequence: delta has likely been silently inert in every offline scenario
experiment's actual decision-making** (`increasing_load.py`, `congestion.py`,
`failure_recovery.py`, `stale_stats.py`, `priority_policy.py`, and this new
one) since the churn-tracker feature was added — not just in isolated
unit-test checks, but in the real `PathCost`/`GraphBuilder` calls those
drivers make on every `.step()`/`.on_link_failure()`. This is a deeper version
of the same category of bug as the TCLink issue: a signal that looked wired
up correctly in isolation, silently doing nothing in the actual integration
path.

**Fixed by threading `now` through the full real call chain**, all
backward-compatible (`now: Optional[float] = None`, defaults preserve real
deployment's existing behavior where record and read both naturally use real
time): `NetworkState.get_link_churn_score(link_id, now=None)` →
`GraphBuilder.build_weighted_graph/_calculate_edge_cost/get_candidate_paths/
enumerate_candidate_paths/get_path_cost(now=None)` →
`PathCost.calculate_path_cost/find_best_path/is_improvement/compare_paths
(now=None)` → `DecisionEngine._execute_reroute`'s `compare_paths` call now
passes its own `now` → `ProposedDriver.step()`/`on_link_failure()`
(`experiments/simulation_common.py`) now pass their own `now_s` into
`find_best_path`. 3 new regression tests in `tests/network_state.py` proving
the facade now respects an explicit synthetic clock (and documenting the
real-time-default case that masked this bug). Full suite 86/86 passing.

**Real results, after the fix** (`results/decision_churn_independence/`, 90
samples, offline, no Mininet):
- utilization vs churn_score: rho=0.480, **p=0.0001** — real, significant.
  Makes sense mechanically: sustained high utilization triggers more
  reroutes, and every reroute records churn on the links it touches.
- utilization vs reliability_down: rho=-0.193, p=0.0645 — not significant at
  the conventional 0.05 threshold (borderline).
- **churn_score vs reliability_down: rho=0.037, p=0.747 — not significant.**
  This directly tests the hypothesis raised earlier in this document (a
  failing link should show elevated churn *and* elevated "recently down"
  simultaneously, since `_execute_reroute()` records churn on the very link
  an emergency reroute just moved away from) — **the hypothesis is not
  supported by this data.** Plausible reason: `on_link_failure()` only
  triggers a real reroute (and therefore churn) if the failed link is
  actually on the driver's *current* path; a prior congestion event may
  already have moved the driver off that link before the "fail" event in the
  schedule reaches it, so failure and churn don't co-occur as reliably as
  the code-reading hypothesis suggested.
- VIF (1.0–1.1) shows no problematic joint collinearity.

**Caveats, stated plainly:** samples within one pair's campaign track the
same link across time (churn is inherently about recent history), so they
are not fully independent draws — event order is randomized *across*
campaigns but not fully decoupled from within-campaign temporal structure.
Persistence was relaxed to `persistence_required_samples=1` (real default is
3) to get enough reroute events within a compact experiment — this affects
the *rate* of churn generation and should be kept in mind before treating the
correlation strength as a production-representative number, though the
qualitative finding (delta/epsilon not clearly correlated) is not obviously
an artifact of that choice.

---

## Loss finally measured for real, and why OVS's own counter missed it (2026-08-12)

Both prior loss attempts (`results/correlation_check/`, `results/independence_check/`,
70 combined samples) read exactly 0.0 loss throughout. Root cause established
via a live diagnostic (`tc -s qdisc show` vs. `ovs-ofctl dump-ports` on the
same interface, same moment, during a 60Mbit burst on a real 20Mbit-capped
link): **`ovs-ofctl dump-ports`'s `drop=` field — what `StatisticsCollector.
calculate_loss_rate()` has always read — does not see tc-netem/htb shaping
drops at all.** Confirmed live: `tc` reported 29104 real dropped packets;
OVS reported `drop=0` for that same interface at that same moment. These are
two separate accounting layers (the OVS datapath vs. the kernel queueing
discipline sitting below it), not two views of the same counter — `drop=` is
structurally blind to shaping-induced loss, which is exactly this project's
own dominant loss mechanism under congestion (every link is tc-rate-limited).
This means **`StatisticsCollector.calculate_loss_rate()`, as used by the real
production system, likely reads near-zero loss on any real link congested
purely by hitting its configured bandwidth cap** — a real, previously
unknown limitation of the "packet_loss real data source" fix documented
earlier in this file.

**Fixed** by adding `src/monitor/qdisc_stats.py` (`QdiscLossTracker`,
`parse_tc_qdisc_stats()`): reads tc's own root-qdisc "Sent X bytes Y pkt
(dropped Z, ...)" line directly, delta-based across samples (same pattern as
`StatisticsCollector`'s existing OVS byte-rate calculation). 6 unit tests in
`tests/qdisc_stats.py`, built against the real captured `tc` output from the
diagnostic run above. `StatisticsCollector.calculate_loss_rate()` itself was
left unchanged — it remains correct for what it measures (OVS-datapath-level
drops), just not sufficient alone; `scripts/mininet_loss_saturation_check.py`
now uses `QdiscLossTracker` instead.

**Real results** (`results/loss_saturation_check/`, real 20Mbit GEANT link
s5-s14, officially "155 Mbps" per `data/Geant2012.graphml`, 30 samples,
randomized requested rates spanning below/above the real cap): 20/30 samples
measured real nonzero loss, up to 61% at the highest requested rates —
genuine saturation-driven loss, finally observed.

**A second, independent measurement-definition issue surfaced in this same
run, distinct from the OVS-counter blind spot above:**
- `requested_rate/link_capacity` (an offered-load proxy, exogenous to the
  loss measurement) vs. loss: rho=0.977, **p=0.0001** — extremely strong.
- `achieved_utilization` (the same real-OVS-counter-based quantity
  `StatisticsCollector.calculate_utilization()` computes, and what alpha
  actually prices in production) vs. loss: rho=0.325, p=0.083 — notably
  weaker, not significant at the conventional threshold.

**Why they differ:** `achieved_utilization` is computed from *successfully
transmitted* bytes. Heavy loss reduces that very quantity — visible directly
in the raw samples (e.g. a 35Mbit request lost 52% of its traffic and showed
achieved_utilization=0.601, while a 16Mbit request lost ~0% and showed
achieved_utilization=0.808, higher despite requesting less). Loss suppresses
the signal meant to predict it, a real feedback effect, not a measurement
error. This matters beyond this one experiment: **the real production cost
formula's alpha term has no access to "offered load," only to this same
achieved/successful-throughput utilization** — real monitoring can only ever
observe what actually got through, never what was attempted. So the weaker,
borderline-significant correlation (0.325) is the one actually representative
of alpha-vs-loss in production, not the strong one (0.977) — a genuinely
different situation from the earlier utilization-vs-delay finding, where the
raw and production-relevant quantities agreed. Practical implication worth
flagging for future weight/curve calibration: near saturation, alpha may
*understate* true congestion severity precisely because loss is suppressing
the utilization signal that alpha reads — meaning gamma's real information
content near saturation is plausibly *less* redundant with alpha than
beta/delay's was, the opposite direction from the double-counting problem
the residual fix addressed. Not yet acted on; noted for whenever
`congestion_loss_bump`'s `onset`/`scale` are fit to real data (still open,
task 5).

**dCor check (2026-08-18): is the weak achieved_utilization-vs-loss
correlation actually a non-monotonic relationship Spearman underestimates?**
`scripts/mininet_loss_saturation_check.py` already computes distance
correlation on this data (dCor=0.5302) but never ran a significance test
against it or wrote the result up. Ran a 9999-permutation test on both
statistics against the same 30 real samples:

```
Spearman rho(achieved_utilization, loss) = 0.3248, p=0.0832  (not significant)
dCor(achieved_utilization, loss)         = 0.5302, p=0.0036  (significant)
```

dCor detects a real, statistically significant dependency that Spearman
misses — consistent with the feedback-suppression mechanism documented
above (heavy loss depresses the achieved_utilization signal itself), which
would produce a non-monotonic (e.g. rise-then-plateau-or-fall) shape rather
than a clean monotonic increase. Practical reading: alpha's blind spot under
heavy loss is *not* just "weak correlation, nothing there" — there is a real
dependency, it's just the wrong shape for a monotonic-only test to fully
credit. This reinforces (doesn't change) the existing finding just below
that beta/gamma's residual design substantially compensates for it.

**Follow-up check (same day): does the full cost formula compensate for
alpha's blind spot?** Computed on the same 30 loss samples, using the real
`config/decision.yaml` weights (alpha=0.4, beta=0.3, gamma=0.2): correlating
`requested_rate/capacity` (true offered-load severity) against alpha's term
alone (`alpha * achieved_utilization`) gives rho=0.416, p=0.022 — weak,
consistent with the blind spot above. Correlating the same offered-load
severity against the **full** `alpha + beta + gamma` cost (using real
delay/loss residuals) gives **rho=0.862, p=0.0001** — more than double the
alpha-alone correlation. The residual-based beta/gamma terms do substantially
recover the true congestion signal alpha's utilization measurement misses
under heavy loss: real delay/loss run far above what the (deflated)
utilization would predict in exactly these cases, producing large residuals
that beta/gamma correctly price. Confirms the concern raised above is
real but the formula's *existing* residual design already mitigates most of
it — not a case that additionally needs fixing on its own.

---

## Joint independence matrix: correlation matrix (once) + VIF (2026-08-12)

Redone at the user's request: rather than testing pairs one at a time across
separate experiments (as above), `experiments/joint_independence_matrix.py`
collects all 5 variables (utilization, delay, loss, delta/churn,
epsilon/reliability) from ONE unified offline experiment (90 samples, 6 real
GEANT pairs, randomized congestion/failure/recovery/quiet events via the
real `ProposedDriver`/`DecisionEngine` — same method as the delta/epsilon
check above, now also recording delay_ms/packet_loss), then computes a
single Spearman correlation matrix and VIF once per variable (5 values) —
joint, not just pairwise, dependency.

**Necessary caveat:** utilization/delay/loss are generated together here via
`congestion_model.py`'s real-data-fitted curve (`set_link_condition`'s
default path), not measured on real Mininet traffic in this run — so
utilization-delay and utilization-loss are correlated *by construction* in
this matrix, not new empirical evidence (that already exists separately,
from real Mininet traffic, in `results/independence_check/` and
`results/loss_saturation_check/`). The genuinely new information here is
delta/epsilon's relationships, which are not mechanically fixed by the
generation formula — churn/reliability come from how the real DecisionEngine
reacts to the injected conditions.

**Results** (`results/joint_independence_matrix/`):

|  | utilization | delay_ms | loss | churn_score | reliability_down |
|---|---|---|---|---|---|
| utilization | 1.000 | 0.995* | 0.875* | 0.480* | -0.193 |
| delay_ms | 0.995* | 1.000 | 0.882* | 0.486* | -0.161 |
| loss | 0.875* | 0.882* | 1.000 | 0.247* | -0.094 |
| churn_score | 0.480* | 0.486* | 0.247* | 1.000 | 0.037 |
| reliability_down | -0.193 | -0.161 | -0.094 | 0.037 | 1.000 |

(* = p<0.05, 4999-permutation test)

VIF: utilization=330.3, delay_ms=105.0, loss=219.1, **churn_score=2.0**,
**reliability_down=1.2**.

**Reading:** utilization/delay/loss's enormous VIF (100-330, far past the
conventional 5-10 concern threshold) is exactly what the caveat above
predicts — they are near-degenerate as a set in this synthetic generation,
not a new finding about the real network. **churn_score and
reliability_down's VIF (2.0, 1.2) are both low** — the actually new,
substantive result: neither is well-predicted by a linear combination of the
other four, i.e. both carry real information not redundant with
utilization/delay/loss or each other. u-churn (ρ=0.480, p<0.05) and
churn-reliability (ρ=0.037, not significant) both match the standalone
`decision_churn_independence.py` run exactly (same seed/event schedule,
delay/loss just added on top) — internally consistent, not a new
contradiction. New cross-pairs from this run: delay-churn (ρ=0.486,
significant, tracks u-churn closely since delay≈f(u) here) and loss-churn
(ρ=0.247, significant but weaker); delay-reliability and loss-reliability
both not significant.

---

## dCor coverage completed; delay-residual leftover investigated (2026-08-18)

**Coverage gap closed:** the joint matrix above only used Spearman+VIF (per
the redirect away from expensive pairwise dCor). Reused the same 90 samples
to add dCor+permutation-test (4999 permutations) for all 10 pairs. Result:
**no case where dCor found significance Spearman missed** — all 10 pairs
agree on significance, including churn_score-reliability_down (Spearman
p=0.743, dCor p=0.495, both not significant). The suspected coverage gap did
not surface anything new.

**Separately, on the real 60-sample delay dataset** (`results/independence_check/`),
dCor on `utilization vs delay_residual` (current fitted curve, scale_ms=113.603)
came back significant (dCor=0.351, p=0.013) even though Pearson (-0.084) and
Spearman (-0.074, p=0.558) both read as decorrelated — a real case of dCor
catching non-monotonic structure the other two miss.

**Investigated three candidate explanations, in order:**

1. **Parameter identifiability.** Tried freeing `BASELINE_DELAY_MS` (fixed at
   6.0 in production) jointly with `scale_ms` via 2-parameter OLS on
   `delay = a + b*u/(1-u)`. Got a=31.087, b=81.030 — but a is not physically
   plausible (real low-utilization delay across all 3 links averages
   11-13ms, not 31ms), and diagnostics confirm why: condition number 4.46,
   correlation between the a,b estimates = **-0.889** (near-degenerate
   trade-off), 95% CI for a = **[2.38, 59.79]** (too wide to mean anything).
   The joint free-intercept fit is itself unreliable given this data's range
   — not a case to build on.
2. **Fix the identifiability bug properly, retest.** Two-stage estimate:
   pin the intercept from the 12 real low-utilization samples alone (u<0.25,
   where the queueing bump is ~0 by construction, so intercept isn't
   confounded with scale) — baseline=12.301ms, physically sane — then fit
   `scale_ms` alone via single-parameter OLS on the full 60 samples:
   scale=105.422 (close to the original 113.603, confirming scale itself was
   never the unstable part). Result: **Pearson/Spearman both improved
   (-0.037, p=0.968) but dCor barely moved (0.351→0.359, still p=0.009)**.
   The identifiability bug was real (confirmed via the diagnostics above)
   but fixing it did not explain the residual dCor signal — ruled out as
   the cause.
3. **Heteroscedasticity and per-link effects, as remaining candidates.**
   Residual std by utilization tercile: 41.4 / 58.7 / 46.5 — not a clean
   monotonic increase (would indicate the queueing model's noise itself
   scales with u, which dCor can detect but a mean-fit never removes).
   Residual mean by link: s1-s2 -8.1ms, s13-s35 +15.5ms, s5-s6 +4.5ms,
   against a per-link std of ~45-53ms and n=20/link — differences are
   within noise, not a clean per-link fixed effect either. Neither
   candidate gives a clean explanation.

**Conclusion:** the ~0.35-0.36 dCor signal is not explained by the
identifiability bug (fixed, signal persisted), nor cleanly by
heteroscedasticity or a per-link offset (both checked, neither clean). Most
consistent with genuine local curvature around u≈0.32-0.4 that a single
global `u/(1-u)` shape underfits, but at n=60 (20/link) this can't be
reliably separated from sampling noise. **Decided not to keep tuning the
curve on a dataset this size — instead re-ran `scripts/mininet_
independence_check.py` with `TRIALS_PER_LEVEL` raised 2→5 (60→150 real
samples, 50/link instead of 20) to get the statistical power needed to tell
whether this is real structure or noise.** Also added permutation-test
p-values to the script's dCor output (previously reported the raw dCor value
with no significance test — the gap that let the 0.53/0.63 values from
earlier passes sit undocumented as "maybe interesting" instead of
confirmed).

**Result (150 real samples, 50/link vs the earlier 20/link):**

```
                                       n=60 (earlier)      n=150 (this run)
utilization vs delay_residual:
  Spearman  rho=-0.0743, p=0.5584      rho=-0.0347, p=0.6782   (not sig, both)
  dCor       dCor=0.3505, p=0.0126      dCor=0.2827, p=0.0008   (sig, both)
```

**Reading:** the effect *shrank* (0.351→0.283) but got *more* statistically
certain (p=0.013→0.0008), not less — the opposite of what a pure small-
sample noise artifact would do (more data should push a noise-driven
"finding" toward non-significance, not tighten its p-value while the
estimate converges downward). This is the signature of a real, small,
non-monotonic dependency between utilization and the delay residual: the
n=60 estimate was somewhat inflated (dCor has a known small positive bias at
low n), and n=150 is converging toward a smaller but genuine value around
~0.28, not toward zero. **Conclusion: the identifiability investigation
above was the right call to rule out — the residual signal is real, just
modest in size, not a curve-fitting artifact.** Practical takeaway: this is
a second-order effect (β's residual pricing is not perfectly clean, some
non-monotonic utilization-dependence leaks through), worth knowing about but
not worth further chasing with curve tweaks — the effect size (~0.28) is
far below the raw utilization-delay dependency it's supposed to have
removed (dCor=0.517 on this same 150-sample run), i.e. the residual design
is still removing the large majority of the dependency, just not all of it.

One numerical footnote from this run: `VIF(loss_residual) = 0.005` in the
new report is **not a real VIF** (VIF is mathematically bounded below by 1)
— it's a degenerate-input artifact. `loss` was 0.0 for all 150 samples
(this environment's known achieved-utilization generation ceiling, ~0.55,
never reaches the loss model's onset=0.7), so `loss_residual = 0 -
predicted_loss(u) ≈ -0.001` is a near-constant column with ~zero variance;
regressing a near-constant on the other variables produces numerically
unstable output, not a meaningful collinearity statistic. Not a new bug,
just worth flagging so this number is never read at face value.

---

## Extended real coverage past u=0.55; delay curve refit, loss curve fit attempted and rejected (2026-08-19)

The independence-check family of scripts never observed achieved_utilization
above ~0.55 on any of the three original real links (all 100/150Mbit,
comfortably above this VM's own iperf UDP generation ceiling). Added a 4th
real link to `scripts/mininet_independence_check.py`: s5-s14 (GEANT's real
"155 Mbps" tier, scaled to 20Mbit by `topology.py` -- the same one
`mininet_loss_saturation_check.py` used to reach u=0.89), with its own lower
rate schedule (4-60Mbit vs the other three's 10-130Mbit) so the same VM
generation ceiling represents a much larger fraction of *this* link's real
capacity. Also switched loss measurement to `QdiscLossTracker` (tc counters)
for all 4 links, since OVS's port counter is structurally blind to
tc/htb shaping drops (established earlier) -- this matters specifically for
s5-s14 since it actually saturates. Result: 196 usable real samples, u range
**[0.006, 0.782]**, first real coverage above 0.55.

**Finding 1 -- utilization-loss finally significant in this unified
experiment.** `rho(u, loss)=0.5918, p=0.0001`, `dCor=0.5965, p=0.0001` (all
31 nonzero-loss samples from s5-s14, the only link that saturates). Not new
in isolation (`loss_saturation_check` already established this), but the
first time it shows up in the *same* run as delay, confirming both are
measurable together without switching methodology mid-experiment.

**Finding 2 -- the u=0.6 extrapolation clamp is confirmed (not just
suspected) to underpredict real delay past that point.**
`utilization vs delay_residual: rho=0.1353, p=0.0568` (borderline),
`dCor=0.3782, p=0.0001` (up from 0.283 at n=150/u<=0.55) -- stronger than
before once real high-u data existed to reveal it. Direct check: mean
residual for u<=0.6 was +59.6ms vs **+189.3ms for u>0.6** -- because
`predicted_delay_ms` clamped its input at u=0.6, its output is *flat* past
that point (176.4ms) regardless of how much higher real u climbs, while real
measured delay kept rising (up to 696ms at u=0.651). This is exactly the
extrapolation risk `congestion_delay_bump_ms`'s docstring already flagged as
theoretical ("no real evidence above [the old ceiling]") -- now measured.

One sample was excluded before any of the above: `s13-s35 u=0.024,
delay=1368ms` -- delay two orders of magnitude above every neighboring
low-u sample at near-zero utilization, almost certainly Mininet/VM
scheduling jitter, not a congestion effect. Confirmed influential:
refitting with vs without it moved scale_ms by ~16% (96.6 vs 112.3) and cut
SSE by over a third. Excluded, documented, not silently dropped.

**Refit `congestion_delay_bump_ms` on the clean 195-sample set using
Theil-Sen (median of pairwise slopes) instead of OLS**, since the "low-u"
samples turned out not to be a clean baseline either (12-443ms spread even
below u=0.25) -- mean/OLS is not robust to that; Theil-Sen's median is.
Result: `scale_ms=120.174` (was 113.603), `BASELINE_DELAY_MS=34.062ms` (was
6.0, hand-picked). Spearman(u, new residual) cleaned up to 0.049 (p=0.489,
not significant, was borderline before refit) but **dCor stayed
significant (0.347, p=0.0001)** -- the underprediction past u=0.6 shrank but
did not disappear (median residual now +108.8ms for u>0.6 vs -6.6ms below).
**Important confound, not resolved:** 22 of the 23 real u>0.6 samples come
from the single s5-s14 link -- cannot yet distinguish "delay genuinely
steepens faster than u/(1-u) predicts at high u in general" from "this one
link's queueing/buffer sizing is idiosyncratic." Applied the refit anyway
(better than the alternative of keeping a fit with zero evidence past 0.55)
and widened `MAX_UTILIZATION_FOR_EXTRAPOLATION` from 0.6 to 0.75 (just under
the real ceiling of 0.782, same "clamp at the evidence boundary" principle
as before) -- but documented this residual gap rather than claiming it's
fixed. 96/96 tests and `experiments/decision_engine_check.py` both still
pass with the new curve (checked specifically for the earlier
negative-edge-weight failure mode; none observed at the new clamp boundary).

**Attempted to fit `congestion_loss_bump`'s onset/scale to real data
(31 real nonzero-loss samples from this run + 30 from
`loss_saturation_check`, all the same real s5-s14 link) -- rejected the
result, did not apply it.** Grid-searched onset over [0, 0.9] with a
closed-form scale fit at each step (both SSE/L2 and a median-based/L1
variant, to check the result wasn't an artifact of one loss function):
both converged to a near-zero onset (0.00-0.09), which does not match the
real data's own zero-loss samples well into the 0.3-0.5 utilization range on
the *same* link -- the fit is dominated by the handful of large loss values
(up to 0.65) at high u and sacrifices the low/mid-u region to accommodate
them, and even the residual after this "best" fit remains dCor-significant
(0.42, p<0.001) either way. **Conclusion: the quadratic-past-a-threshold
functional form and/or the 77 real samples (all one link) available today
are not enough to responsibly fit both onset and scale at once** --
production `congestion_loss_bump` is left unchanged
(`onset=0.7, scale=0.05`, still hand-picked, still not properly calibrated)
rather than replacing a known-uncalibrated curve with an equally
uncalibrated one under a false claim of having fit it. Revisit once more
real, cross-link saturating data exists, or with a more careful fitting
method than a plain grid search.

---

## Root-causing the leftover delay-residual dCor signal: hump diagnosis (2026-08-19)

The Theil-Sen refit above reduced but didn't eliminate `dCor(u,
delay_residual)` (0.378→0.347, still p=0.0001). Rather than keep tuning the
same parametric curve, diagnosed *why* a real signal persists, following a
structured decision path: rule out experiment-design artifacts first (time
drift, link-switch cold start, sample-to-sample hysteresis/queue carryover);
if those are clean, treat it as a genuine non-monotonic relationship and
switch to a non-parametric residual extraction method instead of continuing
to force the queueing-theory shape. Diagnostic figure (4 panels: residual by
link, residual vs execution time colored by link, residual by link-switch
status, local-median smoother showing the hump shape) published at
https://claude.ai/code/artifact/3321f210-88e7-4c3f-b345-ae29664b65f4.

**All three artifact hypotheses ruled out, using the real execution-order
log from the coverage-extension run (196 samples, reconstructed sample
index from `mininet_independence_check.py`'s own stdout):**
- Time drift (execution index vs residual): Spearman p=0.9671, dCor p=0.8945.
- Link-switch cold start (just-switched-to-this-link flag vs residual):
  Spearman p=0.9975, dCor p=0.5739.
- Same-link consecutive-sample carryover (residual[i-1] vs residual[i] for
  same-link back-to-back real executions, n=53 pairs): Spearman p=0.3269.

None significant — the hump is not an artifact of experiment timing,
per-link setup overhead, or queue/buffer carryover between samples.

**Confirmed the hump is a real, within-link phenomenon, not a cross-link
mixing artifact.** Excluding s5-s14 entirely (n=148, the other 3 links
only): dCor(u, residual) = 0.311, p=0.0002 — barely lower than the full
dataset's 0.347. By utilization tercile, each of the 3 remaining links shows
the same rise-then-fall shape individually: s1-s2 -31.5→+21.3→-45.6ms,
s13-s35 -31.1→+7.2→-13.3ms, s5-s6 -9.8→+33.2→+30.6ms (this link's u only
reaches 0.375, consistent with catching the rise but not yet the fall).
Spearman reads this as independence (only detects monotonic trends); dCor
correctly flags it as real dependence.

**Switched to LOESS (local weighted regression, tricube kernel, degree-2,
frac=0.35 — implemented in pure numpy, no scipy) to extract the residual
instead of the parametric `scale_ms * u/(1-u)` curve, per the decision
above.** Result: `dCor(u, LOESS_residual) = 0.281, p=0.0002` (down from
0.347 parametric; Pearson/Spearman both now clean, not significant) — LOESS
captures the hump shape better, as expected from a locally-adaptive method,
but a real dCor signal still remains.

**That remaining signal is heteroscedasticity (variance depends on u), not
a mean-shape defect** — checked residual spread by utilization quintile:
std=93.0 (lowest u) → 73.4 → 62.5 (lowest, mid-range) → 111.4 → **167.4
(highest u, 2.7x the mid-range minimum)**. Real network delay becoming more
variable/bursty as utilization approaches saturation is a well-known,
physically expected effect. A residual formula that prices a single scalar
"how far is delay from its expected value" is structurally about the
*conditional mean* — it cannot, by construction, remove a dependence that
lives in the *conditional variance* without a fundamentally different
design (e.g., separately modeling and pricing dispersion). This is very
likely close to the practical floor for this specific approach, not a sign
the investigation was incomplete.

**Decision: did not switch production `congestion_model.py` to LOESS.**
Discussed the tradeoff explicitly: LOESS is not a closed-form curve — it
requires retaining the full training dataset and doing a local weighted
regression at prediction time, and has no principled extrapolation behavior
in sparse regions (u>0.6, where real coverage is thin and single-link, is
exactly where this would matter most). Kept the current Theil-Sen-fit
parametric curve (`scale_ms=120.174`, `BASELINE_DELAY_MS=34.062`,
`MAX_UTILIZATION_FOR_EXTRAPOLATION=0.75`) as the production choice; this
LOESS analysis stands as a diagnostic result explaining *why* the residual
doesn't fully vanish, not as an implemented alternative.

**Breusch-Pagan test, formally confirming the heteroscedasticity (2026-08-19).**
The "variance rises 2.7x" observation above was a descriptive quintile
comparison; formalized it with a proper test. Implemented in pure numpy
(White-style: auxiliary regression of squared residuals on `[1, u, u^2]`,
LM statistic = n * R^2 of that regression, p-value via 9999-permutation
test rather than the asymptotic chi-square approximation, since exact
permutation p-values don't depend on that approximation holding at this
sample size):

```
Production (Theil-Sen) residual: LM=21.231, aux R^2=0.109, p=0.0005
LOESS residual:                  LM=12.147, aux R^2=0.062, p=0.0071
```

Both significant — heteroscedasticity is now a formally tested finding, not
just a descriptive pattern, for both the currently-deployed curve and the
LOESS alternative.

---

## New cost-formula term: zeta * delay-jitter (2026-08-19)

Rather than keep trying to remove the confirmed heteroscedasticity from
delay_residual (structurally impossible for a residual-pricing design, per
the section above), priced it directly as its own signal instead --
"a link whose delay has recently been bouncing around a lot is itself a
form of instability" fits this project's stability-aware framing directly.

**Design decisions, made explicitly before implementing:**
- **Measurement: real-time rolling-window empirical std**, not a static
  curve fit as a function of utilization. A curve (like congestion_model's
  delay/loss curves) would only express "this utilization level tends to be
  jittery on average" and inherit the same small-n/single-link confound the
  delay curve refit above never fully resolved. A rolling window over each
  link's own recent observations is the same design already used for delta
  (`LinkChurnTracker`) and directly reflects *this* link's *current* real
  behavior.
- **A genuinely new weight (zeta), not folded into delta.** Delta already
  has a specific, tested meaning: control-plane reroute activity (how often
  a link has recently been switched into/out of an installed path). This
  new signal is data-plane measurement volatility -- a link can be jittery
  without ever having been rerouted around, or vice versa. Conflating them
  would blur two empirically distinct things sharing only the word
  "instability."

**Implementation:**
- `src/monitor/delay_jitter_tracker.py` (new): `DelayJitterTracker`, same
  rolling-window pattern as `LinkChurnTracker` (timestamped samples in a
  deque, evict past `window_seconds` on read). Normalizes rolling sample std
  (ms) to [0,1] via `saturation_ms=150.0` (informed by the diagnostic's
  observed residual std range, ~60-170ms across utilization quintiles),
  returns 0.0 below `min_samples=3` (cold start -- no evidence of
  instability yet, not an artificially high default).
- `NetworkState.update_link_statistics()` now computes each sample's delay
  residual (`delay_ms - predicted_delay_ms(utilization)`, the exact
  quantity the Breusch-Pagan test above was run against) and feeds it to
  the tracker automatically -- no caller needs to call a separate recording
  method by hand on every monitoring update.
- `GraphBuilder._calculate_edge_cost` adds
  `zeta * network_state.get_delay_jitter_score(link_id, now=now)` to the
  cost sum. `zeta=0.05` (config/decision.yaml and both Python-side
  defaults) matches delta/epsilon's magnitude as a starting point -- **not
  yet tuned**; weights now sum to 1.05, not 1.0.  `weight_search_comparison.py`
  needs to be rerun with this 6th dimension (already on the open task list
  for other reasons).

**A real bug found and fixed while wiring this up, same shape as the
2026-08-12 churn `now`-facade bug, mirror-imaged:** `update_link_statistics`
initially always recorded jitter samples against real wall-clock time
(`link_stats.timestamp`), while `_calculate_edge_cost` reads jitter through
whatever `now` a caller threads in -- a small synthetic `now_s` in every
offline scenario experiment. Recording-real/reading-synthetic (rather than
the churn bug's recording-synthetic/reading-real) doesn't make the signal
always 0 -- it makes the eviction cutoff always smaller than every real
timestamp, so **the window never rolls at all**: samples accumulate for the
entire experiment run instead of reflecting the last `window_seconds`.
Confirmed live (120 samples recorded across a simulated long run all stayed
in the window at a synthetic `now=1000.0`, none evicted). Fixed by threading
an explicit `now` parameter through `update_link_statistics()` and
`experiments/simulation_common.py`'s `set_link_condition()`, and passing
each caller's `now_s` at all 20 real call sites across
`congestion.py`/`increasing_load.py`/`stale_stats.py`/
`sensitivity_analysis.py`/`decision_churn_independence.py`/
`joint_independence_matrix.py`/`failure_recovery.py`. Verified fixed:
recording+reading under a consistent synthetic clock now shows real
dispersion (score 0.31 for a 3-sample burst) and correctly evicts 100
synthetic seconds later (score back to 0.0).

11 new tests (`tests/delay_jitter_tracker.py`, 2 more in
`tests/network_state.py`), 107/107 passing. Verified `decision_engine_check.py`,
`increasing_load.py`, `joint_independence_matrix.py`, and
`decision_churn_independence.py` all still run end-to-end with consistent
results (u-churn/churn-reliability findings unchanged from before this
change).

**Post-switch settle window: keeping delta and zeta from double-counting
the same reroute event (2026-08-19, user-proposed).** A switch itself can
cause a real, transient delay blip (buffer flush, brief burst) that has
nothing to do with steady-state jitter -- recording it would let delta
(control-plane churn) and zeta (data-plane jitter) both react to the same
underlying event instead of measuring two genuinely distinct things.
Considered reusing `config/decision.yaml`'s `hold_down.duration_seconds`
(10s, the closest existing "how long until things settle after a switch"
concept) directly, but it's scoped per src-dst *flow pair* inside
`DecisionEngine`/`StabilityManager`, not per *link* -- and `NetworkState`
(monitor layer) shouldn't reach up into the decision layer's mutable state
to read it. Instead added `LinkChurnTracker.has_changed_recently(link_id,
within_seconds, now)` -- a non-mutating peek at that link's own most
recently recorded churn timestamp (deliberately not reusing
`get_churn_score`'s eviction side effects) -- and a new
`NetworkState.jitter_settle_window_seconds` (default 10.0, same magnitude
as hold_down, not literally coupled to it). `update_link_statistics` now
skips feeding the jitter tracker entirely while a link is within that
window of its own last churn event. 5 new tests (4 in
`tests/link_churn_tracker.py`, 1 integration test in `tests/network_state.py`
proving samples during the window are dropped and samples after it are
kept), 112/112 passing.

---

## ERRATUM: campaigns sharing a real link contaminated the u-churn finding; bug fixed, result corrected (2026-08-19)

While preparing real (u, delay_residual, loss_residual, churn) data to fit a
PCA composite congestion indicator (see next section), a PCA on
`joint_independence_matrix.py`'s data came back with a near-zero
loss_residual loading -- investigating why surfaced a real, previously
undetected bug affecting **every prior run of both
`decision_churn_independence.py` and `joint_independence_matrix.py`**.

**The bug:** `select_test_pairs(limit=NUM_PAIRS, ...)` picks src-dst pairs
without any notion of whether their first hop links overlap. Confirmed
live: with the default 6 pairs, link "0-4" was the first hop for **4
different campaigns** (`0->11`, `0->13`, `0->14`, `0->12`) simultaneously.
Since every campaign writes to the *same* `NetworkState` and all campaigns'
events are interleaved in one globally shuffled order, two campaigns
sharing a link silently overwrite each other's `set_link_condition()` calls
before either reads back its own -- the recorded (utilization, delay,
churn) tuples for a shared link reflect whichever campaign wrote most
recently, not a clean causal chain from any single campaign's own actions.

**Fix:** both scripts now over-fetch candidates (`NUM_PAIRS * 8`) and
de-duplicate by first-hop link before accepting a campaign, so all selected
campaigns' first links are guaranteed disjoint.

**Result changed materially -- correcting a finding cited multiple times
earlier in this document and in memory:**

```
Before fix (contaminated):  rho(u, churn_score) = 0.480, p=0.0001  -- reported as "significant"
After fix:                  rho(u, churn_score) = 0.163, p=0.1705  -- NOT significant
                             dCor(u, churn_score) = 0.340, p=0.0044 -- still significant
```

**The strong monotonic u-churn relationship reported earlier was
substantially a bug artifact and should be walked back.** What survives:
a weaker, non-monotonic dependency (dCor significant, Spearman not) --
plausible given DecisionEngine's threshold-crossing reroute logic is a
discrete/step decision, not a smooth function of utilization, so a
non-monotonic relationship between u and how often a link gets churned is
physically sensible.

**What did NOT change** (re-verified on the corrected data, both scripts
consistent): **churn_score vs reliability_down remains not significant**
(rho=0.133, p=0.243 in the corrected run vs 0.037, p=0.747 before) -- the
core delta-epsilon independence conclusion this session repeatedly leaned
on survives the fix intact. Also note n dropped slightly (75 samples/5
campaigns instead of 90/6) since one candidate pair had to be skipped after
its first-hop link collided with an already-accepted campaign's.

112/112 tests still passing (the fix only touched candidate-selection logic
in the two experiment scripts, no production code).

---

## Composite congestion indicator: PCA on u/delay_residual/loss_residual/churn -- tried, and reverted (2026-08-19)

Per explicit direction: rather than keep utilization/delay_residual/
loss_residual/churn as 4 separate weighted terms (a VIF analysis earlier
this session found them jointly near-collinear -- not independent
information, different facets of the same congestion event), attempted to
merge them into one composite via PCA. **Implemented, tested end to end,
then reverted the same day** after the composite was found to invert two
of this project's core cost-formula invariants. Recorded in full since the
dead ends and the reversal are themselves the substantive finding here, not
a footnote.

**Data problem #1: no dataset had all 4 variables with real, non-degenerate
variance together.** Real Mininet traffic (`results/independence_check/`,
`results/loss_saturation_check/`) has real u/delay/loss residual structure
but no DecisionEngine running -- no churn. The DecisionEngine-driven offline
experiments (`decision_churn_independence.py` / `joint_independence_matrix.py`)
have real churn but generate delay/loss directly from `congestion_model`'s
own curve via `set_link_condition`'s default path -- their residuals are
~0 by construction (confirmed: loss_residual's std was 0.00006 in that
data), giving PCA no real variance to fit against (a first attempt produced
a near-zero, meaningless loss_residual loading).

**Data problem #2, found while investigating #1: a real, previously
undetected bug.** `select_test_pairs()` doesn't check whether campaigns'
first-hop links overlap -- confirmed live, link "0-4" was the first hop for
4 different campaigns simultaneously. Since all campaigns write to one
shared `NetworkState` with events interleaved in one global shuffled order,
sharing a link lets one campaign silently overwrite another's just-set
state before either reads it back. This also **corrected a finding cited
multiple times earlier in this document**: `rho(u, churn_score)` dropped
from 0.480 (p=0.0001, reported as significant) to 0.163 (p=0.17, not
significant) once fixed -- see the ERRATUM section above for the full
before/after and what survived (churn-reliability independence, unaffected).
Fixed in both experiment scripts by over-fetching candidates and
de-duplicating by first-hop link.

**Bridged the two data sources:** built
`experiments/hybrid_congestion_churn_matrix.py` -- real DecisionEngine-driven
churn campaigns (with the disjoint-link fix), but delay/loss injected via
`set_link_condition`'s new `delay_ms_override`/`loss_override` params using
the real Mininet sample whose achieved_utilization is closest to the
target, instead of the formula. Two more data-quality issues surfaced and
were fixed along the way: (a) down-link samples carry a stale, frozen delay
value from before the failure (no traffic flowing, nothing real to
measure) -- excluding them fixed a spurious `delay_residual` vs
`reliability_down` correlation that had hit rho=0.872 (VIF>3000); (b) the
original script used fixed congest/relieve utilization targets
(0.85/0.3), which made the nearest-neighbor lookup return the *same* real
sample every time, collapsing delay_residual/loss_residual to ~2 distinct
points (a trivial "line through 2 points" gives -1.000 correlation between
them) -- fixed by sampling target utilization from a range each event
instead of a fixed constant.

**PCA fit on the resulting clean data (n=28, 5 campaigns, down-link samples
excluded -- small; a first pass):**
```
PC1 explains 43.6% of variance (4-variable version: u, delay_residual, loss_residual, churn_score)
Loadings: utilization +0.397, delay_residual -0.711, loss_residual -0.307, churn -0.493
```

**Implemented in production** (`congestion_model.congestion_index()`,
`GraphBuilder`'s kappa term replacing alpha/beta/gamma/delta,
`LossJitterTracker`/eta added alongside zeta for loss's heteroscedasticity,
following the same Breusch-Pagan-confirmed reasoning as delay's) and
verified mechanically sound (VIF 1.3-2.9 in the final 3-variable version,
no more degenerate values, 96/96 unrelated tests untouched).

**Then reverted, after concrete behavioral checks:**
- `test_churned_link_costs_more_than_an_identical_untouched_link` failed:
  recording 2 churn events dropped a link's cost from 0.480 to 0.001 (the
  MIN_EDGE_COST floor) -- churn's negative PC1 loading meant **more churn
  made a link look cheaper**, the exact opposite of what this term exists
  to do. User-confirmed this directly conflicts with the stability-aware
  framework's purpose; agreed to extract churn back out as an independent
  delta term rather than accept the sign as PCA found it.
- Refit PCA on the remaining 3 variables (u, delay_residual, loss_residual)
  -- **delay_residual's loading was also negative** (-0.763, the largest
  magnitude of the three). Verified concretely: `composite(u=0.5,
  delay_residual=0)` = -0.084, `composite(u=0.5, delay_residual=+300ms)` =
  -1.671 -- a link with real delay running 300ms worse than its utilization
  predicts scores as *more* attractive, directly inverting the exact
  invariant beta's residual design was built to guarantee (the same one
  `test_link_with_anomalous_delay_beyond_prediction_costs_more` tests).
- Presented this second inversion; decided to abandon the composite
  entirely rather than extract variables one at a time as each sign
  conflict surfaced -- PCA's variance-maximizing objective has no reason to
  align with "worse measured congestion should cost more," and two
  invariant violations out of three non-utilization inputs suggested this
  wasn't a one-off.

**Reverted to the pre-PCA formula** (`git checkout` of
`graph_builder.py`/`path_cost.py`/`congestion_model.py`/`config/decision.yaml`
back to the last clean commit, `eta`/loss-jitter re-added on top since that
change doesn't share the composite's problem -- it's a variance-based term,
always non-negative, no sign to invert). Current formula:
`alpha*u + beta*delay_residual + gamma*loss_residual + delta*churn +
epsilon*reliability + zeta*delay_jitter + eta*loss_jitter`, 7 separate
weighted terms, none merged. `experiments/hybrid_congestion_churn_matrix.py`
and the campaign disjoint-link fix are kept -- both are real, standalone
value (a genuine bug fix; a reusable real+real data-bridging tool) even
though the composite they were built to support wasn't adopted.
`congestion_model.congestion_index()` was removed (dead code once
GraphBuilder no longer calls it).

**Takeaway for anyone revisiting this:** VIF correctly identified real
collinearity among these variables, but "jointly collinear" and "safe to
compress into one variance-maximizing axis" are different claims -- the
latter also needs the compressed axis to preserve whatever monotonic
relationships downstream logic (and its tests) depend on, which PCA's
objective does not guarantee and did not deliver here for 2 of 3 tested
non-utilization inputs.

---

## Testing zeta/eta independence: a false-positive coupling found and refuted (2026-08-19)

Neither `delay_jitter_score` nor `loss_jitter_score` had ever been recorded
by any experiment before -- zero data existed on whether zeta and eta are
independent of each other or of delta. Two passes, in order.

**Pass 1: extended `hybrid_congestion_churn_matrix.py` to also record both
jitter scores.** First run (n=28, the same 5-campaign scale used for the
PCA work) found `delay_jitter_score` vs `loss_jitter_score` at Spearman
rho=1.000* -- too clean to trust. Investigated directly: only 28 of 75 rows
cleared the rolling-window cold-start threshold, and among those, only
~6 distinct value pairs actually occurred (the 60s window often doesn't
change between closely-spaced reads) -- an effective n far below 28.
**Scaled up to the topology's real maximum (14 disjoint-first-link
campaigns, verified live only 14 of 200 candidates qualify) x 60 events**
(213 usable samples): the correlation held up as real at this scale
(Spearman=0.800*, dCor=0.697*, p<0.05 under a 4999-permutation test) -- not
a small-n artifact. Also newly significant at this scale: churn_score vs
both jitter scores (Spearman -0.31*/-0.34*, dCor 0.30*/0.40*), not seen at
n=28.

**But the 0.80 correlation was still suspect for a structural reason
independent of sample size:** `nearest_real_sample()` picks delay AND loss
from the *same* real Mininet sample every time it's called, so within any
rolling window, delay_jitter and loss_jitter are mechanically driven by the
same "which real samples got picked" latent factor -- not two genuinely
separate measurements. More data fixes small-n noise; it cannot fix a
shared-generation-mechanism confound.

**Pass 2: built `scripts/mininet_jitter_check.py` for real, independently-
measured delay and loss** -- real ping-based delay, real tc-qdisc-based
loss (not from a shared lookup), each fed through the actual production
`NetworkState.update_link_statistics()` / `DelayJitterTracker`/
`LossJitterTracker` code path in real time on the real s5-s14 link, no
synthetic clock, no injection. **Result (55 real samples, 51 with both
jitter scores past cold-start): Spearman=-0.2132, p=0.1322 (not
significant); dCor=0.1944, p=0.4278 (not significant).**

**Conclusion: the 0.80 correlation was the shared-lookup artifact, not a
real property of delay/loss jitter.** Under genuine independent
measurement, delay_jitter_score and loss_jitter_score show no evidence of
dependence -- supporting the original design decision to keep eta separate
from zeta rather than merge them. (The churn-jitter correlation found in
pass 1 was not re-tested under independent measurement -- `mininet_jitter_check.py`
has no DecisionEngine running, so churn stays 0 throughout; that finding's
status is still open.)

---

## u-churn residualization: tried both OLS and LOESS, neither works (2026-08-19)

Considered extending the residual-pricing pattern (already used for
delay/loss) to delta: instead of pricing raw `churn_score`, price
`churn_score - predicted(churn_score | u)`, to remove the weak leftover
u-churn dependency found earlier (dCor=0.340, p=0.0044 in
`decision_churn_independence.py`'s post-bugfix data).

**A conceptual caveat flagged before testing:** unlike delay/loss (real,
exogenous physical measurements independent of this project's own
decisions), `churn_score` is *produced by* the decision engine this cost
formula drives. Any `predicted(churn|u)` curve fit from data generated
under the current delta weight reflects that specific policy's behavior --
changing delta would change future churn generation, so the fitted curve
is not guaranteed to stay valid the way `predicted_delay_ms(u)`'s physical
queueing relationship does. A real, if likely second-order, circularity
risk that delay/loss residualization doesn't have.

**Tested anyway, on the dataset where the dependency is actually present
(n=75):**
```
Before:            Spearman=0.163(p=0.17)  dCor=0.3398(p=0.0044)
After OLS residual: Spearman=0.029(p=0.81)  dCor=0.3441(p=0.0038)  -- dCor unchanged
After LOESS residual: Spearman=0.152(p=0.19) dCor=0.3532(p=0.0025) -- dCor slightly worse
```
Neither method touches dCor at all -- OLS kills the (weak) linear
component (Spearman drops sharply) but the dependence itself is
non-monotonic, and LOESS -- normally effective on non-monotonic structure
(see the delay-residual hump-diagnosis section) -- doesn't help here
either. Plausible reason: `churn_score` is a discrete, threshold-gated
count (crosses a utilization threshold, then subject to hysteresis/
persistence/hold-down/change-budget gates before a reroute fires), not a
continuous physical process the way queueing delay is -- there may not be
a smooth curve of any shape for a curve-fitter to find.

Cross-checked on a second, larger dataset
(`hybrid_congestion_churn_matrix.py`, n=213): the baseline u-churn
dependency wasn't even significant there (dCor=0.1182, p=0.1681) --
consistent with this being a modest, somewhat unstable-across-samples
effect to begin with.

**Decision: do not implement.** Two independent reasons converge: the
circularity risk (untested but real), and the empirical fact that neither
tested method removes any of the dependency on the data where it's
actually present. Delta stays priced on raw `churn_score`, unresidualized.

---

## churn vs zeta/eta under real independent measurement: the offline coupling was fake too (2026-08-19)

Closes the item left open earlier: `hybrid_congestion_churn_matrix.py`
(n=213) found churn_score significantly correlated with both jitter scores
(dCor 0.30-0.40) -- but that experiment's delay/loss come from the same
nearest-neighbor lookup mechanism already shown (in the zeta/eta pass
above) to manufacture correlations that don't survive independent
measurement. Churn itself in that experiment is also DecisionEngine-driven
but shares the same `NetworkState`/globally-shuffled-event machinery as
the disjoint-link bug -- reason enough to distrust this finding without a
real check.

**Built `scripts/mininet_churn_jitter_check.py`**: real ping-based delay +
real qdisc-based loss (same as `mininet_jitter_check.py`), plus real churn
events recorded via the actual production call
(`NetworkState.record_link_churn()`) at times drawn from an independently-
randomized schedule (~40% chance per sample, using a separate RNG stream
from the traffic schedule) -- not from a real closed-loop DecisionEngine.
**Scope decision, stated explicitly:** building a full real routing/
decision stack (real candidate paths, real FlowInstaller, real threshold/
hysteresis/hold-down gating) was judged unnecessary for this specific
question -- what matters for testing "does a churn event's aftermath
correlate with subsequent real jitter" is that churn timing is independent
of the traffic pattern being measured, which an independently-randomized
schedule satisfies as directly as a real DecisionEngine would (which also
churns based on utilization crossings, not on delay/loss dispersion).

**Result (56 real samples, 19 independent churn events):**
```
churn_score vs delay_jitter_score: Spearman=-0.0736 (p=0.5817), dCor=0.1389 (p=0.7182)
churn_score vs loss_jitter_score:  Spearman=-0.0362 (p=0.7841), dCor=0.1610 (p=0.4961)
```
Both not significant. **Same pattern as the zeta/eta result: the offline
hybrid experiment's significant correlation did not survive independent
real measurement.**

**Meta-finding, worth stating plainly for the dissertation:** every
correlation this session found via the offline injection-based hybrid
framework, when re-tested under genuinely independent real measurement,
turned out not to be real (delay_jitter-loss_jitter first, now
churn-jitter). The framework has a structural bias toward manufacturing
spurious correlations, from sharing generation mechanisms (nearest-
neighbor lookup, shared `NetworkState` across campaigns) rather than
measuring independently -- any dependency finding produced by it should be
treated as a hypothesis to verify with real, independent measurement, not
a conclusion on its own.

**Current status of all delta/zeta/eta pairwise relationships:**
- delta vs epsilon: independent (confirmed, multiple real/offline reruns)
- zeta vs eta: independent (confirmed under real independent measurement)
- delta vs zeta, delta vs eta: independent (confirmed under real
  independent measurement, this section)
- delta vs u: weak non-monotonic residual dependence (dCor~0.34 where
  present, but not consistently significant across datasets); attempted
  residualization, rejected as ineffective (see previous section) --
  left unaddressed, priced as raw churn_score

---

## delay_residual vs loss_residual: a real dependency this time, not an offline artifact (2026-08-19)

Unlike the two false-positive couplings found this session (delay_jitter-
loss_jitter, churn-jitter -- both traced to injection-mechanism artifacts
in the offline hybrid framework), this one didn't need a new live Mininet
run to test properly: `results/independence_check/` and
`results/loss_saturation_check/` already have real delay (ping) and real
loss (tc-qdisc) measured via **separate mechanisms** on the same real
congestion events -- not from a shared lookup, so not vulnerable to that
specific artifact.

**Result (n=221, real samples, the one confirmed-influential outlier
excluded):**
```
delay_residual vs loss_residual: Spearman=0.4669 (p=0.0001), dCor=0.6442 (p=0.0001)
```
Strong and significant. Physically sensible: when a link's real congestion
exceeds what utilization alone predicts, that excess plausibly shows up as
both extra delay *and* extra loss simultaneously -- the same underlying
anomaly, not two independent draws. **A real, previously unaddressed
overlap between beta and gamma's terms** -- structurally the same kind of
double-counting concern that motivated the original residual-pricing
redesign, just between the two residuals themselves rather than between
utilization and each residual.

**Attempted the same residualize-then-diagnose pipeline used throughout
this session:**
- OLS (`loss_residual = a + b*delay_residual`): slope b=+0.000874,
  correctly signed (worse delay -> worse loss). Residualizing brought
  dCor down to 0.5396 -- only a ~16% reduction, and Spearman's sign
  flipped (0.467 -> -0.296), signaling the true relationship isn't simply
  linear.
- LOESS: dCor=0.5626 -- essentially the same as OLS, not meaningfully
  better.
- Breusch-Pagan on the OLS-residualized remainder: **LM=26.983, p=0.0017,
  significant** -- heteroscedasticity again. Residual std by
  delay_residual tercile: 0.059 / 0.033 / 0.207 -- the top tercile's
  variance is ~6x the middle's.

**Decision: do not implement in production.** The 16% reduction is real
and correctly-signed, but implementing it would make `loss_residual`
depend on `delay_residual` (a new ordering/coupling between beta and
gamma's inputs, two more fitted constants to maintain) for a partial fix
that leaves the larger, heteroscedastic share of the overlap untouched
regardless. Consistent with this session's standing rule (see the loss
curve fit and u-churn residualization rejections): a real but modest,
incomplete effect isn't enough on its own to justify added formula
complexity. Recorded as a known, quantified, real (not artifactual)
overlap between beta and gamma -- unlike delta-u, this one did NOT require
a new live experiment to confirm, since independently-measured real data
already existed for it.

---

## u-churn re-characterized: a real variance effect, not a mean/threshold effect (2026-08-19)

User-proposed reframing: `decision_churn_independence.py`'s `utilization`
column turned out to only take **4 near-identical distinct values across
75 samples** (0.2474/0.2642/0.3/0.85 -- effectively 2 experimental
conditions: baseline ~0.3, congested 0.85). Treating this as a continuous
variable for Spearman/dCor (as done throughout this session) is a
conceptual mismatch -- it's really a two-group comparison. Reframed
accordingly:

**Point-biserial correlation + Mann-Whitney U (location/mean tests, real
permutation p-values, n=43 congested / 32 baseline):**
```
Point-biserial r=0.0995, p=0.4177 (mean-difference permutation test)
Mann-Whitney U=782.5, p=0.2917
```
Both **not significant** -- there is no real difference in *average*
churn_score between the two utilization states.

**But the two groups' churn_score spread is very different:**
```
congested (u=0.85): std=0.369
baseline  (u~0.3):  std=0.223
variance ratio = 2.71, permutation p=0.0001 -- highly significant
```

**This resolves an open question from earlier the same day:** neither OLS
nor LOESS residualization touched the dCor=0.3398 finding at all (0.34 ->
0.34-0.35 regardless of method) -- at the time attributed to churn's
discrete, threshold-gated nature. The real reason is now clear: **there
was never a mean-level relationship to residualize in the first place.**
The entire dCor signal is a variance effect -- congested links show real,
substantially more variable churn outcomes (plausibly because whether a
specific link gets rerouted under congestion depends on which alternate
paths exist, hysteresis timing, and which other links are also congested,
while at baseline utilization there's rarely any reason to reroute at all,
so churn stays consistently near-zero) -- and mean-targeting curve-fitting
methods are structurally blind to a pure variance effect, the same
limitation already established for delay_residual and loss_residual's own
heteroscedasticity.

**Not implemented as a new production signal.** Consistent with this
session's established threshold: real and well-characterized, but pricing
"churn's own dispersion under congestion" would be a variance-of-an-
already-aggregated-rolling-statistic (churn_score is itself a rolling-
window count), a level of nesting this project hasn't found justified
elsewhere. Also only confirmed at 2 experimenter-chosen utilization levels
(0.3 vs 0.85) -- generalizing to intermediate real utilization values is
untested. Recorded as the correct, final characterization of u-churn's
dependency, superseding the earlier "weak non-monotonic residual
dependence" framing.

---

## delay_residual-gamma double-counting, quantified (2026-08-19)

Sharper version of "why we accept this" for the beta-gamma overlap found
earlier: `R² = 0.4086` -- **41% of loss_residual's variance is linearly
predictable from delay_residual alone.** Using the real config weights
(beta=0.3, gamma=0.2) and the fitted slope (loss_residual ≈ 0.0496 +
0.000874*delay_residual): for a real `+X`ms delay anomaly, beta prices
`0.0003*X` directly, and gamma's term *also* moves by `0.000175*X` purely
because of the correlation -- **a 0.58x ratio: for every unit beta prices
from a real anomaly, gamma adds another ~0.58 units driven by the same
underlying event, not a genuinely independent loss signal.**

This is a real, non-trivial amount of double-counting -- smaller than the
~1.7x overstatement that motivated the original alpha/beta/gamma residual
redesign, but not negligible. Stated plainly rather than left as a vague
"acceptable overlap": beta and gamma jointly overweight a real anomaly's
cost by roughly this much relative to two genuinely independent signals,
and the reason this isn't fixed is the cost/benefit finding from the
previous section (16% dCor reduction, heteroscedastic remainder, added
formula complexity) -- not that the overlap is small enough to ignore.

---

## Methodological confidence audit: which findings were only ever offline-framework-supported (2026-08-19)

User-requested: given the offline injection-based hybrid framework
produced 2 confirmed-fake correlations this session (delay_jitter-
loss_jitter, churn-jitter) and 1 confirmed-real one (delay_residual-
loss_residual, verified on independently-measured real data), every OTHER
finding that has *only* ever come from that framework -- never
independently verified -- needs an explicit confidence tier rather than
being cited as settled.

**Tier 1 -- real, independently measured, high confidence:**
u-delay, u-loss (raw), u-delay_residual, u-loss_residual,
achieved_utilization-loss (non-monotonic), delay_residual-loss_residual,
delay_jitter-loss_jitter (independent), u-delay_jitter (independent),
u-loss_jitter (independent), churn-delay_jitter (independent),
churn-loss_jitter (independent), u-churn's TRUE characterization (variance
effect, this section).

**Tier 2 -- real DecisionEngine behavior, offline-only, but not the
shared-lookup-artifact mechanism (u and failure status are experimenter-
set conditions triggering a real DecisionEngine, not values pulled from a
shared real-sample lookup the way delay/loss injection was) -- moderate
confidence, never run live:**
- **churn-reliability independence**: consistent across every offline
  rerun (pre- and post- the disjoint-link bugfix), but never tested with a
  real DecisionEngine reacting to real Mininet failures. Downgrade from
  "confirmed" to "consistently observed offline, not live-verified."
- **u-churn's variance effect** (this section): real, but only tested at 2
  experimenter-chosen utilization levels (0.3, 0.85) — not a natural
  continuous real distribution. Downgrade to "confirmed at these 2 levels,
  generalization untested."

**Tier 3 -- weakest support, explicitly downgrade to "hypothesis, not a
finding":**
- **delay vs churn, loss vs churn** (`joint_independence_matrix.py`'s
  original dCor≈0.47-0.49 and 0.23-0.24): delay/loss in that experiment
  are generated *from* utilization by `congestion_model`'s formula, not
  measured — doubly synthetic (generated delay/loss, offline churn). Never
  re-verified after the disjoint-first-link bugfix (only u-churn and
  churn-reliability specifically were re-run post-fix) — could still
  reflect the same contamination. **Should not be cited without a rerun.**
- **The joint VIF numbers** (`utilization`/`delay_ms`/`loss`/`churn_score`/
  `reliability_down` from `joint_independence_matrix.py`): computed on a
  mix of synthetic (by-construction) delay/loss and real churn — the
  churn_score/reliability_down VIF values (~2.0/~1.2, "carry independent
  information") are the most defensible part (churn is real), but the
  overall joint statistic mixes confidence levels and shouldn't be quoted
  as a single clean number without that caveat.

**How to apply going forward:** Tier 3 items need a real rerun (at minimum,
the disjoint-link fix's rerun that was never done for these two pairs
specifically) before being cited as conclusions in the dissertation write-
up; Tier 2 items are reasonably trustworthy but should be captioned "not
live-verified"; Tier 1 items can be cited directly.

---

## Independence-check workstream: final decisions, variable table, and closing VIF check (2026-08-19)

Two remaining design decisions, resolved:

**delta stays priced on raw `churn_score`, no discretization/state-conditioning added.** delta never applied a u-dependent transform to begin with (unlike beta/gamma, which subtract a u-predicted curve) -- the variance-effect finding confirms this design was already correct, not that it needs a fix. There's no mean-level u-churn relationship to residualize (confirmed via point-biserial/Mann-Whitney), and modeling the variance effect would require conditioning delta's weight or computation on u's discrete state -- exactly the kind of complexity this session has consistently declined to add for real-but-modest effects.

**The beta-gamma overlap (delay_residual vs loss_residual, R²=0.4086, ~0.58x shared contribution) stays unaddressed, not split into a shared-disturbance term.** This was already tested directly (OLS + LOESS residualization, both examined two sections above) -- 16% dCor reduction, heteroscedastic remainder, and a new ordering dependency between beta/gamma's inputs for an incomplete fix. Not a new open question; the earlier decision stands.

### Final variable table

| Weight | Final definition | Independence status | Confidence |
|---|---|---|---|
| alpha | utilization (real OVS-measured) | Real overlap with beta/gamma (residual-priced, not eliminated) | Tier 1 |
| beta | delay_residual = delay - predicted_delay_ms(u) | Independent of u in mean (heteroscedastic in variance, priced separately by zeta); real 0.58x overlap with gamma (quantified, accepted) | Tier 1 |
| gamma | loss_residual = loss - predicted_loss(u) | Independent of u in mean (heteroscedastic in variance, priced separately by eta) | Tier 1 |
| delta | churn_score (raw, unmodified) | Independent of u in mean (point-biserial/Mann-Whitney not significant); real variance effect only (2.71x ratio, p=0.0001); independent of epsilon/zeta/eta (real measurement) | Tier 1 (vs epsilon/zeta/eta); Tier 2 (u-relationship, only tested at 2 experimenter-set utilization levels) |
| epsilon | reliability (up/down binary) | Independent of everything else confirmed | Tier 1 |
| zeta | delay_jitter (rolling-window std, settle-window excluded) | Independent of u, churn, eta (all real independent measurement) | Tier 1 |
| eta | loss_jitter (rolling-window std, settle-window excluded) | Independent of u, churn, zeta (all real independent measurement) | Tier 1 |

No composite terms, no split/shared-factor terms. Final formula unchanged
from current production code:
```
Cost = alpha*u + beta*delay_residual + gamma*loss_residual + delta*churn
     + epsilon*reliability + zeta*delay_jitter + eta*loss_jitter
```

### Closing VIF check: a "partial but clean" scope, not the full 7-variable joint test

A genuine 7-variable joint VIF would need one dataset with all 7 real
together, which doesn't exist (established repeatedly this session). But a
full joint test isn't actually needed: zeta/eta have already been shown
pairwise-independent of u, delta, and each other under real, independent
measurement -- re-testing that inside a joint VIF would be redundant, and
zeta/eta's relationship to delay_residual/loss_residual is definitional
(zeta *is* the rolling variance of delay_residual's own samples), not the
kind of incidental collinearity VIF is meant to catch.

What remains genuinely open to joint collinearity is exactly the 4
variables with real, non-definitional relationships found this session:
utilization, delay_residual, loss_residual, churn_score. These 4 are all
present together in `hybrid_congestion_churn_matrix.py`'s data (n=213,
post disjoint-link-bugfix) -- and unlike delay_jitter/loss_jitter/churn's
known artifact in that same dataset (shared nearest-neighbor lookup
mechanically coupling delay/loss; not applicable to churn, which is
generated by the real DecisionEngine, a separate causal process not
sharing that lookup), this specific 4-variable subset isn't contaminated
by either known artifact mechanism.

```
VIF(utilization)    = 2.055
VIF(delay_residual) = 2.293
VIF(loss_residual)  = 2.836  -- highest, consistent with its real 0.58x overlap with delay_residual
VIF(churn_score)    = 1.020  -- near 1, consistent with no real mean-level u relationship
```

All comfortably under the conventional 5-10 concern threshold -- the known
real overlaps (beta-gamma) are real but not severe multicollinearity, and
the values line up with everything else found this session (loss_residual
highest given its documented overlap, churn_score near 1 given its
confirmed lack of a mean relationship with u).

**Independence-check workstream closed.** Next phase: weight optimization
(grid search / Bayesian optimization / simulated annealing) --
`weight_search_comparison.py` needs updating from its original 5-weight
shape (alpha/beta/gamma/delta/epsilon) to the current 7-weight one before
its comparison is meaningful again.

## Closing out the remaining deferred items (2026-08-19)

Four items had been left open across earlier sessions ("fix this later").
All four addressed in one pass:

### 1. `offered_load_utilization` wired into the remaining scenario scripts

Previously wired into `increasing_load.py` only (2026-08-18). Now also
wired into `congestion.py`, `failure_recovery.py`, `stale_stats.py`, and
`priority_policy.py` -- every offline scenario experiment now accounts for
a flow's own future bandwidth demand when "proposed" evaluates a candidate
path, not just the flow-ramp scenario.

- `congestion.py`, `failure_recovery.py`, `stale_stats.py`: same pattern as
  `increasing_load.py` -- find `flow-video-1` (the flow actually mapped
  onto `PRIMARY_PAIR`) among the scenario's flows, resolve its Mbps demand
  into a utilization fraction via `resolve_offered_load_utilization()`, set
  it on `drivers["proposed"].offered_load_utilization` after the driver is
  constructed (has to happen after, since the hotspot link isn't known
  until the driver's initial path is computed). Confirmed live: hotspot
  link resolves to `2-32`, `flow-video-1`'s 24Mbps resolves to a real
  0.16 offered-load fraction on that link (matches the 0.16 already cited
  in `increasing_load.py`'s own wiring, since it's the same physical link
  and flow).
- `priority_policy.py` is structurally different (4 traffic classes sharing
  one physical path, each through its own `DecisionEngine`, no
  `ProposedDriver`/`make_drivers`) -- wired directly into
  `DecisionEngine.evaluate_service_congestion(offered_load_utilization=...)`,
  computed per class from that class's own flow, since each class's engine
  should account for its own demand, not a shared one.
- All 5 scenario scripts smoke-tested end to end (fresh CSV output, no
  exceptions) and the full 123-test suite still passes.

### 2. Tier 3 rerun: delay-churn / loss-churn under the disjoint-link bugfix

`joint_independence_matrix.py` already carries the disjoint-first-hop fix
(the campaign-link-sharing bug found and fixed earlier this session --
see item 8k below). Rerunning it (75 samples, 5 disjoint pairs x 15
events) gives:

```
delay_ms vs churn_score:  rho=0.170  (not significant)
loss vs churn_score:      rho=0.094  (not significant)
utilization vs churn_score: rho=0.163 (not significant -- matches the
                                        already-corrected value)
churn_score vs reliability_down: rho=0.133 (not significant)
```

**These numbers do not replicate the originally-cited findings** (delay-churn
rho=0.486 significant, loss-churn rho=0.247 significant) from before the
disjoint-link bugfix. Same pattern as every other post-bugfix rerun this
session: the pre-fix "significant" correlations were an artifact of
campaigns sharing a first-hop link and corrupting each other's `NetworkState`
via globally-interleaved events, not real dependencies. **Tier 3 items are
now downgraded from "hypothesis" to "checked and refuted"** -- delay/loss
and churn_score are independent as far as this offline framework can show.

### 3. Stale experiments re-run under the current (7-weight, offered-load-fixed) formula

`baseline_comparison.py`, `sensitivity_analysis.py`, and `pilot_experiments.py`
all predate this session's residual-pricing fix, the delay curve refit(s),
delta's churn-visibility fix, the offered-load fix, and the new zeta/eta
jitter terms. All three re-ran cleanly (all under 2 seconds each, all
`"status": "ok"`) and wrote fresh output under `results/`. No code changes
needed -- these scripts already call the current formula through
`GraphBuilder`/`PathCost`; they just hadn't been re-executed since.

### 4. `congestion_loss_bump` onset/scale: re-attempted with more real data, still rejected -- now with a sharper reason why

The original 2026-08-12 rejection was "not enough real cross-link
saturating data." Real data has grown since (`results/independence_check/`
now has 196 samples across 4 links including the saturating s5-s14 link;
`results/loss_saturation_check/` has 30 more, same s5-s14 link). Combined
same-link data: 77 samples, u in [0.134, 0.827], 51 with nonzero loss.

Re-ran the same SSE grid-search fit as before, now on this larger,
single-link (no cross-link confound) dataset. Result: **the SSE-optimal
onset pins at the edge of the search grid** (0.05, the lower bound tried)
regardless of how far the grid is widened -- a signature of a misspecified
functional form, not just bad parameters. Plotting the raw (u, loss) pairs
sorted by u shows why: loss is exactly 0 for every sample below u=0.42, then
becomes **bimodal** from u~0.43 to u~0.68 (individual samples land at
either ~0.0 or ~0.2-0.65 for very similar utilization values -- not a smooth
ramp), then **trends back down toward 0** above u~0.68, including two exact
zeros at the two highest utilization values observed (0.782, 0.808). This
is not an onset-quadratic curve; it's non-monotonic.

This matches a mechanism already documented earlier this session (item 5's
dCor follow-up): `achieved_utilization` is *post-drop* throughput, so heavy
loss mechanically suppresses the very quantity being used as the x-axis --
a feedback loop, not a clean congestion-to-loss causal curve. More data
didn't fix this because the confound is structural (which variable is being
measured), not a sample-size problem. **Decision unchanged: keep the
hand-picked `onset=0.7, scale=0.05`.** The stronger, now-demonstrated reason
is: no 2-parameter monotonic curve fit to `achieved_utilization` can be
correct here, and the production-relevant x-axis (`achieved_utilization`,
what `alpha` actually reads) is the wrong variable for this kind of fit even
in principle -- `requested_rate/capacity` (offered load) is the
non-confounded predictor (rho=0.977, per item 1 above) but isn't available
in production. This closes the loop on why beta/gamma's residual design
being what actually compensates for alpha's loss-blind-spot (item 5) is the
right fix, not a substitute for a loss curve fit that was never going to be
cleanly fittable in the first place.

### `weight_search_comparison.py`: adapted to 6 searched dimensions, run to completion

Adapted from its original 3-searched-dimension shape (alpha/beta/gamma;
delta/epsilon held fixed) to search **alpha/beta/gamma/delta/zeta/eta**
(6 of the formula's 7 weights). **epsilon is deliberately excluded from
the search, not merely held fixed**: `TopologyState.set_link_status`
removes a "down" link from `active_graph` entirely rather than costing it,
so a nonzero epsilon can never differentiate among paths that are all still
selectable candidates in a 3-path comparison fixture like this one -- there
is no way to give it a real, differentiating cost here without changing
what's being compared. epsilon stays at its production default (0.05)
throughout and contributes 0 to every path in this fixture.

**Fixture redesign (two iterations).** The first attempt gave each of
`sensitivity_analysis.py`'s existing 3 contrast paths (bad-utilization,
bad-delay, bad-loss) one extra stability/jitter property each (churn on
the bad-utilization path, delay-jitter on the bad-delay path, loss-jitter
on the bad-loss path). Checking the resulting regret landscape directly
showed this was wrong: delta's signal sat on the path that's already
unelectable purely from its 0.85 utilization, so no value of delta could
ever change which path wins -- OAT sensitivity was a *structurally
guaranteed* flat zero, not a real finding. A term only produces regret
signal if it can plausibly flip the decision for a path near the
ground-truth optimum; putting it on a path that's already permanently
lost (or already won) can't do that by construction.

Redesigned so Path C (bad loss) is a single "trap": modest loss (gamma)
plus real churn history *and* real delay/loss jitter, all three -- a path
that looks fine on congestion alone but is unstable once stability is
priced in. Path A (bad utilization) and Path B (bad delay, the intended
"correct" ground-truth answer) are left clean on delta/zeta/eta. Verified
this produces real, non-degenerate signal: dropping delta to 0 measurably
flips the decision-maker's choice away from ground truth (regret goes from
0 to 0.52494). zeta/eta's own raw magnitudes came out realistic-sized
(~0.07-0.2, matching real jitter scores measured on actual Mininet links
elsewhere in this project, not inflated to force a story) -- honestly
reported as a finding, not smoothed over: at today's production-default
weights, only delta's magnitude is currently large enough to flip a
decision in isolation; zeta/eta behave as fine-grained tie-breakers whose
effect shows up in the full joint search space, not in a one-at-a-time
sweep holding everything else at small defaults.

**Run**: grid search (3 points/dim, 3^6=729), Bayesian optimization
(from-scratch GP+EI, 20 random init + 709 guided iterations), simulated
annealing (729 iterations, geometric cooling) -- matched 729-evaluation
budget across all three. Runtime: grid ~1s, BO+SA together ~3 minutes
(GP refit is the dominant cost, cubic in the growing observation count).

**Result**: all three methods converge to **regret=0.0**. A 3000-point
random sample of the 6-D weight space finds **73% of random weight vectors
already achieve regret=0** -- and **current production defaults
(alpha=0.4, beta=0.3, gamma=0.2, delta=0.05, zeta=0.05, eta=0.05) are
already inside that zero-regret region.** This is the honest headline
result, not a new "optimal" weight vector to adopt: with this specific
3-path contrast fixture, the zero-regret region is too large a fraction of
the space to meaningfully discriminate between grid/BO/SA as exploration
strategies -- all three "succeed" almost immediately (BO and SA both landed
on a zero-regret point at their very first randomly-drawn evaluation, which
is unsurprising when 73% of random draws already qualify; grid search's own
245-of-729 evaluations-to-reach-optimum number is an artifact of its fixed
iteration order over a flat objective, not a real measure of grid search's
efficiency). **Practical conclusion: this benchmark validates the current
production weights (they already achieve zero regret against this
ground-truth objective) rather than arguing for a change**, and a
meaningfully discriminating comparison of the three search methods would
need either a narrower/harder regret landscape or averaging convergence
speed over many random objective realizations -- noted as follow-up scope,
not pursued further here given the diminishing returns on a secondary
methodology comparison.

## Auditing past "leave it as-is" decisions for real neglect (2026-08-19)

User question: across this session's many "decided not to implement" calls,
were any of those actually a missed fix rather than a justified one? Two
concrete candidates were checked.

**epsilon/reliability_penalty -- looked neglected, isn't.** `_calculate_edge_cost`
only runs on edges from `NetworkState.get_active_graph()`, which excludes
every "down" link entirely -- so `reliability_penalty = 0.0 if
status=="up" else 1.0` can structurally never be nonzero for any edge a
cost calculation ever sees, under normal correct link-failure handling.
This is not new or overlooked: `sensitivity_analysis.py`'s
`_run_cost_weight_sweep` docstring (lines 264-291) already documents this
exactly, with two dedicated isolation tests -- one deliberately constructing
the *inconsistent* state (`update_link_statistics(status="down")` without
the paired `set_link_status()`/`mark_link_failed()` call) where epsilon
does fire, and one with a properly-failed link where it doesn't. The
design is correct as-is: a genuinely failed link should be infeasible, not
merely expensive. What was checked and confirmed clean: whether this
project's *real* (non-experiment) code could ever produce that
inconsistent state. It can't -- there is no live/continuous monitoring
loop in this codebase (`src/monitor/statistics_collector.py` always writes
`status="up"`; the only place that ever writes `status="down"` outside the
offline experiment harness is `scripts/mininet_failure_recovery_demo.py`,
which already calls `state.topology.set_link_status(...)` directly, so
stats and topology are set in the same paired step every time, same as
`simulation_common.py::set_link_condition()`). **No gap exists to fix
given the project's current architecture.** If a future live monitoring
loop is ever added, this pairing requirement would need re-verifying then.

**`LINK_CAPACITY_MBPS=100.0` in `compute_flow_metrics()` -- a real, fixed
oversight.** This flat constant fed `throughput_mbps`'s clamp
(`min(offered_load_mbps, LINK_CAPACITY_MBPS) * throughput_factor`) for
every scenario script's reported throughput, despite its own code comment
already flagging it as stale ("this constant is still used as-is... a
separate, not-yet-revisited spot with the same stale-100-for-every-link
assumption `src/monitor/link_capacity.py` fixed for `topology.py`") --
the fix (`resolve_link_bw_mbps()`) already existed and was reused
everywhere else this session (`resolve_offered_load_utilization()`), just
never brought into this one function. Currently harmless in practice
(every flow in `traffic_generator.py` is 12-30 Mbps, well under even the
lowest real GEANT tier used here, so the clamp never actually engaged) --
but a landmine for any future larger flow or lower-capacity link.

**Fixed 2026-08-19**: added `resolve_link_capacity_mbps()` (single real
per-link lookup, LinkLabel-based, same fallback as before for the ~1/3 of
edges with no published label) and `resolve_path_capacity_mbps()` (bottleneck
= min capacity across a path's hops, mirroring how a real network's
achievable throughput is capped by its narrowest link). Also cached the
GEANT graphml parse at module level (`_load_geant_graph()`) -- it was being
re-read from disk on every call inside `resolve_offered_load_utilization()`,
which was fine at its old call frequency (once per scenario setup) but
would have been a real cost at `compute_flow_metrics()`'s call frequency
(every sample, every flow, every algorithm). `compute_flow_metrics()` now
uses the real per-path bottleneck instead of the flat 100.
`LINK_CAPACITY_MBPS` remains only as the last-resort default inside those
two resolver functions. Verified live: real capacity differs meaningfully
by path (`2-32`: 150 Mbps; `2->0->34->7`: 80 Mbps bottleneck) instead of a
uniform 100 everywhere. All 123 tests still pass; all 5 scenario scripts
smoke-tested clean end to end.

## Weight tuning, take two: a harder, multi-trial benchmark (2026-08-19)

The previous weight-search run (see above) was honestly reported as too
easy to be a useful benchmark: 73% of random 6-D weight vectors already
tied at regret=0, and current production defaults were already inside
that region, so the search validated the defaults without finding any
argument for change. Asked whether to stop there or design a harder
benchmark and re-tune -- chose to redesign.

**What changed.** `_build_extended_contrast_state()` now takes graded,
per-trial-randomized severities (`random.Random(seed)`) instead of one
fixed extreme value each: Path A's utilization draws from `[0.55, 0.95]`
instead of a fixed `0.85`; Path B's delay from `[15, 70]` ms instead of a
fixed `40`; Path C's base loss from `[0.02, 0.12]` (plus randomized swing
across its 3 samples) and churn event count from `[1, 6]` instead of fixed
`0.05`/4 events. `make_objective()` now builds **15 independent trials**
(different seeds) and returns `regret()` as the **mean regret across all
15**, instead of a single trial's regret. Rationale: one fixed
all-or-nothing scenario meant most of the 6-D space "won" trivially
because alpha/gamma alone already settled the decision most of the time;
averaging over a *distribution* of graded severities means getting
delta/zeta/eta's relative weight right can actually change the outcome in
some fraction of trials, closer to how a real deployment sees a range of
congestion severities rather than one fixed worst case.

**Result -- meaningfully better on every axis checked:**

- **OAT sensitivity is now non-degenerate on all 6 weights** (previously
  zeta and eta were exactly flat-zero): delta range 0.167, alpha 0.162,
  beta 0.101, gamma 0.098, eta 0.098, **zeta 0.033 (was 0.0)**.
- **Current production defaults now show real, nonzero regret**: 0.0325
  (previously exactly 0.0 -- the old benchmark couldn't tell current
  defaults apart from optimal at all). This is a genuine, if modest,
  finding: production's current small delta/zeta/eta weights (0.05 each)
  aren't quite enough to avoid an occasional wrong pick across this
  trial distribution.
- **The zero-regret region shrank**: 68% of a 500-point random sample
  (down from 73%), so the benchmark discriminates somewhat better, though
  a large "good enough" plateau still exists -- expected, since ground
  truth picked Path B in all 15 trials (never flipped to A or C), so most
  reasonable weight vectors that don't badly starve alpha/gamma still get
  the right answer most of the time.
- Grid, BO, and SA all still find weight vectors reaching **regret=0.0**
  (perfect across all 15 trials) -- e.g. BO/SA both converged to
  `alpha=0.844, beta=0.758, gamma=0.421, delta=0.259, zeta=0.511,
  eta=0.405`; grid found a different zero-regret point
  (`alpha=0.5, beta=0, gamma=0, delta=0, zeta=0, eta=0.5`). Multiple
  distinct vectors reaching the same regret=0.0 confirms the zero-regret
  region is a broad basin, not a single point -- there's no unique
  "optimal" weight vector to name, only a direction.

**Honest read, not a change recommendation yet.** The gap between current
defaults (0.0325 average regret) and the zero-regret basin is real but
small relative to the trials' own path costs (means 0.073-0.54). The
common thread across every zero-regret point found is *more weight on
delta/zeta/eta than their current 0.05 defaults* -- consistent with the
OAT finding that delta has the single largest individual impact (range
0.167, larger than even alpha). This is a legitimate, evidence-backed
direction (nudge delta/zeta/eta up), not a forced conclusion: the found
points (BO/SA's vs grid's) disagree with each other on exact values, and
this benchmark's 15 trials are still randomly drawn, not adversarially
chosen to find the worst case -- a genuinely confident production
recommendation would want either many more trials (variance in the
regret estimate itself hasn't been checked) or a harder, deliberately
adversarial contrast set. Not implemented as a production weight change
without the user's explicit call.

## Which optimizer actually fits this formula: a proper BO-vs-SA test (2026-08-19)

User question: which of grid/BO/SA is actually best suited to this cost
formula's regret landscape? Answering it surfaced a real bug in the
comparison script itself, fixed it, then ran the corrected experiment the
user specified: independent RNG streams, 30 seeds, Mann-Whitney.

**The bug**: `run_bayesian_optimization` and `run_simulated_annealing` both
defaulted to `seed=0` and both call `[rng.uniform(*BOUNDS[p]) for p in
PARAM_ORDER]` as their literal first action after seeding
`random.Random(seed)` -- with the same seed, that first draw is bit-
identical between the two functions. This is exactly why every prior run
showed BO and SA "independently" landing on the same point at "evaluation
1": they weren't converging, they were reading the same first random draw
off two separately-constructed but identically-seeded generators. This
invalidated any conclusion about which method finds a good point faster.

**Fix**: `spawn_independent_seeds()` (new), using `numpy.random.SeedSequence`
spawning -- the documented-correct way to get statistically independent RNG
streams from a shared master seed, rather than manually offsetting a single
int (which doesn't guarantee independence). `run_bayesian_optimization`/
`run_simulated_annealing` gained `seed` parameters wired through properly;
even the single "headline" run above now uses two independently-spawned
seeds instead of both silently defaulting to 0 (its own BO/SA best points
are now visibly different from each other, as they should be).

**Theoretical prediction first** (stated before running the numbers, so
it's falsifiable, not fit after the fact): earlier analysis in this section
established the regret objective is piecewise-constant, not smooth -- each
path's cost is linear in the weights, so "which path wins" only changes at
sharp cost-equality boundaries. This breaks the Gaussian Process's core
smoothness assumption (BO's RBF kernel treats nearby points as correlated,
which isn't true across a decision boundary), while SA's random-walk
approach makes no such assumption. Prediction: SA should be at least as
good as BO here, and BO's usual "smarter exploration" advantage over a
naive method shouldn't show up.

**Metric had to change too, and this was checked empirically before
committing to it**: "final regret at a fixed budget" turned out to be
degenerate regardless of budget size -- at this benchmark's 68% zero-regret
base rate, a 30-seed x 30-evaluation-per-run check gave *both* methods a
mean final regret of exactly 0.0 (Mann-Whitney U=450, p=1.0, meaningless
tie). This isn't a small-budget artifact: P(missing the zero-regret region
after k random draws) = 0.32^k, which is already under 1% by k=5 -- no
practical budget leaves final regret with real variance to compare. Switched
to **cumulative regret** (sum of regret over every evaluated point in a
run, not just the best found) instead -- the standard bandit/online-learning
metric for exactly this situation, since it penalizes evaluations spent on
clearly-worse points along the way, which "final best" is blind to once
every method eventually finds the optimum. Verified this has real,
non-degenerate variance on a small pilot (5 seeds) before running the full
comparison.

**Result (30 independent seeds each, 30-evaluation budget/run, matching
the "realistic retuning budget" framing, not the 729-evaluation headline
budget)**:

```
                          BO          SA
mean cumulative regret   0.8389      0.8208
median cumulative regret 0.8245      0.6901
Mann-Whitney U = 400.0, p = 0.4647 (NOT significant)
```

**Conclusion: no statistically significant difference between BO and SA
on this formula's regret landscape** (p=0.465, nowhere close to the
conventional 0.05 threshold). This actually confirms the theoretical
prediction, just not in the form first guessed: SA doesn't measurably
*outperform* BO either -- the two are statistically indistinguishable,
consistent with BO's usual advantage (a smooth surrogate model guiding
exploration) simply not applying to a piecewise-constant objective, so it
degrades toward performing like an unguided random-walk method rather than
actively losing to one. A small 5-seed pilot check (run before the full 30,
to sanity-check the metric) had suggested BO might be consistently lower --
**that impression did not hold up at proper statistical power**, the same
pattern as nearly every other "small-sample offline signal, refuted by a
properly-powered real test" finding this session (delay_jitter-loss_jitter,
churn-jitter, Tier 3 delay/loss-churn -- see earlier sections). Consistent
methodological lesson reinforced again: don't trust an n=5 impression.

**Practical takeaway at this point** (superseded below, kept for the
record of how the conclusion evolved): given this formula's
piecewise-linear-argmin structure, grid/BO/SA are not meaningfully
different in outcome quality, so grid search looked like a defensible
simple default -- not because it's *better*, but because BO's theoretical
advantage didn't materialize and SA added complexity without payoff
either.

## A method actually designed for this structure: DIRECT confirms the theory (2026-08-19)

User pushed the framing further: if the problem is really "find the
decision boundary in a piecewise-constant landscape," not "optimize a
smooth function," a method built for exactly that -- DIRECT (DIviding
RECTangles), which partitions the search space using each region's *size*
and its center's value rather than an assumed-smooth surrogate -- should
be tried and compared the same rigorous way (independent seeds/instances,
Mann-Whitney), not left as a hypothetical.

**Dependency decision, asked first**: this environment has no scipy (every
other method this session is hand-rolled numpy specifically because of
that). Asked whether to install scipy for `scipy.optimize.direct()` or
hand-implement DIRECT from scratch -- **user chose installing scipy**
(`requirements.txt` updated, noted as this project's only scipy usage;
DIRECT's potentially-optimal-rectangle selection, a lower-convex-hull
computation, is easy to get subtly wrong hand-rolled, and a self-implemented
bug would undermine the very comparison being run).

**A second fairness issue found while building this**: DIRECT is
deterministic (no seed of its own), so it can't be given "30 seeds" against
one fixed objective the way BO/SA's comparison worked -- it would just
replay the same trajectory 30 times. Fixed by generalizing the comparison
itself: `make_objective()` gained a `seed_offset` parameter so
`run_multi_instance_comparison()` can build **30 independent problem
instances** (genuinely different randomized 15-trial contrast sets, not
just different optimizer-internal RNG on one fixed problem) and run DIRECT,
BO, and SA once each per instance. This is also a more robust generalization
check for BO-vs-SA specifically than the earlier fixed-objective version,
since it now varies the underlying problem too.

**Result (30 independent problem instances, matched 30-evaluation budget,
cumulative regret as before -- final regret is still degenerate, all three
methods reach exactly 0.0 on average, for the same reason established
earlier)**:

```
                          DIRECT      BO          SA
mean cumulative regret    0.1079     0.8466      0.8701
median cumulative regret  0.1080     0.8263      0.7225

Mann-Whitney (cumulative regret):
  DIRECT vs BO:  U = 0.0,   p = 0.0001  (significant)
  DIRECT vs SA:  U = 210.0, p = 0.0004  (significant)
  BO   vs SA:    U = 422.0, p = 0.68    (not significant -- reproduces
                                          the earlier fixed-objective
                                          result, now under a more
                                          robust varied-instance design)
```

**DIRECT vs BO's U=0.0 is the most extreme value the statistic can take**:
every single one of DIRECT's 30 cumulative-regret values was lower than
every single one of BO's 30 values -- complete separation between the two
distributions, not a marginal edge. DIRECT vs SA is similarly decisive.
This is a strong, cleanly significant result, not a borderline one.

**Conclusion, now superseding the "grid is a defensible default" one
above**: the theoretical prediction is confirmed, and more strongly than
expected. DIRECT's space-partitioning approach -- which never assumes
smoothness, unlike BO's GP surrogate, and searches systematically by
region size rather than unguided random walk, unlike SA -- is
substantially and significantly better matched to this formula's real
piecewise-constant, decision-boundary-driven structure. BO and SA remain
statistically indistinguishable from each other (confirmed under two
independent experimental designs now), consistent with neither actually
exploiting the landscape's real structure. **DIRECT is the
recommended method for tuning this specific cost formula's weights**,
with the "why" being the dissertation-worthy part: not because DIRECT is
generically the best global optimizer, but because its specific mechanism
(region-size-based partitioning, no smoothness assumption) matches a
structural property of this exact formula (linear-in-weights path costs
→ piecewise-constant regret) that was diagnosed earlier in this same
session, not assumed going in. The full chain -- diagnose the objective's
real structure, predict which method should fit it, test that prediction
properly (independent seeds/instances, Mann-Whitney, a checked-not-assumed
choice of paired vs. unpaired test), and get a result that confirms the
theory this cleanly -- is presented as-is, including the two real bugs
found and fixed along the way (BO/SA seed-sharing; DIRECT's need for
varied problem instances rather than varied seeds), consistent with this
session's established pattern.

## When DIRECT's "optimal" point turned out to be a trivial artifact: a real-scenario check, and a Pareto frontier instead of a single number (2026-08-19)

DIRECT's headline-run best point (the one reported above) was `alpha=
beta=gamma=delta=zeta=eta=0.5` -- every one of the 6 searched weights
exactly equal. Checked directly: this is DIRECT's mandatory *first*
evaluated point (the exact geometric center of the `[0,1]^6` search box,
evaluated before any rectangle subdivision), not a refined result --
confirmed by inspecting `run_direct()`'s row log, `rows[0]` is always the
center and it already scores regret=0.0 given how large the benchmark's
zero-regret region is (68%, established earlier). DIRECT's real,
statistically-verified strength is in the *search efficiency* comparison
(cumulative regret across 50 varied instances, DIRECT vs BO/SA/random,
covered above) -- not this one specific point.

**Real-scenario sanity check found a genuine problem with that point.**
Built two links with the actual production `NetworkState`/`GraphBuilder`
(not the abstract benchmark): one at u=0.85 utilization, perfectly stable;
one at u=0.30, but recently rerouted 3 times (real `record_link_churn`
calls). Production weights price the churny-but-light link cheaper (0.151
vs 0.341) -- matches this project's stability-aware design intent. DIRECT's
all-0.5 point prices it *more expensive* (0.451 vs 0.426) -- would have the
DecisionEngine prefer the heavily congested link over the mildly unstable
one, the opposite of the intended behavior.

**Diagnosed why, in two steps:**

1. Tried "path 1" (unit normalization) first, per explicit user instruction
   to try the cheaper fix before the more expensive one. Rescaled
   delay_residual_ms and loss_residual onto saturation-capped [-1,1] ranges
   (200ms / 0.3, both grounded in this session's real measured delay/loss
   residual ranges) so every term would be on a comparable scale before
   weighting. **No effect** -- confirmed directly: the diagnostic trials set
   delay/loss to their exactly-predicted values (zero residual) on purpose,
   so beta/gamma never participate at all. The conflict is 100% alpha
   (utilization) vs delta (churn), and both are *already* properly
   [0,1]-normalized (utilization is a physical fraction of capacity;
   churn_score is capped via `LinkChurnTracker.saturation_count`). No units
   mismatch exists there for path 1 to fix.
2. Re-ran the same churn-vs-congestion trials under the benchmark's own
   ground truth (equal 1/6 weights, the same convention used throughout
   this whole weight-search workstream) -- **GT agrees with DIRECT, not
   with production's historical weights.** This means the real disagreement
   isn't "DIRECT found a bad point" -- it's that production's hand-picked
   weights (0.4/0.3/0.2 primary, 0.05 secondary, chosen once early in the
   project, never itself validated by any of this session's rigor) and this
   benchmark's equal-weight ground truth genuinely disagree on how much a
   churn event should outweigh a utilization gap. Also checked: `LinkChurnTracker`'s
   own `saturation_count=5` ("5 reroutes = maximally bad") is itself an
   unvalidated hardcoded default, not measured from real operational cost --
   so even a unit-correct [0,1] churn score doesn't have a real-world-grounded
   "how bad is bad" anchor to compare against utilization's very physically
   meaningful one.

**Conclusion: this is a genuine multi-objective value judgment, not a bug
fixable by better normalization.** Per explicit user direction, rather than
assert one arbitrary "correct" weight vector (which is what both
CURRENT_DEFAULTS and DIRECT's raw point implicitly do), built
`experiments/pareto_weight_analysis.py`: for a set of trade-off scenarios,
check whether one candidate genuinely Pareto-dominates the other
(no weight choice matters) or whether they're mutually non-dominated (a
real trade-off), and for the latter, sweep the weight simplex to find the
**critical ratio** separating which weight regime prefers which candidate.

```
Churn vs congestion (secondary weight: delta/alpha ratio)
  5 trials (churn severity 0.4-1.0, utilization gap 0.30-0.80): all mutually
  non-dominated (genuine trade-offs). Boundary ratios: 0.35, 0.50, 0.75,
  0.75, 0.80 -- verified exactly against closed-form algebra for these
  single-dimension-conflict trials (e.g. CD1: delta*0.8 = alpha*0.4 =>
  ratio=0.5, matches the empirical sweep's 0.5001).
  Production's actual ratio: 0.05/0.4 = 0.125 -- below every boundary found
  => production always prefers the churny-light link in this severity
  range (consistent with its historical behavior).
  DIRECT's raw ratio: 0.5/0.5 = 1.0 -- above every boundary found => DIRECT's
  point always prefers the congested-clean link (the divergence found above).

Delay-jitter vs congestion (secondary weight: zeta/alpha ratio)
  3 trials: also all mutually non-dominated. Boundary ratios: 1.02, 1.66, 2.18.
  Production's ratio (0.125) is again below every boundary => also always
  prefers the jittery-light link, consistent with production.
  DIRECT's ratio (1.0) sits *below* 2 of the 3 boundaries and right at the
  3rd => mostly (not always) also prefers the jittery-light link here --
  unlike the churn case, DIRECT's raw point roughly agrees with production
  on this specific tradeoff.
```

**This is itself an important, nuanced finding, not just "DIRECT was
wrong": the same raw equal-weighting assumption lands on different sides
of the boundary for different secondary dimensions** (wrong side for
churn, right side for jitter) purely because churn_score's and
delay_jitter_score's saturation conventions (`saturation_count=5` vs
`saturation_ms=150.0`) give them different effective real-world severity
scales despite both nominally outputting [0,1]. A single universal weight
vector cannot be simultaneously well-calibrated against every secondary
dimension's own arbitrary saturation convention -- which is precisely the
argument for reporting a Pareto frontier / regime map instead of insisting
on one number.

**Framed as two separate, independently-defensible dissertation
contributions, per explicit user direction:**
1. **Algorithm choice** ("which tuning method fits this cost formula"):
   DIRECT is statistically and substantially better than BO/SA/random search
   at optimizing *any* fixed regret objective over this formula's
   piecewise-constant landscape (n=50, Cliff's delta up to -1.0, p<0.001,
   out-of-sample-validated). This conclusion is untouched by everything in
   this section -- it holds regardless of what the "correct" weights turn
   out to be, since it's about search efficiency, not about which point is
   correct.
2. **Specific weight values**: not settled by search alone, because the
   benchmark's own "ground truth" bakes in an arbitrary equal-importance
   assumption that a real-scenario check shows disagrees with this
   project's own historical design intent on at least one real tradeoff.
   The Pareto/boundary-ratio analysis above is offered as the honest
   deliverable here instead of a single asserted-optimal vector: report
   which weight regimes prefer which outcome, and let the specific
   priority (how much a network operator should fear instability vs.
   congestion) be an explicit, argued value judgment in the dissertation
   text -- not a number smuggled in through benchmark construction.

No change made to `config/decision.yaml` as a result of any of this weight-tuning
arc -- current production weights remain the hand-picked originals, now with
a documented, quantified understanding of where they sit relative to the
churn/jitter-vs-congestion boundaries (safely inside the "prefer stability"
region for both, per the ratios above) rather than an unexamined default.

## Real SNDlib traffic-demand data calibrates offline scenario experiments' background links (2026-08-20)

User recalled asking, earlier in this project, whether offline scenario
experiments' background link data could be grounded in a real dataset
("SNDlib" -- the term surfaced across several turns as "sdnlib"/"slilib"
before landing on the real name) instead of the arbitrary formula
`build_network_state()` had used since the start:
`utilization = 0.22 + 0.01*(link_index % 6) + small random jitter` -- no
real data behind any of it, just a fixed pattern by iteration order.

**Verified the real data exists and is usable, rather than assuming.**
`data/sndlib_geant.xml` (fetched from a public SNDlib network-data mirror,
2005-vintage GEANT instance, 4-month-granularity peak demand matrix in
Mbps): 22 nodes with ISO-country-code ids (`de1.de`, `ch1.ch`, ...), a
complete 22x21=462-entry pairwise demand matrix. **21 of those 22 nodes
match our `Geant2012.graphml`'s country labels exactly by real identity**
(both datasets describe the same real GEANT backbone, different
snapshots) -- the only unmatched SNDlib node is `ny1.ny` (a transatlantic
New York node our topology doesn't have). The other 19 of our 40 nodes
(smaller/peripheral countries this SNDlib instance doesn't cover -- Balkans,
ex-USSR states, Baltics, Iceland, Denmark) have no real data to borrow.
Real per-node aggregate demand spans a genuine ~42x range (Switzerland
~1.21M Mbps total vs Luxembourg ~29K) -- real signal, not noise.

**Verified the mapping method before adopting it, per explicit user
request for a sanity pass**: log-scaled (linear would flatten everything
but the top node given the 42x range), min-max normalized into
`[0.15, 0.35]` (bracketing the old formula's own 0.22-0.28 range --
these are *background*, non-hotspot links, meant to carry mild ambient
traffic, not spike just because one endpoint is a real hub), edge baseline
= geometric mean of both endpoints' normalized demand (an edge touching
even one low-demand node should be pulled down, not dominated by its
busier neighbor). Two checks run before implementing, both passed:
1. **Distribution actually spreads** across the target range: 30/61 real
   topology edges have both endpoints matched; histogram over 5 bins was
   `[0, 6, 16, 6, 2]` -- bell-shaped, not collapsed to one point.
2. **Matches intuition on hand-picked edges**: Luxembourg-touching edges
   land low (0.212-0.218); Switzerland-Germany (the two single highest-demand
   matched nodes) lands highest among all matched edges (0.333);
   Austria-Germany lands solidly upper-middle (0.304) -- no edge that
   should intuitively be peripheral came out inflated, or vice versa.

**Implemented**: `experiments/sndlib_demand.py` (new) --
`resolve_edge_demand_baseline(u, v)` returns the real-calibrated baseline
for an edge if both endpoints are SNDlib-matched, `None` otherwise (caller
falls back to the original arbitrary formula, unchanged, for the 19
unmatched nodes -- no data is guessed at where none exists).
`simulation_common.py::build_network_state()` now calls this per edge
instead of the flat `index % 6` pattern for every background link whose
both endpoints are matched. 123/123 tests still pass; all 5 scenario
scripts smoke-tested clean; `baseline_comparison.py`/`sensitivity_analysis.py`
re-ran clean; `weight_search_comparison.py`'s objective still builds
correctly (regret at production defaults shifted slightly, 0.0325→0.0314,
an expected small change now that the shared/background edges it also
touches are partially real-calibrated, not a break).

**Scope note for interpreting future results**: this calibrates the
*background* link realism only -- the hotspot link(s) each scenario script
deliberately manipulates (e.g. `increasing_load.py`'s utilization ramp)
were already scripted, not random, and their delay/loss were already
derived via `congestion_model.py`'s real-fitted curve (delay) or
hand-picked default (loss), unaffected by this change. This closes the
"our background data is literally arbitrary" gap for roughly half the
topology's edges (30/61) without touching decision logic, algorithm
comparison validity, or the real-Mininet independence-check track (which
never used `build_network_state()` at all).

## A second SNDlib source (nobel-eu) for scenario diversity only, kept structurally separate from the real tier (2026-08-20)

Follow-up questions after the above: could the real-data tier be extended
to cover more nodes, and specifically could SNDlib's `nobel-eu` instance
help (it surfaced during the original SNDlib research)?

**Checked whether nobel-eu is real data, not assumed either way.** Found
and read a description of SNDlib's own classification of its network
instances into three backgrounds: (a) real industrial-project data, (b)
"reference networks" defined *for* international research projects, built
to correspond to realistic planning scenarios rather than measured from an
operating network, (c) NDA-protected/undisclosed. **nobel-eu is case (b)**
-- it's the network defined by the EU-funded NOBEL project (telecom
operators + equipment vendors + academic partners), a constructed planning
scenario. Corroborated independently: the file itself carries no
`<meta>`/`<origin>`/`<unit>` tag at all (unlike `sndlib_geant.xml`'s
explicit `<unit>MBITPERSEC</unit>` and real measured-data origin
citation), and its demand values sit on a wildly different, undocumented
scale (Amsterdam-Athens = 6.0, vs. the real geant matrix's comparable-distance
pairs in the hundreds-to-thousands). A separate search lead ("a GEANT
instance with 29 nodes/88 links, 25-300Gbps capacities") that looked like
it might be an even better real dataset was chased down to its cited
source and did **not** hold up -- read the full paper (arXiv 2508.09573)
directly and it contains no such topology table; the search-engine summary
that raised it wasn't substantiated by the primary source.

**Coverage check: even combined with the real tier, does not reach full
coverage.** nobel-eu's 28 city nodes map to 19 countries by identity, but
16 of those overlap with countries the real tier already covers. Only 3
genuinely new countries: Denmark (Copenhagen), Norway (Oslo), Serbia
(Belgrade). Combined real+nobel-eu coverage: 24/40 nodes, still 16 nodes
short of full coverage (Ukraine, Moldova, Bulgaria, Romania, Turkey,
Cyprus, Malta, Belarus, Macedonia, Montenegro, Lithuania, Russia, Iceland,
Finland, Estonia, Latvia) -- no known public dataset found to cover these.

**Adopted for a narrower, honest purpose per explicit user framing**: use
nobel-eu's *relative demand pattern* to give background links real
structural variation instead of the flat index-based formula, without
claiming it represents real Copenhagen/Oslo/Belgrade traffic, and without
letting it blend into or override the real tier. Implemented as a strict
three-tier fallback in `simulation_common.py::build_network_state()`:
(1) `resolve_edge_demand_baseline()` -- real geant data, unchanged; (2)
`resolve_edge_diversity_baseline()` (new) -- nobel-eu, consulted *only*
when tier 1 already returned `None` for that edge, so a real value is
never overridden or averaged with a synthetic one; (3) the original
arbitrary formula, unchanged, for whatever neither tier covers. Result:
tier 1 still covers 30/61 edges; tier 2 adds 6 more (edges touching
Denmark/Norway/Serbia and an already-matched neighbor); 25/61 edges remain
on the original arbitrary formula. 123/123 tests pass; all 5 scenario
scripts + `baseline_comparison.py` + `sensitivity_analysis.py` re-ran
clean.

## Churn-adaptive minimum-improvement threshold: a real production fix, found via the new scenario-outcome objective's sanity check (2026-08-20)

While building a new weight-search objective grounded in real scenario
outcomes (delay/throughput/loss vs the `dynamic` baseline, rather than the
abstract regret-vs-assumed-GT objective -- see the two sections above),
the very first sanity check surfaced a real, production-relevant finding
unrelated to weight search itself: on `congestion.py`'s sustained-overload
phase, `proposed` **never rerouted at all** (0/12 samples), regardless of
which of several clearly-different weight vectors was tested.

**Root-caused, not assumed.** Traced the exact per-sample decision path
(instrumenting `ThresholdViolation`/`reroute` output directly, not
guessing): the utilization threshold *was* being violated correctly every
congested sample, but the candidate path's cost improvement over the
current path fell just short of the fixed `minimum_improvement` gate
(`relative_cost_reduction: 0.15`) once the **offered-load self-influence
correction** (a real, previously-fixed, and independently correct
mechanism -- see item 6/9 in pending_tasks -- that prices a candidate
path's own future load before comparing costs) shrank the improvement
margin. Verified precisely with two independent single-mechanism tests:
disabling *either* the offered-load correction *or* the min-improvement
gate alone (leaving the other active) was independently sufficient to
restore the reroute, both landing on the same earliest-possible sample
(sample 6, exactly matching `persistence_required_samples=3`'s own
floor) -- confirming the two mechanisms were jointly suppressing an
improvement margin that was real but modest (~9%), not that either one
alone was structurally broken.

**The real design gap**: `minimum_improvement`'s thresholds
(`absolute_cost_reduction: 0.1`, `relative_cost_reduction: 0.15`) were a
single fixed pair of constants applied identically regardless of context
-- appropriate for protecting against reroute *flapping* on a link with
real recent instability, but needlessly strict for a link with **zero**
recent churn history, where there is no flapping risk to protect against
and a genuine, if modest, improvement is being blocked for no real reason.

**Fix**: `DecisionEngine._churn_adaptive_min_improvement()` (new) scales
both thresholds by the max real-time `churn_score` across the reroute's
affected link(s) -- linear interpolation between a new, more permissive
floor (`absolute_cost_reduction_low_churn: 0.03`,
`relative_cost_reduction_low_churn: 0.05`, used at churn_score=0) and the
original values as the ceiling (used at churn_score=1.0, i.e. an
already-flapping link gets *exactly* the original, unchanged behavior --
this fix can only ever make the gate *more* permissive for stable links,
never less cautious for unstable ones). `_execute_reroute()` (the single
shared entry point for every kind of reroute -- congestion, failure,
recovery switchback) now calls this instead of reading the flat config
values directly; `emergency=True` reroutes are unaffected (already bypass
both floor and ceiling with 0.0). This deliberately reuses the same
`churn_score` signal already computed for δ's cost-formula term and
already real-time-available on `NetworkState` -- no new signal introduced.

**Verified working end to end, not just in isolation**: re-ran
`congestion.py`'s sustained phase with *nothing* manually disabled --
proposed's mean delay dropped from 272.84ms to 119.83ms (-56%), throughput
3.92→7.54 Mbps, loss 0.0086→0.0036, reroute count restored from 0 to 12
(matching `dynamic`'s count) -- identical to the numbers obtained by
manually disabling either mechanism, confirming the adaptive threshold is
a real, working, general fix rather than a special-cased patch for this
one scenario. One existing test
(`tests/decision_engine_integration.py::test_decision_engine_integration`)
broke as a direct, correctly-diagnosed consequence: its "low gain, should
be rejected" fixture used a ~9% improvement that cleared the *old* 15%
ceiling as intentionally rejected, but 9% also clears the *new* 5% floor
(this link has no churn history in that fixture) -- fixed by adjusting the
fixture's `low_gain_path` utilization to produce a ~3.6% improvement
instead (comfortably below both the old ceiling and the new floor), so the
test still correctly demonstrates a genuinely-rejected marginal case under
the new design. 123/123 tests pass.

**Remaining gap vs `dynamic` is understood and is not a bug**: even with
the fix, proposed's delay (119.83ms) stays notably above `dynamic`'s
(54.21ms) in this specific scenario -- traced to `persistence_required_
samples=3` itself, a *third*, independent, always-active gate: `dynamic`
has no persistence requirement and reroutes on the very first violating
sample (sample 4), while `proposed` cannot react before sample 6 no matter
how permissive the cost-improvement gate is. This is the deliberate,
core stability-vs-responsiveness trade-off this whole project is about --
not something to "fix," and is explicitly not conflated with the
min-improvement issue that was fixed.

**Broader design discussion (not implemented, offered as a documented
direction)**: two other axes could similarly replace `minimum_improvement`'s
flat constants with context-aware ones, both reusing existing signals
rather than introducing new ones: (a) scale by *current-path severity*
(a barely-over-threshold link should require a bigger margin to justify
switching; a severely-degraded one should accept almost any improvement),
and (b) scale by *traffic-class priority*, mirroring `TrafficPolicy`'s
existing `reroute_immediate` mechanism for high-priority classes. Only
(the churn-based) direction was implemented and tested this session, per
explicit user direction to try one concrete direction first rather than
build all three speculatively.

## Fallback baseline range widened to remove a systematic bias (2026-08-20)

User directly demanded a fix ("我需要修复，来优先使用置信度高的数据") for the
systematic bias documented in `sndlib_demand.py`'s `FALLBACK_LOW`/`FALLBACK_HIGH`
docstring: the old fallback formula for background links with no real SNDlib
data (`0.22 + 0.01*(index % 6)`, a narrow ~0.05-wide band) was deterministically
underrepresenting those links' plausible utilization relative to real
demand-calibrated edges between busy hub countries (whose legitimate range
reaches up to 0.35), so any candidate path touching even one SNDlib-uncovered
node looked artificially cheap and was chosen every time regardless of seed —
verified across 20 seeds pre-fix, all 20 landed on the same Iceland-touching
path `['2', '32', '34', '7']`.

**Fix**: `simulation_common.py::build_network_state()`'s tier-3 fallback now
draws `rng.uniform(FALLBACK_LOW, FALLBACK_HIGH)` — the same [0.15, 0.35] range
tiers 1/2 (real SNDlib data, nobel-eu diversity data) already use — instead of
the old narrow index-based formula. The now-unused `base_utilization` parameter
was removed from `build_network_state()`'s signature (no caller passed it
explicitly). `FALLBACK_LOW`/`FALLBACK_HIGH` are exported from `sndlib_demand.py`
so the fallback tier is defined relative to the same constants the real tiers
use, not a second hand-picked range that could drift out of sync.

**Verified, not just asserted**: re-ran the same 20-seed determinism check
post-fix. The bias is gone — the Iceland-touching path now wins only 2/20
seeds (down from 20/20); a fully SNDlib-covered path `['2', '0', '34', '7']`
now dominates (14/20), and 4/20 seeds correctly stay on the original hotspot
path (no reroute clears the gate at all). 123/123 tests pass.

Re-ran `congestion.py`'s sustained phase (5 seeds, dynamic vs proposed with
mechanism 1+2 both active) with the corrected background data: delay
118.07ms vs the pre-fix 119.83ms, throughput 8.47 vs 7.54 Mbps, loss 0.0037
vs 0.0036 — all within normal run-to-run noise of the pre-fix churn-adaptive
numbers, so this specific scenario's headline comparison did not change
materially. `dynamic`'s numbers (delay 51.91ms) are likewise consistent with
its pre-fix value (54.21ms).

**One real, unplanned side effect found and reported, not hidden**: re-checked
whether mechanism 1 (offered-load self-influence correction) still changes the
sustained-phase outcome post-fix, the same way the churn-adaptive section above
verified it did pre-fix. Across 5 seeds, toggling mechanism 1 on/off now
produces the *identical* final path every time (`compare_paths` picks the same
candidate regardless of the ~0.16 self-load utilization bump). Mechanism 1's
logic is unchanged and still fires (confirmed with a direct call trace); it is
just no longer the deciding factor for this specific scenario's candidate-path
ranking now that the background baselines are wider and more spread out —
the real-data-driven cost gap between candidate paths now dominates the
comparison margin mechanism 1 used to swing. This does not mean mechanism 1 is
dead in general (it is still a live, correct per-edge cost term, and other
scenarios/pairs may still turn on its effect); it means this one default
scenario configuration is no longer a case that demonstrates its effect, and
any dissertation figure claiming otherwise needs to be re-checked against this
post-fix run rather than the earlier `mechanism_comparison.png`.

## Tier 2 (nobel-eu) re-centered — same bias mechanism found in a second, smaller place (2026-08-20)

Directly prompted by the user asking a general methodological question right
after the fix above shipped: "if we have a preference for certain paths, does
that affect our judgment of the experiment's own results — e.g. could a
reroute's choice of new path be influenced by this preference?" Rather than
answer in the abstract, checked it against the actual routing mechanism first:
`PathCost.find_best_path()` uses `networkx.shortest_path(graph, weight='weight')`
— full-graph Dijkstra on *summed* per-edge cost, with alpha (utilization) at
weight 0.4, the single largest term. This means background-baseline generation
does not just loosely correlate with path choice, it **mechanically determines**
it for any pair of non-congested candidates — confirming the user's concern is
structural, not hypothetical, and applies most strongly to *which path* gets
chosen (the deliberately-injected congestion signal dominates *whether* to
reroute at all, since 0.88 vs 0.35 dwarfs any 0.15-0.35 background noise).

Checked directly whether the just-shipped fallback fix left any *other*
instance of the same failure mode (a background tier reading systematically
low/high and deterministically, not randomly) — it did. `resolve_edge_
diversity_baseline()`'s (tier 2, nobel-eu) 6 our-topology edges had mean 0.186
vs tier 1's 0.256 (a real ~0.07 gap) and, unlike tier 3, are **not randomized
per seed** (nobel-eu's demand matrix is fixed data), so this is a deterministic
bias of the same shape the fallback fix just removed elsewhere — just smaller
in footprint (6 edges, not 25). Traced precisely to `PRIMARY_PAIR`'s own source
node ("2", Denmark): 3 of its 4 non-fallback edges are tier-2-covered
(0.164–0.241, all below tier 1's mean), which is why the fallback fix's own
20-seed re-check still showed one specific alternate path (`['2','0','34','7']`,
via the tier-2-covered Denmark–Netherlands edge) winning 14/20 — not because it
was genuinely better, but because of this second, still-live instance of the
exact bias just fixed elsewhere.

**Fix (user chose this option directly, over "add random jitter" or "leave and
document")**: `_load_diversity_baselines()` in `sndlib_demand.py` now
re-centers its raw min-max-normalized output via a z-score rescale against
tier 1's own empirical node-level mean/std (`_load_node_demand_baselines()`),
clipped back into `[_BASELINE_LOW, _BASELINE_HIGH]`. This fixes the *level*
tier 2 reads at without erasing its own relative structure (Denmark still
reads differently from Norway, exactly per nobel-eu's own data) — the same
"match the real tier's statistics, don't fake realism" principle as the
fallback fix, applied to a second, distinct piece of the same three-tier
system.

**Verified, not asserted**: edge-level tier-2 mean moved from 0.186 to 0.225
(vs tier 1's 0.256 — a real, close-but-not-forced match, not an exact clamp).
Re-ran the same 20-seed determinism check: no single alternate path dominates
any more — the original hotspot path is (correctly) retained 12/20 times, and
the two real alternates split 4/20 each, essentially balanced, vs. the
14/2/4 split immediately after the first fix alone. 123/123 tests pass.
Re-ran the sustained-phase mechanism comparison (5 seeds): delay 118.53ms
(proposed) vs 52.50ms (dynamic) — consistent with both the pre-fix and
first-fix numbers, so still not a headline-changing shift for this particular
scenario/seed set. Mechanism 1 vs mechanism 1+2 stayed identical across the
same 5 seeds — the "mechanism 1 became a no-op for this scenario" finding
from the first fix still holds after this second correction, not an artifact
of the first fix alone.

## Offered-load penalty offset for the min-improvement gate — real, verified fix; a deeper, separate issue found underneath it (2026-08-20)

Following the multi-pair robustness check above (4 objectively-selected
pairs, one hotspot edge per tier + PRIMARY_PAIR, congested-window-only
delay averaging), which found `proposed` stuck on the congested path for
most or all of the congestion window in 3/4 pairs despite the
churn-adaptive fix, user directed a 4-step plan: (1) standardize on the
congested-window-only averaging convention used by that check (the
looser whole-phase-average convention used earlier, e.g. the churn-
adaptive section's 118.79ms/52.59ms numbers, mixes in 6 baseline samples
and is NOT directly comparable to these numbers); (2) verify the root
cause (multi-hop candidates' improvement margin gets compressed by the
offered-load self-influence correction) against a few more pairs before
changing anything; (3) fix it with a threshold that adapts to the real
structural cause (hop count / offered-load magnitude), not a new fixed
constant; (4) re-verify across all 4 pairs.

**Step 2 verification** (`verify_offered_load_compression.py`, 6 pairs
spanning 3-5 hop candidates): confirmed the mechanism is *exact*, not just
correlated — the "penalty" (how much the correction eats into the raw
improvement) is fully explained by `alpha_weight * offered_load_utilization
* hop_count / old_cost`, an already-computable quantity. Penalty ranged
30%-60% of the raw improvement across the 6 pairs tested.

**Step 3 fix**: `PathCost.offered_load_penalty_fraction()` (new) computes
this delta *exactly* (candidate cost with vs without the correction,
divided by old_cost) rather than an assumed hop-count formula — no new
tunable constant introduced, consistent with the churn-adaptive fix's own
"reuse a real structural factor, don't add a new independent assumption"
principle, taken one step further (exact computed delta beats an assumed
linear-in-hops proxy). `DecisionEngine._execute_reroute()` now subtracts
this fraction from both `min_abs`/`min_rel` (churn-adaptive result) before
comparing, floored at 0.0 — `is_improvement()`'s own unconditional
`new_cost < old_cost` check remains the floor beneath that, so this can
only ever remove the known accounting inflation, never accept a literally
worse path. 123/123 tests pass.

**Step 4 verification — partial, honestly reported**: re-ran all 4 pairs.
Two improved measurably: PRIMARY_PAIR (2→7) reroute rate 0.60→1.00 (now
matches dynamic every seed), delay 267.85→161.70ms; tier3 (0→10) reroute
rate 0.50→0.80, delay 294.11→214.25ms. **Two pairs (tier1 0→12, tier2
0→37) showed zero change** — traced precisely, not hand-waved: for these,
the offered-load correction on the (5-hop) candidate is large enough that
`new_cost` (0.779) exceeds `old_cost` (0.605) *outright*, so
`is_improvement()`'s hard pre-gate check fails before any threshold is
ever consulted — a case the threshold-offset fix cannot reach by
construction, since it isn't a threshold problem at that point.

**A further, deeper issue found while explaining this, not yet fixed**:
checked whether the correction's size here is itself fully justified —
`offered_load_utilization` is computed once, relative to the *hotspot
link's* own capacity (100 Mbps here), then applied as the same fraction to
every edge of the candidate regardless of that edge's own real capacity.
For this candidate, 3 of 5 edges are actually 150 Mbps links (confirmed via
`resolve_link_capacity_mbps()`), so the correction over-estimates this
flow's true utilization impact on those edges by 0.24 vs the true 0.16 —
a real, quantifiable inaccuracy. Estimated it explains only part of the
gap, though (rough per-edge-correct recompute still gives ~0.384 raw
penalty, close to the observed 0.364) — the larger share is simply that a
5-hop detour genuinely carries this flow's own future load across 5 edges,
which is a real cost, not an artifact, and arguably *should* count against
a long detour. **Deliberately not fixed this session**: correcting it
properly means threading the flow's raw Mbps demand (not a single
precomputed fraction) through `ProposedDriver` → `DecisionEngine` →
`PathCost` so each edge can compute its own utilization contribution from
its own capacity — a real refactor touching multiple call sites and
existing tests, not a contained patch like the fix above, and it wouldn't
fully close the gap on its own (the genuine multi-hop self-load cost would
remain). Flagged as an open item for a deliberate decision, not
silently absorbed into this session's fix. Full numbers and the exact
verification script paths are recorded in `pending_tasks_2026_08_12.md`
item 24.

## Per-edge, no-double-counting offered-load correction — the real refactor, done (2026-08-20)

User asked directly "what exactly is this, how do we fix it, will it help"
after the section above. Investigated one specific failing case (0→12)
precisely rather than guessing, and found **two distinct bugs**, not one:

1. **Capacity mismatch** (already documented above): `offered_load_
   utilization` was computed once against the *hotspot link's* own
   capacity, then applied as that same fraction to every edge of the
   candidate regardless of that edge's real capacity.
2. **Double-counting** (found while explaining the first): a candidate
   that reuses part of the current path (a common, realistic case --
   rerouting around one congested hop while keeping the rest of the
   route) was getting this flow's own future load bumped onto those
   *shared* edges too, even though they already carry the flow's real,
   current contribution today. For the (0→12) case this was the dominant
   bug: correcting only the capacity mismatch moved new_cost from 0.7786
   to 0.7120 (still worse than old_cost=0.6049); correcting *both* moved
   it to 0.5598 -- a real +7.46% improvement, flipping the case from
   "reject" to a genuine accept.

**Fix implemented** (real refactor, not a threshold patch): `offered_load_
utilization` (a single precomputed fraction) was replaced end-to-end with
`offered_load_mbps` (the flow's raw demand) across `ProposedDriver` →
`DecisionEngine` (`evaluate_pair`/`evaluate_service_congestion`/
`evaluate_failure`/`evaluate_recovery_switchback`/`_execute_reroute`) →
`PathCost` (`calculate_path_cost`/`is_improvement`/`compare_paths`), all 4
scenario scripts, and `priority_policy.py`. `resolve_link_capacity_mbps`
moved from `experiments/simulation_common.py` into `src/monitor/
link_capacity.py` (it wraps genuinely production per-link-capacity
resolution but had only ever been wired up on the offline-experiment
side) so `PathCost`, production code, can use real per-edge capacity
without importing from `experiments/`. `PathCost.calculate_path_cost`
gained `exclude_edges` (edges to leave unbumped -- used by `is_improvement`/
`compare_paths` for whatever `new_path` shares with `old_path`). The
earlier threshold-offset patch (`offered_load_penalty_fraction` and its
call in `_execute_reroute`) was **removed**, not kept alongside this --
once the underlying cost is computed correctly, offsetting the threshold
on top of that would double-discount the same correction twice. New test
`tests/path_cost.py::test_offered_load_not_double_counted_on_edges_shared_with_current_path`
demonstrates the fix directly. 124/124 tests pass (123 + this new one);
all 5 scenario scripts smoke-tested clean.

**Re-verified across all 4 pairs -- mostly a decisive win, one honestly-reported correction**:

| pair | before this fix | after this fix |
|---|---|---|
| tier1 (0→12) | 0.00 reroutes, 429.98ms | **1.00 reroutes, 170.89ms** |
| tier2 (0→37) | 0.00 reroutes, 426.02ms | **1.00 reroutes, 169.08ms** |
| tier3 (0→10) | 0.80 reroutes, 214.25ms | **1.00 reroutes, 161.01ms** |
| PRIMARY_PAIR (2→7) | 1.00 reroutes, 161.70ms | **0.40 reroutes, 320.92ms** |

Three of four pairs now match `dynamic`'s reroute rate exactly (1.00) with
delay dropping further still. **PRIMARY_PAIR's number went down, and this
needed explaining, not smoothing over.** Traced precisely: PRIMARY_PAIR's
winning candidate (`['2','32','34','7']`, hotspot `2-4`) shares *no* edges
with the current path, so the double-counting fix doesn't apply to it --
the only thing that changed for this specific case was **removing** the
earlier threshold-offset patch. That patch subtracted the *entire*
offered-load correction from the min-improvement threshold whenever no
edges were excluded (which, for a fully-disjoint candidate, is every
edge) -- effectively zeroing out most of the gate rather than precisely
compensating for a real inaccuracy. PRIMARY_PAIR's earlier "1.00" was
itself inflated by that over-generous patch; 0.40 is the properly-computed,
honest number for this specific pair -- still nonzero (still clearly above
the pre-churn-adaptive 0/12 baseline), just not as strong as previously
reported. Not fixing this further this session -- it reflects a genuine,
seed-dependent case where this particular candidate's real improvement
margin sits close to the churn-adaptive floor, which is the correct,
conservative behavior the gate is designed to produce, not a bug.

**Background-tier independence re-confirmed**: the 20-seed determinism
check (same one used to verify the two SNDlib bias fixes) still shows no
single path dominating after this refactor -- 3 distinct outcomes, most
seeds correctly show no reroute (`2-4-6-7` retained, 15/20), matching this
session's now-stricter, more honest gate. Confirms this fix is orthogonal
to the background-data tier work: it changes how a flow's own future load
is priced, not how background links are generated.

## Persistence window never actually reset on recovery — found via an oscillating-hotspot flapping check, fixed, then redesigned as a leaky bucket (2026-08-20)

User asked to actually build the "close-cost multi-path, fluctuating conditions" verification this whole thread had been working toward. Built an oscillating-hotspot scenario (bursts of violating samples, then clearing samples, repeated many cycles) across the same 4 objectively-selected pairs, including `static` this time (this project's actual convention is 3 algorithms, not 2 -- user caught this had been dropped).

**First result was confusing, not celebrated**: `dynamic` reroutes exactly once and never again, no matter how the scenario is constructed (confirmed a third independent way this session -- pure ambient noise, single sustained spike, now repeated oscillation). Read `DynamicBaseline.evaluate_reroute()` directly to explain why: `should_consider_reroute` is gated on `current_utilization > threshold` -- i.e. "is *my own current path* bad right now" -- not "does a cheaper path exist." `compute_path()` (full-graph Dijkstra, same mechanism `proposed` uses) runs every sample regardless, but its result is only acted on once the gate passes. This means `dynamic`, despite the name, is not the naive "always chase the global optimum" baseline the whole premise assumed -- it only reacts when its own situation is actually bad, so once it escapes to *any* non-congested path it structurally cannot flap again, regardless of how alternatives fluctuate. The literature's usual "naive dynamic routing flaps under close-cost fluctuation" framing does not describe this specific implementation.

**Second finding, found while explaining the first**: `proposed` also reacted once during the oscillation, which shouldn't have been possible if persistence genuinely required 3 *consecutive* violations against a 2-on/2-off burst pattern. Traced precisely (not assumed): `PersistenceChecker.evaluate_sample`'s window-clearing branch (`if not is_violation: self.clear_window(...)`) is called from exactly one place in the entire codebase (`decision_engine.py`), and that call site hardcodes `is_violation=True` -- the clearing branch is dead code. `clear_window` is otherwise only reached via `record_reroute` (after a *successful* reroute). So a link's violation count never actually reset on recovery, only on a successful reroute -- silently turning "N consecutive violating samples" (the documented intent) into "N violations total since the last reroute." This is why the oscillation test's first pass "worked" -- not because persistence correctly filtered noise, but because violations quietly accumulated across the clean gaps.

**First fix (strict reset), then reconsidered**: added `DecisionEngine.clear_persistence()` (later renamed, see below) and wired it into both places a "not violating" branch exists -- `evaluate_service_congestion`'s internal below-threshold check, and `ProposedDriver.step()`'s else-branch (the only two places in the codebase that determine "no violation this sample"). 124/124 tests pass; the standard sustained-congestion (`congestion.py`) numbers were unchanged (expected -- a single continuous burst has no clearing gap for this fix to touch). Re-ran the oscillation test with strict reset: `proposed` became numerically identical to `static` (0 reroutes, same delay/throughput) -- with a burst shorter than `required_samples=3`, persistence can now *never* complete, no matter how many times the burst repeats. User asked directly: doesn't this mean proposed should be optimized further, and isn't `dynamic`'s not-flapping itself suspicious? Answered with the `DynamicBaseline` code reading above, then proposed a concrete next step rather than leaving strict-reset as the final word.

**Leaky-bucket persistence (the redesign actually implemented)**: strict reset is *too* strict in the other direction -- a link that violates repeatedly but briefly (each burst under `required_samples`) is now permanently invisible to `proposed`, indistinguishable from a perfectly healthy link, no matter how often the pattern repeats. Replaced clear-on-recovery with `PersistenceChecker.leak_window()`: one clean sample forgives exactly one accumulated violation (`deque.popleft()`), rather than wiping the whole window. No new free parameter -- 1:1 leak rate, reusing the existing violations deque. `DecisionEngine.clear_persistence` renamed to `leak_persistence` (both call sites updated to match). 124/124 tests pass; standard sustained-congestion numbers unchanged again (a single burst ≥3 samples still satisfies persistence immediately, same as before, leaky or not).

**Verified precisely, both directions**:
- Symmetric on/off (equal violate/recover counts, e.g. 2-on/2-off or 1-on/1-off): nets to zero every cycle by construction, correctly *never* triggers (0/10 seeds) -- intentional, not a gap: a 50/50 link isn't chronically worse than tolerable, it's borderline.
- Asymmetric on/off (violates more often than it recovers, e.g. 2-on/1-off -- each individual 2-sample burst still can't satisfy strict-consecutive alone) nets upward across cycles and *does* eventually trigger in 4/10 seeds -- genuine evidence of catching chronic-but-brief instability that strict consecutive-only semantics structurally could not see (confirmed this is not just single-burst persistence in disguise: a burst-length-4-or-more pattern also triggers, but for the *wrong* reason -- 4 consecutive already satisfies strict persistence on its own, not a leaky-bucket effect; 2-on/1-off is the case that actually isolates the leaky-bucket's contribution).
- A genuinely isolated single 2-sample spike followed by 20 clean samples still never triggers (0/10 seeds) -- the original noise-rejection goal is fully preserved, not traded away for the chronic-instability fix.

**Re-ran the 2-on/1-off scenario across all 3 algorithms, all 4 pairs, `static` included**: `proposed` now matches `dynamic`'s reroute rate exactly in 3/4 pairs (1.00) and clearly beats `static` on delay/throughput in all 4 (e.g. tier1: 93.34ms vs static's 327.78ms, throughput 14.78 vs 7.60 Mbps) -- a decisive, honest win over the "do nothing" baseline that strict-consecutive semantics had silently reduced `proposed` to. `proposed`'s delay still trails `dynamic`'s in absolute terms (93ms vs 41ms) -- expected and not something to chase away: persistence's net-accumulation-then-react necessarily costs some reaction lag even in the fixed design, the same stability-vs-responsiveness trade-off already documented in the churn-adaptive section. PRIMARY_PAIR (2→7) remains the one pair where `proposed`'s reroute rate stays lower (0.40) -- same root cause as the offered-load section above (this specific candidate shares no edges with the current path, so its improvement margin is a separate, already-understood story).

**Framing, per explicit user direction**: this keeps the dissertation's stated focus -- "Resilient and Stability-Aware SDN Traffic Engineering Framework for Dynamic Network Conditions" -- intact. The leaky bucket is not a new mechanism bolted on; it is a targeted correction to persistence's own resilience-under-dynamic-conditions claim (chronic-but-intermittent instability is exactly a "dynamic network condition" the framework's own title commits to handling), reusing the same violations deque and 1:1 arithmetic rather than introducing a new tunable constant.

## weight_search_comparison.py: NUM_TRIALS re-derived from scratch, severity ranges re-grounded in real data (2026-08-20)

Two pending items from earlier this session ("data extraction done, code not yet changed" per the pre-compaction summary) were closed out -- but the extraction data itself wasn't recoverable after compaction, so both were **redone from scratch, not trusted from memory**, and the fresh derivation disagreed with the recalled numbers in both cases.

**NUM_TRIALS**: recalled as "~200" going in. Redid the Law & Kelton pilot-variance check properly: 30 independent 15-trial regret estimates at a production-like weight point (0.4/0.3/0.2/0.05/0.05/0.05) gave mean=0.00889, std=0.01012 -- CV>1, because regret here is zero-inflated (item 16/17's "68% zero-regret base rate": most trials land exactly at 0, a minority carry all the nonzero signal). A *relative*-precision target (95% CI within 10% of the mean) is infeasible for a zero-inflated near-zero mean -- it demands N in the thousands (computed: ~7460) purely because the mean is tiny, not because the estimate is practically unstable. Switched to an *absolute*-precision target instead (95% CI half-width <= 0.005, small relative to this project's documented nonzero OAT sensitivities of 0.033-0.167): implied per-trial std ~0.0392 -> required N ~236, rounded to **NUM_TRIALS = 250**. Recalled "~200" turned out to be in the right ballpark by coincidence of methodology (an absolute-precision target was apparently used before too), not a number that could be trusted without rederiving.

**Severity ranges**: `_build_extended_contrast_state()`'s Path A/B/C severities were still the original hand-picked values (`bad_u ~ U(0.55,0.95)`, `bad_delay ~ U(15,70)`, `base_loss ~ U(0.02,0.12)`, `churn_events ~ randint(1,6)`) -- never actually replaced despite being flagged as a pending grounding task. Extracted real percentiles directly from this project's own already-collected real Mininet data (still on disk, extraction redone since the pre-compaction numbers weren't recoverable):
- `results/independence_check/independence_samples.csv` (196 real samples): `achieved_utilization` p75-max = (0.445, 0.782); `delay_ms` p75-p99 = (177.8, 697.1) -- real congestion delay runs **far higher** than the old hand-picked ceiling of 70ms, capped at p99 (not the real max of 1368.1ms) to avoid one outlier stretching the whole range.
- `results/loss_saturation_check/loss_samples.csv` (20 nonzero real samples): `loss` p25-p75 = (0.182, 0.520) -- real measured congestion loss runs roughly an order of magnitude higher than the old (0.02, 0.12) range.
- `results/churn_jitter_check/churn_jitter_samples.csv` (56 real samples): `churn_score` never exceeded 0.8 (saturation_count=5 means each event = 0.2, so 0.8 = 4 real events, never the full 5) -- `churn_events` capped at `randint(1,4)`, not the original `(1,6)`.

Applied: `bad_u = rng.uniform(0.445, 0.782)`, `bad_delay = rng.uniform(177.8, 697.1)`, `base_loss = rng.uniform(0.182, 0.520)`, `churn_events = rng.randint(1, 4)` (`loss_swing` widened proportionally to `rng.uniform(0.02, 0.15)` -- not independently grounded, no matching real per-trial-swing statistic exists in this project's data, documented as a judgment call rather than overclaimed as measured).

**A large, unplanned consequence found immediately, not smoothed over**: the more severe, real-grounded ranges make Path A/B/C much more clearly differentiated than before. Re-checked the "73% zero-regret base rate" claim the module's own docstring makes (item 15's headline finding, later re-cited through items 16-18) against the new ranges with a 300-point random-weight sample: **zero-regret rate is now 0.3%, not ~70%.** Regret at the production-like weight point jumped from the pilot's 0.00889 to 0.12729 under the corrected ranges. **This means every specific numeric claim from items 15-18** (the 73%→68% zero-regret shrinkage, production's headline "0.0325 regret," DIRECT's "0.108 mean cumulative regret vs BO's 0.847," the Pareto/boundary-ratio numbers) **was computed against the old, insufficiently-severe benchmark and is now stale** -- not wrong as a *method* (the DIRECT-vs-BO/SA algorithm-choice conclusion should still hold, since it's about search efficiency on a piecewise-constant landscape, a structural property independent of the exact severity ranges), but the specific quoted numbers no longer describe the current benchmark. Full pipeline re-run (`python3 -m experiments.weight_search_comparison`, NUM_TRIALS=250 + corrected ranges: grid/DIRECT/BO/SA + 50-seed multiseed + 50-instance multi-comparison + 20-instance out-of-sample validation) launched in the background given the ~55-65 minute estimated runtime (250-trial objective calls cost ~0.30-0.37s each, measured directly, against roughly 11,000+ total evaluations across the full pipeline) -- underestimated: the single 729-eval headline run's Bayesian-optimization phase refits a Gaussian Process (Cholesky, O(n^3)) over all points seen so far every iteration, growing to n=729, which is superlinear and wasn't accounted for in the original estimate. Actual runtime: ~87 minutes. 124/124 tests pass; the change is isolated to `weight_search_comparison.py`, no production code touched.

**Results, now complete**:

- **`fraction_zero_regret_in_500_random_weight_vectors`: 0.006 (0.6%)** -- confirms the quick 300-sample check during development (0.3%), consistent. The old ~70% zero-regret plateau (items 15-18) is gone under the real-grounded ranges; this is now a genuinely hard benchmark, not one where most random weight vectors tie.
- **`current_production_defaults_regret`: 0.127292** (single fixed-instance) / **0.129751** (mean over the 20-instance out-of-sample holdout, essentially identical -- stable, not a fluke of one instance draw). This is roughly **4x item 15's old headline number (0.0325)**. Production's actual weights are now shown to be meaningfully, not marginally, suboptimal under a benchmark grounded in this project's own real measured congestion severity -- items 15-18's "production shows real but modest regret" framing needs updating to "real and substantial."
- **OAT sensitivity re-ranked**: gamma (loss weight) is now the single largest-impact term (range 0.519), ahead of alpha (0.374) and delta (0.301) -- a change from the old benchmark's alpha/delta-dominant ranking, directly traceable to loss severity being re-grounded roughly an order of magnitude higher than the old hand-picked range.
- **`ground_truth_winner_counts`: A=178, B=72, C=0** (of 250 trials) -- Path C (the churn/jitter "trap") never wins under ground-truth weights in this run, unlike the old benchmark where it contributed real contested cases. Worth a follow-up look (not done this session) at whether `loss_swing`'s un-grounded judgment-call range pushed Path C's cost uniformly too high, or whether this is a legitimate consequence of grounding loss severity in real (quite severe) congestion data.
- **The single-headline-run "DIRECT's best point is the exact box-center (0.5 each)" artifact from item 18 reproduces identically** under the new benchmark (`grid_search`/`direct` both again land on all-0.5, `best_regret: 0.0`) -- confirms this is a structural property of `scipy.optimize.direct`'s mandatory first evaluation plus the ground truth's own equal-weighting construction, not something the old benchmark's easiness caused. Item 18's Pareto/boundary-ratio analysis (`experiments/pareto_weight_analysis.py`) was built against the *old* severity ranges and has not been re-run against these -- a legitimate next step, not done this session.
- **Multi-instance comparison (the rigorous one, 50 independently-drawn problem instances, avoids the box-center artifact): DIRECT still decisively wins.** Mean cumulative regret -- direct 0.280, bayesian_optimization 0.930, simulated_annealing 0.740, random_search 0.663. direct-vs-bo and direct-vs-random both show complete separation (Cliff's delta = -1.0, U=0.0, p=0.0001); direct-vs-sa delta=-0.76 (p=0.0001). **The core items-16-18 algorithm-choice conclusion (DIRECT beats BO/SA/random) is confirmed to still hold under the corrected, harder benchmark** -- it did not depend on the old benchmark's easiness.
- **New, updated finding**: bo-vs-sa is now **significant** (U=699, p=0.0003, Cliff's delta=+0.44, SA the better of the two) -- a change from item 16's original finding under the old benchmark ("not significant," U=422, p=0.68). Under the harder, real-grounded benchmark, simulated annealing measurably outperforms Bayesian optimization; they were statistically indistinguishable before.
- **Out-of-sample validation (20 genuinely unseen holdout instances)**: direct/grid_search mean regret 0.0, bayesian_optimization 1.4e-05, simulated_annealing 3e-06 -- all four search methods generalize with essentially zero regret on unseen instances, no overfitting. Production defaults' 0.129751 mean regret on this same holdout set confirms the gap is real and out-of-sample, not an artifact of the specific instances used to compute the single-point number above.

**Net assessment**: the benchmark-hardening work was worth doing -- it did not just make numbers bigger, it changed which findings are still true. The *algorithm-choice* conclusion (use DIRECT) survives unchanged and is now validated on a harder, more realistic benchmark. The *specific regret magnitude* for production's current weights was previously understated by roughly 4x. The *Pareto/boundary-ratio analysis* for what specific weight values to recommend is now stale and would need re-running against these ranges before being cited again.

## scenario_performance_objective.py: the real-scenario DIRECT search, finally completed — one clean negative result, one real win found (2026-08-20)

This was the user's original 5-step plan from earlier this session, deferred for a long stretch while the churn-adaptive, offered-load, and persistence bugs got found and fixed first. Completed now, directly prompted by an explicit ask to (a) finish the DIRECT search and (b) find *some* dimension where `proposed` genuinely beats `dynamic`.

**Sanity check, re-run with all this session's fixes in place**: `current_production_defaults: -0.8973`, and every other hand-picked candidate (including `alpha_dominant`, expected to look good) also negative. All four candidates in `_sanity_check()` show `proposed` worse than `dynamic` on the objective (mean relative improvement across delay/throughput/loss, real congestion.py + failure_recovery.py runs).

**DIRECT search** (`scipy.optimize.direct`, 300-eval budget, ~212s): converged to the same box-center artifact already documented in item 18 (all weights = 0.5) -- meaning across the whole 6-D space explored, **no weight vector found improves on the search-box center**, and even that best point scores `-0.6741`. **Out-of-sample validation** (holdout seeds [11,12,13], not used during the search): `-0.6602` -- consistent with training, not an overfit fluke. **Per-metric breakdown at the best-found weights**: `proposed` is worse than `dynamic` on all three of delay (89.56 vs 49.48ms), throughput (10.66 vs 11.78Mbps), and loss (0.0027 vs 0.0012) individually, not just in aggregate.

**Conclusion, stated plainly**: weight tuning alone cannot make `proposed` beat `dynamic` on delay/throughput/loss in these real scenarios. This matches and generalizes the reroute-mechanism findings earlier this session -- the gap is driven by `persistence_required_samples`'s reaction lag (a timing mechanism), not by the cost-formula weights (which only affect *which* candidate gets chosen once the gates already opened, not *when* they open). No search budget or weight vector can close a gap that isn't caused by the thing being searched.

**The real win, found by asking a different question**: rather than raw outcome metrics, checked reroute *operational cost* -- how many OpenFlow rule installs each algorithm actually causes. First pass (summing `flow_updates`/`reroute` directly from the CSV) looked dramatic (`proposed` 48 vs `dynamic` 85.33 mean flow-updates/run) but turned out to be inflated 4x for both algorithms identically: `congestion.py`/`failure_recovery.py` write one CSV row per flow per sample (4 flows), all sharing the same per-sample decision, so naively summing overcounts by the flow count. Caught this before reporting it, corrected by filtering to one representative flow (`flow-video-1`, this project's existing PRIMARY_PAIR convention) for a true per-run count.

**Corrected, real result** (production defaults, holdout seeds, `operational_cost_report()` added to the module): 
- `CongestionScenario`: `proposed` 0 reroutes / 0 flow-updates per run vs `dynamic` 2 reroutes / 16 flow-updates per run.
- `FailureRecoveryScenario`: `proposed` 2 reroutes / 16 flow-updates per run vs `dynamic` 3.33 reroutes / 26.67 flow-updates per run.

`dynamic`'s extra reroutes are not spurious flapping (already established this session that this specific baseline structurally cannot flap) -- they come from reacting independently, with zero caution, to *every* phase/case a scenario presents (congestion.py's temporary-spike phase and sustained phase are two separate opportunities to react; failure_recovery.py's unstable case gives a third). `proposed`'s stability gates (persistence, hysteresis, min-improvement, hold-down) correctly suppress reacting to some of these -- fewer real OpenFlow rule installs for a comparable outcome, a genuine and mechanistically-understood win, not an artifact.

`decision_time_ms` was also checked as a candidate "proposed wins" metric but rejected as too noisy to trust: `proposed` was *faster* in `FailureRecoveryScenario` (0.71ms vs 1.77ms) but *slower* in `CongestionScenario` (2.32ms vs 1.77ms) -- inconsistent direction, almost certainly dominated by Python-level measurement noise at sub-millisecond scale rather than a real signal, not reported as a finding.

124/124 tests pass; `operational_cost_report()` added to `scenario_performance_objective.py` as a reusable, documented function (not a one-off script), with the flow-deduplication issue explained directly in its docstring so it isn't rediscovered as a surprise later.

## Closing the reaction-lag gap: severity-scaling rejected, traffic-class-immediate policy adopted, safety net tried and reverted (2026-08-20)

Directly following `scenario_performance_objective.py`'s clean negative result (weight tuning cannot close the delay gap -- it's a timing mechanism, not a cost-formula effect), tried the two previously-flagged-but-deferred directions ("direction 1": severity-scaled persistence; "direction 2": traffic-class-aware persistence) to attack the timing mechanism itself.

**Severity-scaled persistence credit (direction 1) -- implemented, verified working, then reverted.** Added `PersistenceChecker.record_violation(credit=...)` and `DecisionEngine._severity_credit()`, scaling how many violation-units one sample contributes based on `ThresholdViolation.severity` normalized against the threshold's own headroom (reused `required_samples` as the only reference, no new constant). Verified real effect: closed part of the delay gap (tier1 170.89ms -> ~106ms). **But found a real, disqualifying cost**: `congestion.py`'s "temporary" phase (a 2-sample transient blip, meant to be filtered) now also triggered a reroute, because it uses the *identical* `SPIKE_UTILIZATION=0.88` as the "sustained" phase -- severity alone cannot distinguish "brief" from "sustained" at decision time, since both look identical for as long as the brief one lasts. This is an information-theoretic limit, not a tuning problem: any signal derived only from samples-seen-so-far (severity, or a trend/derivative -- checked directly, both phases jump to peak with zero ramp, so trend is identical between them too) cannot predict whether a violation is about to end. **User chose to revert this direction entirely** rather than accept the risk. Reverted cleanly: `credit` parameter removed from `PersistenceChecker`, `_severity_credit()` deleted from `DecisionEngine`, test fixtures restored. 124/124 tests pass.

**Traffic-class-aware persistence (direction 2) -- adopted, real and substantial win.** `config/policies.yaml` already marks VoIP/Video as `reroute_immediate: true`, but this only ever applied via `evaluate_service_congestion` (the `priority_policy.py` scenario) -- every other scenario's monitored flow (`flow-video-1`, service_type "Video") got no benefit from its own already-configured policy. Threaded `service_type` through `ProposedDriver` -> `evaluate_pair` -> `DecisionEngine.traffic_policy.should_reroute_immediately()`, wired into `congestion.py` (the flow's `service_type` looked up once, passed into `make_drivers()`). **Result, verified on the 4-pair robustness check: tier1/tier2/tier3 now match `dynamic`'s delay *exactly*** (e.g. tier1 41.35ms vs 41.35ms, down from 170.89ms) -- persistence's entire reaction-lag disadvantage closed for these three pairs, because skipping persistence (not just accelerating it) lets `proposed` react on the same sample `dynamic` does, once hysteresis+min-improvement also clear (which they already did, just later). PRIMARY_PAIR unaffected (268.30ms, 0.40 reroute rate) -- its bottleneck is the min-improvement gate itself (no shared edges with current_path), not persistence timing, so this fix doesn't reach it, consistent with everything already known about that pair.

**Same cost re-appears from this direction alone, confirmed precisely.** Checked whether `reroute_immediate` alone (severity-scaling already reverted) still triggers the temporary-phase false positive: yes, 1/1 -- because `reroute_immediate` doesn't accelerate persistence, it *skips it entirely*, so even a single sample is enough. This is not new: it is the design `policies.yaml` already specifies for high-priority classes (accept more reactivity, including to possibly-brief problems, in exchange for faster response for latency-sensitive traffic) -- simply never exercised against a scenario built specifically to test the brief-vs-sustained distinction before.

**"Safety net" attempted to reduce (not eliminate) this cost -- two iterations, both net negative or neutral, reverted.** Idea: when persistence is skipped, require a *stricter* min-improvement margin than normal, so an un-verified fast reaction is at least a decisive one.
- Attempt 1: force the full, unrelaxed ceiling (`absolute_cost_reduction`/`relative_cost_reduction`, ignoring the churn-adaptive floor) whenever `skip_persistence` is true. Checked against the real 4-pair robustness check: **too strict** -- blocked tier1/tier2's genuine (if modest) sustained-congestion improvements too, reducing `proposed` to `static`'s behavior in both (429.98ms/426.02ms, 0 reroutes -- the delay-gap win from direction 2 was undone for those two pairs).
- Attempt 2: floor at the midpoint (0.5) of the churn-adaptive floor/ceiling range, taken as the max against the link's real churn-adaptive value (a churny link still gets its real, possibly-stricter value; a churn-free link gets lifted to the midpoint instead of the permissive floor). Checked precisely against tier1's real numbers: its candidate's real relative improvement is 7.46% -- above the floor (5%) but below the midpoint (10%) -- so the midpoint still rejected it, identically to attempt 1's result.
- **Root cause, confirmed not assumed**: tier1's real improvement (7.46%) sits in a range that is inherently ambiguous -- genuinely real but modest, indistinguishable in magnitude from what a noise-driven margin could also produce. No single threshold between the floor and ceiling can separate "block noise" from "keep real-but-modest improvements," because their value ranges overlap. This is the same information-theoretic wall as severity-scaling's failure, expressed on the cost-margin axis instead of the sample-count axis.
- **Decision (user's explicit call after seeing both attempts fail)**: revert the safety net entirely, keep bare `service_type`/`reroute_immediate` with no additional margin requirement. Reverted `_execute_reroute`'s `skip_persistence` parameter and all associated logic back to its pre-this-section form. 124/124 tests pass; 4-pair check reproduces the direction-2-only numbers exactly (tier1-3 match `dynamic`, PRIMARY_PAIR at 268.30ms/0.40).

**Net conclusion of this whole thread**: the reaction-lag gap is now closed for 3/4 objectively-selected pairs via the already-existing, already-configured `reroute_immediate` policy (not a new mechanism -- just correctly wired up outside `priority_policy.py` for the first time), at an explicit, disclosed, and now well-understood cost (loses noise-rejection for this traffic class specifically, matching that policy's own designed trade-off). No further mitigation of that cost was found to be possible without new data this project doesn't have (a real congestion-duration distribution, to ground an actual probabilistic accept/reject decision -- flagged as a genuine open item, not fabricated here). PRIMARY_PAIR's gap remains open, root-caused to a different, already-documented mechanism (min-improvement, not persistence timing).

## Directions 3+4 converge: recovery switchback also blocked by the offered-load correction, not the recovery window (2026-08-20)

Tried "direction 3" (re-examine `RecoveryManager`'s recovery-window tuning) and "direction 4" (special-case min-improvement for candidates sharing zero edges with `current_path`, e.g. PRIMARY_PAIR) together, since they turned out to be the same root cause found from two different starting points.

**Direction 3, instrumented precisely, found the window itself is not the problem.** Traced `failure_recovery.py`'s "stable" case for PRIMARY_PAIR sample-by-sample: `RecoveryManager.is_eligible_for_switchback` correctly becomes eligible at sample 11 (recovery starts at sample 8/t=16s, `recovery_window_seconds=5.0` -> eligible at t>=21s, sample 11's t=22s) -- but `proposed` never actually switched back within the scenario's 14-sample window (confirmed: `switched_back` stays `False` from sample 4 through 14, before any fix). The window duration is fine; something else was rejecting the switchback after eligibility was confirmed.

**Root cause, found by direct instrumentation, not assumption**: `evaluate_recovery_switchback` was applying the same `offered_load_mbps` self-influence correction as any other candidate. For this case, `original_path` (`['2','32','34','7']`) shares zero edges with `current_path` (`['2','4','6','7']`) -- exactly the same "no shared edges" structural case already documented for regular congestion reroutes on this pair. Computed precisely: `old_cost=0.2609` vs `original_path`'s true cost `0.2597` -- a real, if modest, 0.46% improvement -- ballooned to `0.4772` once the correction applied to all 3 of its edges. `is_eligible_for_switchback` becoming `True` at sample 11 was real; `complete_recovery()` (called immediately upon eligibility, before checking whether `_execute_reroute` actually accepts the candidate) tore down the recovery-tracking state regardless -- so the window fired correctly, and the switchback was then separately rejected by cost.

**Fix, framed and risk-checked before implementing**: `evaluate_recovery_switchback` no longer applies the offered-load correction at all. Rationale: that correction exists to stop a path that has *never* carried this flow from looking artificially cheap; `original_path` is not that -- it is specifically the path this exact flow was already routed on before the failure, real historical evidence it can carry this load. User asked directly what real risk this trades away before agreeing to implement it: if background conditions on `original_path`'s edges genuinely worsened *during* the outage (e.g. other traffic migrated there), removing the correction could let a switchback proceed into a now-actually-bad path -- explained precisely why this risk is narrower than it first sounds: real-time utilization stats (which already reflect any such worsening) are untouched by this change and still drive the base cost comparison; the *only* thing removed is the additional "would adding this flow's own future load also push it over" margin, which matters only in the narrower edge case of a path sitting right at a tipping point -- and original_path has direct precedent (it carried this exact flow before) that a genuinely new candidate wouldn't have.

**Verified across 5 seeds, not asserted**: "stable" case -- 5/5 seeds now genuinely switch back (all at sample 11, the window's own earliest-eligible moment, confirming this isn't newly-too-permissive, just newly-correct); before the fix, 0/5 ever switched back within the scenario's window. "unstable" case -- 3/5 switch back (at sample 27), 2/5 don't -- real, seed-dependent variation (different background draws genuinely produce different real margins), not a new defect: `emergency=True`'s unconditional `new_cost < old_cost` floor was never bypassed by this change, so a switchback into a genuinely worse path remains impossible regardless. 124/124 tests pass (`experiments/simulation_common.py`'s call site updated to match the removed parameter).

## Revoking the offered-load correction (2026-08-20)

Following the direction-3+4 fix above (removing the correction from `evaluate_recovery_switchback` specifically), asked directly whether disabling it more broadly -- across regular congestion-driven reroutes too, not just recovery switchback -- would help, with a full ablation table as evidence before deciding.

**Ablation, run properly**: all 4 objectively-selected pairs, both sustained (single-spike) and chronic-intermittent (2-on/1-off) congestion, 10 seeds each, `offered_load_mbps` set vs left unset on `drivers["proposed"]`:

| scenario | pair | proposed, WITH correction | proposed, WITHOUT correction | dynamic |
|---|---|---|---|---|
| sustained | tier1 | 41.35ms, 1.0 reroutes | 41.35ms, 1.0 | 41.35ms, 1.0 |
| sustained | tier2 | 40.60ms, 1.0 | 40.60ms, 1.0 | 40.60ms, 1.0 |
| sustained | tier3 | 28.78ms, 1.0 | 28.78ms, 1.0 | 28.78ms, 1.0 |
| sustained | **PRIMARY_PAIR** | **268.30ms, 0.4** | **29.48ms, 1.0** | 29.48ms, 1.0 |
| oscillation | tier1 | 41.35ms, 1.0 | 41.35ms, 1.0 | 41.35ms, 1.0 |
| oscillation | tier2 | 40.60ms, 1.0 | 40.60ms, 1.0 | 40.60ms, 1.0 |
| oscillation | tier3 | 28.78ms, 1.0 | 28.78ms, 1.0 | 28.78ms, 1.0 |
| oscillation | **PRIMARY_PAIR** | **206.98ms, 0.4** | **29.48ms, 1.0** | 29.48ms, 1.0 |

tier1-3 are unchanged (the correction was never the bottleneck there). PRIMARY_PAIR -- the one pair that never closed the gap to `dynamic` through any earlier fix this session (double-counting, per-edge capacity, exclude-shared-edges, severity-scaling, service_type/reroute_immediate, the two safety-net attempts) -- **now matches `dynamic` exactly, in both scenario types**. This is the same root cause as the recovery-switchback fix: PRIMARY_PAIR's winning candidate shares zero edges with `current_path`, so the correction's per-edge penalty applies to the whole route and swamps a real, if modest, improvement.

**A real, disclosed, still-open risk was surfaced before deciding, not glossed over**: `tests/path_cost.py::test_offered_load_can_flip_the_accept_decision` demonstrates a synthetic case where the correction is *necessary* -- a candidate that looks acceptable at its current (uncontributed) utilization but would not actually be an improvement once this flow's own load lands on it. Disabling the correction in real scenario scripts reopens exactly that risk in principle. It did not materialize in the 4 pairs / 2 scenario types tested here (real, measured delay after disabling matches `dynamic`'s -- not secretly worse), but that is evidence the risk didn't hit *these* cases, not proof it can never occur.

**Decision (user's explicit call after seeing the full ablation table and the disclosed risk)**: disable the correction at the *scenario* level, not delete the mechanism. `PathCost.calculate_path_cost`/`is_improvement`/`compare_paths`'s `offered_load_mbps` parameter, `DecisionEngine`'s threading of it, and every test in `tests/path_cost.py` that exercises it (including the risk-demonstrating one) are left fully in place and passing -- the mechanism remains correct, tested code, just not invoked by default. All 5 scenario scripts (`congestion.py`, `failure_recovery.py`, `stale_stats.py`, `increasing_load.py`, `priority_policy.py`) no longer set `offered_load_mbps`/pass it into `evaluate_service_congestion`, each with a comment pointing back here. Dead `primary_flow`/`offered_load_mbps` dict variables that had no remaining use after this were removed (`failure_recovery.py`, `stale_stats.py`, `increasing_load.py`, `priority_policy.py`). 124/124 tests pass; all 5 scenario scripts smoke-tested clean.

**Re-enabling this later**: if a real case is ever found where a candidate's own future load genuinely would have made it worse (the risk this trades away), the fix is to set `driver.offered_load_mbps` (or pass it into `evaluate_service_congestion`) again in the specific scenario -- the underlying machinery was never removed, only its default invocation.

## Node-pair generalization: 17 objectively-selected pairs, clean 17/17 (2026-08-20)

The last open item from this session's reroute-mechanism work. Blocked from validating the offered-load-correction risk on real Mininet data (this VM has `mn`/`ovs-vsctl` installed but no passwordless sudo, and real network-namespace/OVS operations require root -- an environmental constraint, not something to bypass; a real Mininet experiment script extending `scripts/mininet_failure_recovery_demo.py` was drafted instead, for the user to run with `sudo` themselves), so generalization was tackled in the meantime since it needs no elevated privileges.

**Selection, pre-registered before any outcome was seen** (`select_generalization_pairs.py`): same qualifying criterion as the original 4-pair check (3-5 hop shortest path, at least one genuine alternative route), stratified by the hotspot edge's background tier and spread across hop counts via even-index sampling within each tier bucket -- 8 tier1 + 2 tier2 (tier2 only has 6 real-diversity edges total, so its pool of qualifying pairs is inherently small) + 6 tier3, plus `PRIMARY_PAIR` = 17 total.

**Result, using the final mechanism state this whole session converged on** (leaky-bucket persistence, no-double-counting per-edge offered-load available but not invoked, `service_type="Video"` wired via `reroute_immediate`): **17/17 pairs match `dynamic`'s delay exactly (0.0% difference) in both sustained (single-spike) and chronic-intermittent (2-on/1-off) congestion.** 14/17 pairs show both algorithms genuinely rerouting to the identical real candidate path; 3/17 (`11->21`, `18->36`, `20->29`, all tier3) show both algorithms correctly agreeing there is no better path at all (0 reroutes each, both stuck on the same congested path) -- a different but equally valid form of agreement, not silently conflated with the successful-reroute cases in the reported count. Spot-checked directly (not just trusted the aggregate) that this isn't a script artifact: `static` genuinely differs (429.96ms, stuck on the original path) while `dynamic`/`proposed` converge on the identical real alternative path and identical delay.

**What this closes**: PRIMARY_PAIR was, until this session's offered-load-correction revocation, the one pair that never fully closed its gap to `dynamic` despite every earlier fix. This generalization check confirms that fix (and the whole stack of fixes underneath it -- leaky-bucket persistence, per-edge no-double-counting offered-load machinery, `reroute_immediate` wiring) is not a PRIMARY_PAIR-specific patch: it holds across a real, pre-registered, tier-diverse sample of the topology, not just the 4 pairs used to develop it.

**What remains genuinely open**: this still only tests the delay dimension on hotspot-triggered congestion; it does not test the theoretical offered-load risk (a candidate that looks fine until its own future load lands on it) under a real, dynamically-changing background -- that requires the pending real Mininet experiment (drafted, blocked on sudo access) -- and it does not test failure/recovery-switchback generalization beyond PRIMARY_PAIR specifically (the 16 new pairs were only run through the congestion scenario, not failure_recovery.py).

## Real Mininet offered-load risk experiment: drafted, self-reviewed, blocked on sudo (2026-08-20)

`scripts/mininet_offered_load_recovery_check.py` -- tests the one thing this session's offline-harness generalization structurally cannot: whether the offered-load correction's caution (revoked this session, see above) actually pays off once background traffic genuinely worsens on the recovery-switchback candidate *during* a real outage, rather than only in the offline harness's static, never-updated background.

Design: same PRIMARY_PAIR first-hop failure as `mininet_failure_recovery_demo.py`; during the outage, injects a REAL iperf flow directly between the hosts at two of `original_path`'s other (non-failed) nodes -- every GEANT node has its own host, so this crosses the exact real switch-to-switch link the switchback candidate depends on, not a proxy; measures real utilization there via two `StatisticsCollector.parse_ovs_port_stats`/`calculate_rates` polls a few seconds apart (real byte-counter deltas, not simulated); then compares `PathCost.compare_paths` on the real switchback decision with `offered_load_mbps` on vs off, to see whether the correction would have caught the injected degradation.

Cannot be run in this session: this VM has `mn`/`ovs-vsctl` installed but no passwordless sudo, and real network-namespace/OVS operations require root -- a genuine environmental constraint, not something to bypass. Left for the user to run themselves (`sudo python3 scripts/mininet_offered_load_recovery_check.py`) and report results back.

**Self-reviewed and fixed two real bugs before handing it off, not just written once and left**:
1. The injected background iperf flow's own host-to-host path had no OpenFlow rules installed (`failMode=secure` with no controller drops anything unrecognized) -- the background traffic would have been silently dropped, producing a real measured utilization of ~0 regardless of anything actually being tested. Fixed by calling the same `install_path_rules` helper for that 2-node segment.
2. `StatisticsCollector.calculate_utilization` takes one already-rate-populated `PortStatistics`, not two raw snapshots plus a time gap (the API actually requires calling `calculate_rates` twice, since it keeps its own previous-sample state internally keyed by switch+port) -- the first draft called it directly with two raw stats objects and a gap, which doesn't match the real signature. Fixed to call `parse_ovs_port_stats` + `calculate_rates` twice, with the real sleep between them, then `calculate_utilization` on the second (rate-populated) result.
3. Only seeding the one specifically-measured background edge and leaving every other edge with no `NetworkState` stats at all would make `PathCost.calculate_path_cost`'s "no data" flat 1.0-per-edge fallback dominate the cost comparison by hop count, not by the real background signal being tested. Fixed by seeding the whole topology with `experiments.simulation_common.build_network_state()` (the same real-data-calibrated baseline the rest of this session's results are grounded in) first, then overwriting only the specific background edge with a real, freshly-measured value.

Syntax-checked (`python3 -m py_compile`) but not functionally run -- the user should review the `NEIGHBOR_A`/`NEIGHBOR_B` (auto-derived from `original_path`, adjustable if a different SRC/DST pair is used) and the iperf/timing constants before running.

**First real run, executed by the user (2026-08-20)**: succeeded end to end on real Mininet/OVS -- the script itself works. Result at the single fixed rate tested (24 Mbps, matching `FLOW_MBPS`): `without correction: accepted=False old_cost=0.236009 new_cost=0.267531`; `with correction: accepted=False old_cost=0.236009 new_cost=0.454504`. Both correctly reject the switchback -- the injected background alone (13.4% real degradation) was already enough to make `original_path` look worse than staying, without any help from the correction. A real, useful result (confirms background genuinely can shift enough on a real network to matter, unlike the offline harness), but not the informative boundary case (correction changing the outcome).

**Rewritten as a rate sweep** (`BACKGROUND_RATES_MBPS = [4, 8, 12, 16, 20]`, one Mininet session, clean `pkill`+cooldown between rates) instead of one fixed rate, so the boundary -- if one exists for this pair/edge -- can be found without the user manually re-running the whole ~1-2 minute Mininet startup/teardown cycle per rate. Also separated the injected background flow's rate from `FLOW_MBPS` (the flow *being evaluated* for switchback, used only in `offered_load_mbps`) -- these were conflated in the first version, reusing one constant for two conceptually different flows. Prints a per-rate table and flags any rate where `without_accepted != with_accepted`. Syntax-checked; the sweep version itself has not yet been run (pending the user's next `sudo` run).

## Re-enabling the offered-load correction for recovery switchback (2026-08-20)

The user ran `scripts/mininet_offered_load_recovery_check.py`'s rate sweep for real. **Result -- the theoretical risk this session's "Revoking the offered-load correction" section disclosed is real, not hypothetical**:

| rate (Mbps) | real measured utilization | without correction | with correction |
|---|---|---|---|
| 4 | 0.0432 | accepted=**True** | accepted=**False** |
| 8 | 0.0864 | accepted=**True** | accepted=**False** |
| 12 | 0.1294 | accepted=**True** | accepted=**False** |
| 16 | 0.1725 | accepted=**True** | accepted=**False** |
| 20 | 0.2158 | accepted=False | accepted=False |

At every rate from 4-16 Mbps, the *uncorrected* decision would have switched `proposed` back onto a path whose background had genuinely, measurably worsened (real iperf traffic, real `ovs-ofctl dump-ports` counters) -- exactly the failure mode the correction exists to prevent. The corrected decision stayed rejected at every one of those rates. Only at 20 Mbps did the injected background become severe enough that the uncorrected comparison also rejected it on its own (matching the earlier single-rate 24 Mbps run).

**This directly reopens the "Revoking the offered-load correction" decision for recovery switchback specifically** (the broader revocation for regular congestion-driven reroutes, e.g. `congestion.py`'s PRIMARY_PAIR fix, is a separate case -- not recovery switchback, not retested by this Mininet experiment, left unchanged). **User's decision: re-enable it for recovery switchback.** `evaluate_recovery_switchback` accepts `offered_load_mbps` again; `experiments/simulation_common.py`'s `ProposedDriver.step()` passes it through again; `failure_recovery.py` (the only scenario script that exercises this code path) sets `proposed.offered_load_mbps` again, with a comment explaining why it differs from `congestion.py`/`stale_stats.py`/`increasing_load.py` (which stay disabled -- they never reach `evaluate_recovery_switchback` at all).

**The trade-off this re-introduces is real and was verified, not glossed over**: the two findings are in tension because the correction cannot distinguish "this candidate's margin shrank because background genuinely changed" from "this candidate's margin was always this small, and the correction's own self-load estimate is what tips it" -- both produce the identical symptom (rejection). Re-checked directly: `failure_recovery.py`'s "stable" case switchback (PRIMARY_PAIR, the item-31 case) is back to 0/5 seeds completing within the scenario window -- the offline harness's static, never-updated background genuinely cannot produce enough real margin to survive the correction now that it's back, the same limitation already identified when this whole thread started. `congestion.py`'s PRIMARY_PAIR sustained-congestion result (52.59ms, matching `dynamic` exactly) is unaffected, since that scenario never sets `offered_load_mbps` and never touches recovery switchback.

**Net position**: this correction is now enabled exactly where a real experiment demonstrated it prevents a real failure (recovery switchback, background can genuinely shift during a real outage), and disabled exactly where a real ablation demonstrated no such failure and a real, measurable win (regular congestion-driven reroutes, PRIMARY_PAIR's gap to `dynamic`). Not a blanket policy either way -- the two scenarios were tested separately and reached different, evidence-backed conclusions. 124/124 tests pass; all 5 scenario scripts smoke-tested clean.

## Failure/recovery node-pair generalization: 23 pairs, proposed beats both baselines on delay and reroute cost -- low switchback rate is not a defect (2026-08-24)

Closes the one open item the 2026-08-20 "Node-pair generalization" section
explicitly flagged as untested: recovery-switchback behavior had only ever
been checked on PRIMARY_PAIR, never on a broader, pre-registered sample of
node pairs the way `congestion.py`'s 17-pair check was.

**Note on session continuity**: the original congestion-side pair-selection
script (`select_generalization_pairs.py`) and the memory file tracking
this and other open items were both found missing at the start of this
session (2026-08-24) -- neither is recoverable. `experiments/
failure_recovery_generalization.py` (new) is a fresh implementation of the
same *documented* selection criterion (3-5 hop shortest path, at least one
genuine alternative route confirmed by removing the shortest path's edges
and checking connectivity survives, stratified 8/2/6 across the pair's
first-hop edge's SNDlib background tier), not a recovery of the original
17 pairs -- the specific pairs differ from the original congestion-side
run by construction (fresh, independent draw), but the sampling method is
identical and pre-registered (deterministic, no seed-dependent reshuffling,
computed before any scenario was run).

**Sampling-range check, done before drawing any conclusions from it**: the
3-5 hop criterion (inherited unmodified from `congestion.py`'s original
check, matching PRIMARY_PAIR's own 4 hops) covers 481/780 = 61.7% of all
node pairs in the real 40-node/61-edge GEANT topology and is the single
largest hop-count bucket (3 hops: 25.6% of all pairs) -- a defensible
"typical case" range, not a narrow or cherry-picked one. It does exclude
the short end (1-2 hops, 27% of pairs, 83-87% alternative-route
availability -- the "easy" cases) and the long tail (6-8 hops, 11.4% of
pairs, availability collapsing to 29%/15%/0% as hop count rises -- the
"hard" cases, with a genuinely thin qualifying pool: only 3 pairs total
qualify at 7 hops, 0 at 8). Added a 6-pair boundary supplement (3 pairs
each from the 1-2 hop and 6-7 hop ranges, same deterministic even-index
selection, preferring the rarer end of each range first) to check whether
the core finding holds at the edges rather than asserting it does.

**Method**: 23 pairs total (17 core 3-5-hop pairs incl. PRIMARY_PAIR + 6
boundary pairs), both `failure_recovery.py` cases ("stable" restoration,
"unstable" flap-then-settle), 5 deterministic seeds each. Originally
scored only on switchback agreement (does `proposed` reach the same
end-of-scenario path as `dynamic`) -- **extended after that metric alone
gave a misleadingly negative picture** to also record each algorithm's
mean delay and reroute count across the post-failure window (matching the
"congested-window-only" averaging convention used elsewhere in this log),
against `static` as the do-nothing reference.

**Result: switchback agreement is genuinely low, but delay and reroute
cost -- the metrics that actually matter -- decisively favor `proposed`,
across every one of the 23 pairs including both boundary groups.** Full
(5/5-seed) switchback agreement in only 4/23 pairs (stable) and 3/23
(unstable). But mean delay across all 23 pairs: `static` 86.8/85.8ms,
`dynamic` 76.9/75.3ms, **`proposed` 46.6/46.1ms** (stable/unstable) --
`proposed` beats `dynamic` by ~40% and `static` by ~46% on every single
pair, not just on average (spot-checked the per-pair table, not just the
mean). Mean reroutes/run: `dynamic` 1.93/3.79, `proposed` 1.22/1.22 --
`proposed` uses a third to a fifth as many OpenFlow rule installs,
especially in the `unstable` case where `dynamic` reacts to every flap
independently. Boundary pairs reproduce the same pattern (e.g. `7->9`,
1 hop: delay static/dynamic/proposed = 77.4/66.5/20.6ms; `13->33`, 6 hops:
103.5/95.2/61.3ms) -- the win holds at both edges of the sampled range,
not just in the 3-5-hop core.

**Reinterpreting the low switchback rate, not just reporting it**: a low
rate does not mean `proposed` fails to recover -- it means `proposed`
often stays on the (already-rerouted, already cost-cheaper) path it found
during the outage instead of blindly returning to the pre-failure path
once it's restored, whereas `dynamic` always returns unconditionally. The
delay numbers show this is usually the right call: `dynamic`'s mean delay
after switching back (Dynamic column above) is *worse* than `proposed`'s
after declining to, on literally every pair tested. The offered-load
correction's real job here -- block a switchback into a path that
isn't actually better -- is doing exactly what it's supposed to; a lower
switchback rate is this mechanism working as designed, not the
already-disclosed static-background limitation misfiring across the
topology (that limitation is real and still worth noting -- see below --
but it does not make this an outcome regression).

**What the static-background limitation (disclosed in the "Re-enabling
the offered-load correction..." section above) still means here**: the
offline harness's background never updates after `build_network_state()`'s
one-time stamp, so it's possible some of these 0.00-switchback-rate cases
are pairs where a real, changing network would have given the original
path enough real margin to pass the correction, and this harness simply
can't produce that margin. This check cannot distinguish "correctly
declined a bad switchback" from "harness structurally under-produced the
original path's margin" on a per-pair basis -- both look identical here.
What it *can* say, and does: even under that pessimistic assumption,
outcomes did not suffer -- delay and reroute cost still favor `proposed`
everywhere. The Mininet rate-sweep experiment remains the only tool that
can resolve the per-pair ambiguity on real hardware.

**What this does NOT retest**: this only exercises recovery-switchback
(the case `offered_load_mbps` is set for in `failure_recovery.py`); it
does not touch `congestion.py`'s already-confirmed 17-pair delay-match
result (a separate code path that never sets `offered_load_mbps` and is
unaffected by any of this). Raw per-pair results (including per-pair
group/hop-count labels for the boundary check):
`results/failure_recovery_generalization/summary.csv`.

## Generalizing the remaining three scenarios: stale_stats, increasing_load, priority_policy (2026-08-24)

Prompted by a direct user question ("have the other scenarios been generalized too?"). Answer at the time was no: only `congestion.py` (17-pair, 2026-08-20) and `failure_recovery.py` (23-pair, earlier this session) had ever been checked beyond PRIMARY_PAIR. `stale_stats.py`, `increasing_load.py`, and `priority_policy.py`'s conclusions were all still resting on a single hand-picked pair. Closed for all three in this pass, each via a new `experiments/*_generalization.py` script that reimplements the scenario parameterized by `(src, dst)` (the originals hardcode PRIMARY_PAIR).

**All three reuse the identical 23-pair sample** (`labeled_pairs_23()`, factored out of `failure_recovery_generalization.py`) rather than each drawing its own -- results are directly comparable pair-for-pair across all four generalized scenarios now, not just individually pre-registered.

### `increasing_load_generalization.py` -- generalizes cleanly, but the headline flips: dynamic beats proposed here

Mean delay across all 23 pairs, full 12-sample ramp: `static` 226.6ms, `dynamic` **130.3ms**, `proposed` 194.2ms. Both `dynamic`/`proposed` reroute exactly once per pair (a single clean threshold crossing on a monotonic ramp); `static` never reroutes. **This is the one scenario where `dynamic` outperforms `proposed`, and it generalizes just as cleanly as the wins do** (every one of the 23 pairs shows the same static > proposed > dynamic delay ordering, e.g. `2->7`: 221.9/188.8/123.6ms; `13->33`: 249.1/216.3/151.8ms). Not a contradiction of the failure/recovery finding -- it's the same mechanism cutting the other way: a monotonic, noise-free ramp is exactly the case where `dynamic`'s lack of stability gates (persistence/hysteresis) costs it nothing, since there's no false-positive risk to guard against, so `proposed`'s caution is pure overhead here rather than a hedge that pays off. `proposed` still clearly beats `static` (194.2 vs 226.6ms) in every pair. Raw data: `results/increasing_load_generalization/summary.csv`.

### `stale_stats_generalization.py` -- perfectly clean, zero variance across all 23 pairs

Two phases, both fully generalized with **identical results on every single pair** (not just similar -- exactly identical, e.g. `dynamic_detect_lag_samples=0.0` and `proposed_detect_lag_samples=2.0` on all 23 rows). This is expected, not a bug: both phases inject a synthetic utilization value directly onto whichever edge resolves as the pair's hotspot link, at a fixed magnitude and fixed sample schedule -- the mechanism being tested (does persistence filter a one-sample glitch; does it still detect a real sustained event under delayed polling) depends on that fixed schedule and `persistence_required_samples=3`, not on the specific real background data underneath, so pair-to-pair topology differences have no lever to act through here.

- **Noise phase** (ground truth flat, one sample's *observed* value glitches): `static` 0 false reroutes (never reacts to anything), **`dynamic` 1.0 -- reacts to the glitch every single time, on every pair**, `proposed` 0 -- persistence correctly filters it every time, on every pair.
- **Delayed-detection phase** (real sustained congestion, 2 of the confirming polls report stale data): both `dynamic` and `proposed` reach 100% detection across all 23 pairs (neither ever misses the real event) -- `dynamic` reacts with 0-sample lag (no gate to wait on), `proposed` with a consistent 2-sample lag (the cost of requiring 3 confirming samples when 2 of them are delayed). Zero false reroutes for either during this phase's non-congested tail, on every pair.

**Reading this together**: `dynamic`'s "faster" reaction (0 lag here, and the increasing_load win above) is the same underlying trait as its 100% false-positive rate on noise -- it has no mechanism to distinguish a real event from a glitch, so it is fast and wrong in exactly the case this phase is built to expose. `proposed` trades that lag for a robustness guarantee that held on every one of 23 topologically diverse pairs, not just PRIMARY_PAIR. Raw data: `results/stale_stats_generalization/summary.csv`.

### `priority_policy_generalization.py` -- reroute_immediate ordering holds 23/23

Structurally different from the other three (no static/dynamic baseline at all -- compares the 4 traffic classes' reroute timing against each other under one shared `proposed`-only path). **The core claim -- VoIP/Video (marked `reroute_immediate: true` in `config/policies.yaml`) react no later than Web/File Transfer -- holds in all 23/23 pairs**, not just PRIMARY_PAIR. Mean first-reroute sample across all 23 pairs: VoIP 4.81, Video 4.81 (identical -- both are `reroute_immediate`), Web 8.18, File Transfer 10.00 (File Transfer's higher `qos_threshold` tolerance means it needs the utilization trace to climb further before its own effective threshold is crossed). A few pairs show VoIP/Video reacting later than sample 4 (e.g. `12->25`: all four classes react at sample 10, `1->27`: immediate classes at 5.8-6.3) -- real, pair-dependent variation in exactly when the shared path's cost first crosses each class's threshold, but the *relative ordering* (immediate classes first-or-tied, never after) never breaks. Raw data: `results/priority_policy_generalization/summary.csv`.

### Net position across all four generalized scenarios

| scenario | generalizes? | headline |
|---|---|---|
| `congestion.py` | yes, 17/17 | delay match to `dynamic` exact, every pair |
| `failure_recovery.py` | yes, 23/23 | `proposed` beats both baselines on delay/reroute cost, every pair (switchback rate is a red herring -- see above) |
| `increasing_load.py` | yes, 23/23 | but the winner is `dynamic`, not `proposed` -- a real, generalized trade-off, not a defect |
| `stale_stats.py` | yes, 23/23, zero variance | `proposed` is the only algorithm robust to noise AND still detects real events, on every pair |
| `priority_policy.py` | yes, 23/23 | `reroute_immediate` ordering holds everywhere |

Every scenario this project makes a comparative claim about has now been checked beyond PRIMARY_PAIR. `increasing_load.py` is the one honest counter-example to "`proposed` always wins" -- reported here exactly as found, not smoothed over: `proposed`'s stability mechanism is a deliberate trade of raw ramp-following speed for noise robustness, and which one matters depends on whether the real deployment expects clean monotonic load growth (favors `dynamic`) or noisy/bursty real polling (favors `proposed`, per the `stale_stats` result immediately above).

### Publication-style figures added (2026-08-24)

The bar-chart summaries above are the analysis; two dissertation-ready
figure/table reports were built from the same underlying data, revised
once from an initial bar-chart-only draft to figures with genuinely
quantitative axes (distributions and trajectories, not single collapsed
means) after review:

- **Part 1** (failure/recovery): box plots of the post-failure delay
  distribution across all 23 pairs (stable + unstable), and a
  hop-count-vs-improvement% scatter with an OLS trend line, replacing the
  original mean&plusmn;s.d. bars.
- **Part 2** (the three scenarios above): a delay-vs-offered-load line
  chart for `increasing_load` (12 ramp steps, mean&plusmn;s.d. band across
  23 pairs &times; 5 seeds, showing each algorithm's real reroute point as
  a visible step), two delay-vs-sample line charts for `stale_stats`'
  noise/delayed-detection phases (same convention), and a box plot of
  first-reroute-sample per traffic class for `priority_policy`.

New instrumentation scripts, added specifically to capture the
per-sample/per-seed trajectories the original `*_generalization.py`
scripts collapsed into single means: `experiments/
increasing_load_persample.py` -> `results/increasing_load_generalization/
persample.csv`; `experiments/stale_stats_persample.py` ->
`results/stale_stats_generalization/persample_noise.csv` and
`persample_delayed_detection.csv`. Both reuse `labeled_pairs_23()`, so
they're aggregated over the identical 23-pair sample as everything else
in this section.

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

---

## Final Verdict — 2026-08-24 update

The paragraph above is preserved as-written (it was accurate on 2026-08-11); this
update exists because roughly ten days of further findings — the 23-pair
generalization work for all four remaining scenarios, the independence-check
workstream, the weight-search rework, and an independent scenario design audit
(experiments/, congestion.py excluded by request) covering data-validation and
statistical-rigor questions the sections above didn't ask — accumulated in later
subsections of this same file without the header above ever being revisited. Net
position, current as of this update:

**Still holds:** the core claim above (real code, not hardcoded outcomes; real
Mininet/OVS deployment verified) is unchanged and, if anything, more thoroughly
exercised now — every scenario this project makes a comparative claim about has
since been checked beyond `PRIMARY_PAIR` (17–23 node pairs each; see "Failure/recovery
node-pair generalization" and "Generalizing the remaining three scenarios," both
2026-08-24).

**Found and fixed by the scenario design audit (2026-08-24):**
- `evaluation/calculate_metrics.py`'s 95% CI used a fixed z=1.96 regardless of
  sample size — wrong for the pilot pipeline's actual n=3 (understated every
  reported interval by ~2.2×). Fixed: now looks up a t-critical value by degrees
  of freedom. All 124 tests still pass (one test's own assertion updated to the
  now-correct value).
- `failure_recovery_generalization.py` and `priority_policy_generalization.py`
  collapsed their 5 seeds/pair straight to a mean with no per-seed record kept
  anywhere (the other two generalized scenarios already had this). Fixed: new
  `failure_recovery_perseed.py` / `priority_policy_perseed.py` companions, both
  run end-to-end (real output: `proposed`'s stable-case delay carries a genuine
  ±2.65ms mean within-pair seed standard deviation, n=23 pairs — a number with no
  source file to compute from before this fix).
- The "5 seeds per pair" convention's actual strength varies ~20× by pair (most
  of `build_network_state()`'s background jitter comes from tier-3 fallback
  edges only) — now disclosed directly in `failure_recovery_generalization.py`'s
  docstring (4 of 23 pairs' seeds barely vary the input).
- `priority_policy.py` silently never ran `static`/`dynamic` — now documented in
  its own docstring and at the `algorithms=("proposed",)` default itself, with
  the real reason (`static_shortest_path.py`/`dynamic_baseline.py` have no
  `service_type` concept, confirmed by direct grep, not assumed).

**Found, not fixed — real numeric magnitudes need caution:** every scenario's
`delay_ms`/`throughput_mbps`/`packet_loss` figure passes through two layers —
`congestion_model.py`'s link-level curves (delay: real-fitted with a documented
uncorrected high-u bias; loss: fit attempted and rejected) plus a second,
never-discussed per-flow overlay in `simulation_common.py::compute_flow_metrics`
whose constants have no documented derivation anywhere in this file. A first
real-hardware attempt to extend the delay/loss calibration past the single link
(`s5<->s14`) the original fit relied on found the loss-onset problem
cross-link-confirmed (real loss well below the assumed 0.7 threshold on both
tested links) but also caught its own measurement-timing bug (utilization and
delay windows didn't overlap cleanly, confirmed by requested_rate correlating
far better with real delay than the script's own achieved_utilization did) —
fixed same day in `mininet_calibration_sweep.py`, but the corrected sweep has
not been re-run yet. Decision-logic conclusions (who reroutes, when, in what
order) don't depend on either formula layer and remain high-confidence;
specific delay/throughput/loss *magnitudes* should be read as illustrative
until this is resolved.

**Genuinely new since 08-11, real-hardware:** the offered-load correction's
recovery-switchback check was extended from 1 pair to 5 (`2->7`, `12->25`,
`0->12`, `3->36`, `16->23`), run for real. Result: 2 of 5 pairs (`2->7` —
PRIMARY_PAIR itself — and `3->36`) show the correction actively changing a real
decision at real, measured background rates (4-16 Mbps) that the original
single-rate (24 Mbps) PRIMARY_PAIR check had missed entirely. The mechanism's
real-hardware evidence base is meaningfully stronger than it was on 08-11, not
just re-confirmed.

Still open: committing a figure-generation script (several dissertation-cited
figures currently reconstructable from `results/*.csv` but not committed as
code — same gap for `congestion.py`); re-running the corrected calibration
sweep; deciding whether to fit or retire `compute_flow_metrics`'s Layer-2
overlay once that data exists.

---

## Final Verdict — 2026-09-06 update

The two paragraphs above still stand. This update covers the resilience-avoidance
layer's rework and a repository restructure, both after 2026-09-01.

**Resilience-avoidance layer, reworked and now real-traffic-validated:**
- The avoidance gate was rewritten (`src/routing/resilience_gate.py`, new):
  suppress/reuse hysteresis (RFC 2439, reuse = 0.375 x avoid) on a combined
  resilience score, and a **finite additive penalty** (`GraphBuilder.
  RESILIENCE_AVOID_PENALTY = 1000.0`) rather than edge removal — a link with no
  alternative still carries traffic (no black-hole), plus a bounded-downside
  give-up cap (`RESILIENCE_MAX_DETOUR_FACTOR = 4.0`).
- `config/decision.yaml`: `resilience_avoidance.enabled` **false -> true**.
  Verified byte-identical output on all 5 generalization suites bar
  `failure_recovery` (which improves: proposed now stays off a link that flapped
  during recovery). No existing scenario's links reach a nonzero resilience score.
- Signal set trimmed to two: `get_resilience_score = max(flap, abnormal_loss)`.
  `traffic_growth` **dropped** — it fired on any link under rising load and gated
  the congested hotspot in `increasing_load`.
- Live trigger added: `DecisionEngine.evaluate_resilience_avoidance` moves a flow
  off a gated link on its current path (emergency reroute, no offered-load
  correction). RYU app link-event gaps closed (`EventLinkDelete` /
  `EventOFPPortStatus` / `EventLinkAdd` now all feed `set_link_status`).
- Robustness pass (P1-P4): absolute-level loss term (`LOSS_LEVEL_CAP = 0.05`) for
  chronic-stable-bad links with no shift; single-poll immunity (median + zero-std
  fallback); correlated-flap discount (>=4 links in one 2s poll -> 0.25 weight);
  the give-up cap above.
- **Three latent clock bugs fixed** — all meant the flap score was silently 0.0
  in every offline experiment before this session (synthetic clock not forwarded
  through `set_link_condition`/`set_link_status`; `LinkFlapTracker` missing
  transitions carried on `update_link_statistics`; `DynamicBaseline` rebuilding
  the graph at wall-clock and evicting synthetic-timestamp samples).
- **Real-hardware validation** (`scripts/mininet/abnormal_loss_check.py`, run
  2026-09-01): real `tc netem loss 8%` + live iperf on pair 2->7's first hop,
  **10/10 checks pass** — score crosses `avoid_threshold`, gate latches, flow
  reroutes off the lossy link (measured flow loss 8.8% -> 0), gate releases under
  hysteresis while the flow stays on the clean detour; both resilience-OFF
  controls never reroute. First real-hardware validation of the loss signal path.
  The link-flap real-hardware check (`scripts/mininet/link_flap_check.py`) has
  been rewritten around the recovery-window switch-back but not yet re-run
  successfully on the VM — the one remaining real-traffic gap.
- New evidence experiment `experiments/resilience/resilience_avoidance.py`
  (23/23 real GEANT pairs, abnormal_loss + chronic_loss: flow loss ~2.5% ->
  ~0.2%, delay ~124 -> ~48 ms). New figures `resilience_avoidance.png`,
  `resilience_sensitivity.png`.
- **Loss-signal threshold calibration** (2026-09-06): `resilience_sensitivity.py`
  extended with an ROC / Youden's-J search for the loss signal (was flap only).
  Against synthetic ground truth, `LOSS_LEVEL_CAP = 0.05` at the config
  `avoid_threshold = 0.57` gives TPR 0.89 / FPR 0.05; Youden-optimal for that cap
  is threshold 0.44 (J 0.89), and a cap near 0.03 separates the classes better
  (~0.99 / ~0.01). 0.05 is retained for its independent SPC/SLA grounding — the
  misses are marginal (~1.5-2.5pp chronic excess) links the 3-sigma shift term
  still catches once they worsen. Documented at `LossJitterTracker.LOSS_LEVEL_CAP`.

**Repository restructure (`ed28ef4`, 2026-09-06):** flat `experiments/` (34
files) split into `experiments/{scenarios,cost_formula,resilience}/`; `scripts/`
split into `scripts/{ryu,mininet}/` (the `mininet_` filename prefix dropped).
`experiments/` converted relative -> absolute imports. `experiment.py` and
`tests/` unchanged (only ever imported top-level modules). No experiment logic,
data, or figure changed — `make_figures` output is byte-identical across the
commit. Paths cited in *this* file above are pre-restructure and left as-is (a
dated journal); current paths are in `README.md`.

**Test count: 178 passing** (was 48 on 2026-08-11, 124 on 2026-08-24).
