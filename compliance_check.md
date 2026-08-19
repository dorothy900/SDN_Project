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
