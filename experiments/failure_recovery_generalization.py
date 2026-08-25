#!/usr/bin/env python3
"""
Failure/Recovery Node-Pair Generalization - closes the one open item flagged
in compliance_check.md's "Node-pair generalization" section (2026-08-20):
the 17-pair congestion generalization check was never re-run through
failure_recovery.py, so recovery-switchback behavior was only ever verified
on PRIMARY_PAIR.

The original congestion-side pair-selection script (select_generalization_
pairs.py) is not present in this repository (lost along with the original
memory notes -- see compliance_check.md's session-continuity issue,
2026-08-24). This is a fresh implementation of the same *documented*
selection criterion, not a recovery of the original pair list -- the
specific 17 pairs will differ from the original congestion-side run, but
the sampling method is the same:

  1. Shortest path 3-5 hops (matches the original 4-pair check's own
     qualifying criterion, e.g. PRIMARY_PAIR "2"->"7" is 4 hops).
  2. At least one genuine alternative route -- checked directly (not
     assumed): remove every edge of the shortest path and confirm src/dst
     are still connected in the remaining graph.
  3. Stratified by the pair's failure-hop background tier -- which SNDlib
     data tier (see sndlib_demand.py) the pair's own first-hop edge (the
     edge failure_recovery.py's _run_case always fails first, mirrored
     here) falls into: tier1 = real GEANT demand data, tier2 = nobel-eu
     structural-diversity fallback, tier3 = uniform fallback. Same 8/2/6
     split as the original congestion-side 17-pair check (tier2's pool is
     inherently small -- only 6 of 61 edges are tier2-covered).
  4. Selection is pre-registered (computed once, deterministically, before
     any scenario is run) via even-index sampling within each tier bucket,
     sorted by hop count then node id -- no randomness, no outcome-based
     cherry-picking.

Metric: does `proposed` reach the same end-of-scenario switchback state as
`dynamic` (both cases: "stable" restoration, "unstable" flap-then-settle),
across 5 seeds per pair per case -- mirroring the "verified across 5
seeds, not asserted" convention already used for PRIMARY_PAIR itself in
compliance_check.md's recovery-switchback sections.

Caveat on that convention's actual strength, found in this session's
scenario design audit (finding F4): the 5 seeds only vary
build_network_state()'s tier-3 (uniform-fallback) edges by a full redraw --
tier-1/tier-2 edges only jitter by U(0.0, 0.01), effectively unchanged
across seeds. For 4 of these 23 pairs (0->9, 1->27, 2->9, 7->9), every
candidate path GraphBuilder considers is tier-1/tier-2 only, so all 5
seeds present nearly identical input to the algorithms -- "5/5 seed
agreement" for those 4 pairs is close to re-running one input 5 times, not
5 independent draws. The other 19/23 pairs have >=1 candidate touching a
tier-3 edge and carry real seed-to-seed variation. Treat agreement on
those 4 pairs as weaker evidence than the same-looking number elsewhere in
this file's output.

Run as: python3 -m experiments.failure_recovery_generalization
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import networkx as nx

from .simulation_common import (
    PRIMARY_PAIR,
    SAMPLE_INTERVAL_S,
    build_network_state,
    compute_flow_metrics,
    link_id,
    make_drivers,
    set_link_condition,
)
from .sndlib_demand import resolve_edge_demand_baseline, resolve_edge_diversity_baseline
from .traffic_generator import FlowDefinition

FAILURE_SAMPLE = 4
RESTORE_SAMPLE = 8
FLAP_DOWN_SAMPLE = 9
FLAP_RESTORE_SAMPLE = 10
SEEDS_PER_PAIR = 5
TIER_TARGETS = {1: 8, 2: 2, 3: 6}

MONITORED_FLOW = FlowDefinition(
    "flow-video-1", "Video", "UDP", "hA", "hB", 0, 12, 24.0, 1200
)


def _classify_tier(u: str, v: str) -> int:
    if resolve_edge_demand_baseline(u, v) is not None:
        return 1
    if resolve_edge_diversity_baseline(u, v) is not None:
        return 2
    return 3


def _has_genuine_alternative(graph: nx.Graph, path: Sequence[str]) -> bool:
    trimmed = graph.copy()
    trimmed.remove_edges_from(list(zip(path, path[1:])))
    return nx.has_path(trimmed, path[0], path[-1])


def select_pairs(graph: nx.Graph) -> Tuple[List[Tuple[str, str]], Dict[int, int]]:
    """Pre-registered pair selection. Returns (pairs, pool_sizes_by_tier)."""
    buckets: Dict[int, List[Tuple[str, str, int]]] = {1: [], 2: [], 3: []}
    nodes = sorted(graph.nodes(), key=int)
    for i, u in enumerate(nodes):
        for v in nodes[i + 1 :]:
            if (u, v) == PRIMARY_PAIR or (v, u) == PRIMARY_PAIR:
                continue
            if not nx.has_path(graph, u, v):
                continue
            path = nx.shortest_path(graph, u, v)
            hops = len(path) - 1
            if not (3 <= hops <= 5):
                continue
            if not _has_genuine_alternative(graph, path):
                continue
            tier = _classify_tier(*sorted((path[0], path[1]), key=int))
            buckets[tier].append((u, v, hops))

    selected: List[Tuple[str, str]] = []
    pool_sizes = {}
    for tier, target in TIER_TARGETS.items():
        pool = sorted(buckets[tier], key=lambda t: (t[2], int(t[0]), int(t[1])))
        pool_sizes[tier] = len(pool)
        take = min(target, len(pool))
        if take == 0:
            continue
        step = max(1, len(pool) // take)
        chosen = pool[::step][:take]
        selected.extend([(u, v) for u, v, _ in chosen])
    return selected, pool_sizes


BOUNDARY_TARGETS = {"short": (1, 2, 3), "long": (6, 7, 3)}


def select_boundary_pairs(graph: nx.Graph) -> Dict[str, List[Tuple[str, str]]]:
    """
    Supplementary edge-of-range pairs, added 2026-08-24 after the core
    3-5-hop check (61.7% of all node pairs, the topology's modal range --
    see compliance_check.md) was flagged as excluding both the "easy" short
    end (1-2 hops, 27% of pairs, 83-87% alternative-route availability) and
    the "hard" long tail (6-8 hops, 11% of pairs, availability collapsing
    to 29%/15%/0%). Not a random sample -- deterministic even-index draw
    from each qualifying pool, same method as select_pairs(). 3 per
    category: enough to check whether the core finding holds at the edges
    without overclaiming exhaustive coverage (the 7-hop pool itself only
    has 3 qualifying pairs total, so "long" is close to exhaustive already).
    """
    nodes = sorted(graph.nodes(), key=int)
    result: Dict[str, List[Tuple[str, str]]] = {}
    for label, (lo, hi, target) in BOUNDARY_TARGETS.items():
        pool: List[Tuple[str, str, int]] = []
        for i, u in enumerate(nodes):
            for v in nodes[i + 1 :]:
                if (u, v) == PRIMARY_PAIR or (v, u) == PRIMARY_PAIR:
                    continue
                if not nx.has_path(graph, u, v):
                    continue
                path = nx.shortest_path(graph, u, v)
                hops = len(path) - 1
                if not (lo <= hops <= hi):
                    continue
                if not _has_genuine_alternative(graph, path):
                    continue
                pool.append((u, v, hops))
        # prefer the rarer/harder end of the range first (7 before 6 for
        # "long"; 1 before 2 for "short" -- both are the smaller pools)
        pool.sort(key=lambda t: (-t[2] if label == "long" else t[2], int(t[0]), int(t[1])))
        take = min(target, len(pool))
        step = max(1, len(pool) // take) if take else 1
        chosen = pool[::step][:take]
        result[label] = [(u, v) for u, v, _ in chosen]
    return result


def _run_case(
    src: str,
    dst: str,
    case: str,
    unstable: bool,
    total_samples: int,
    seed: int,
) -> Dict[str, object]:
    state = build_network_state(Path("results/failure_recovery_generalization"), seed=seed)
    drivers = make_drivers(
        state, src, dst, threshold=0.7, persistence_required_samples=3,
        offered_load_mbps=MONITORED_FLOW.offered_load_mbps,
    )
    static, dynamic, proposed = drivers["static"], drivers["dynamic"], drivers["proposed"]
    failed_link = link_id(static.path[0], static.path[1])

    static_delays: List[float] = []
    dynamic_delays: List[float] = []
    proposed_delays: List[float] = []
    dynamic_reroutes = proposed_reroutes = 0
    dynamic_flow_updates = proposed_flow_updates = 0
    for sample in range(1, total_samples + 1):
        now_s = sample * SAMPLE_INTERVAL_S
        topology_changed = False
        if sample == RESTORE_SAMPLE:
            set_link_condition(state, failed_link, status="up", now=now_s)
            proposed.on_link_recovered(failed_link, now_s)
            topology_changed = True
        if unstable and sample == FLAP_DOWN_SAMPLE:
            set_link_condition(state, failed_link, status="down", now=now_s)
            proposed.on_link_flap()
            topology_changed = True
        if unstable and sample == FLAP_RESTORE_SAMPLE:
            set_link_condition(state, failed_link, status="up", now=now_s)
            proposed.on_link_recovered(failed_link, now_s)
            topology_changed = True

        if sample == FAILURE_SAMPLE:
            set_link_condition(state, failed_link, status="down", now=now_s)
            dynamic_result = dynamic.step(now_s=now_s, topology_changed=True)
            proposed_action = proposed.on_link_failure(failed_link, now_s)
            proposed_result = {"path": proposed.path, **proposed_action}
        else:
            dynamic_result = dynamic.step(now_s=now_s, topology_changed=topology_changed)
            proposed_result = proposed.step(now_s=now_s)

        static_result_path = static.path
        if sample >= FAILURE_SAMPLE:
            static_delays.append(
                compute_flow_metrics(state, static_result_path, MONITORED_FLOW.offered_load_mbps)["delay_ms"]
            )
            dynamic_delays.append(
                compute_flow_metrics(state, dynamic_result["path"], MONITORED_FLOW.offered_load_mbps)["delay_ms"]
            )
            proposed_delays.append(
                compute_flow_metrics(state, proposed_result["path"], MONITORED_FLOW.offered_load_mbps)["delay_ms"]
            )
            dynamic_reroutes += int(bool(dynamic_result["reroute"]))
            proposed_reroutes += int(bool(proposed_result["reroute"]))
            dynamic_flow_updates += int(dynamic_result["flow_updates"])
            proposed_flow_updates += int(proposed_result["flow_updates"])

    return {
        "dynamic_switched_back": dynamic.path == static.path,
        "proposed_switched_back": proposed.path == static.path,
        "static_delay_ms": sum(static_delays) / len(static_delays),
        "dynamic_delay_ms": sum(dynamic_delays) / len(dynamic_delays),
        "proposed_delay_ms": sum(proposed_delays) / len(proposed_delays),
        "dynamic_reroutes": dynamic_reroutes,
        "proposed_reroutes": proposed_reroutes,
        "dynamic_flow_updates": dynamic_flow_updates,
        "proposed_flow_updates": proposed_flow_updates,
    }


def run_pair(src: str, dst: str, group: str = "", hops: int = 0) -> Dict[str, object]:
    summary = {"pair": "%s->%s" % (src, dst), "group": group, "hops": hops}
    for case, unstable, total_samples in (("stable", False, 14), ("unstable", True, 16)):
        dyn_back = prop_back = 0
        delay_matches = 0
        case_offset = 0 if case == "stable" else 5000
        agg = {k: [] for k in (
            "static_delay_ms", "dynamic_delay_ms", "proposed_delay_ms",
            "dynamic_reroutes", "proposed_reroutes",
            "dynamic_flow_updates", "proposed_flow_updates",
        )}
        for trial in range(SEEDS_PER_PAIR):
            # Deterministic (not Python's randomized string hash()) so pair
            # selection and results are reproducible run to run.
            seed = int(src) * 1000 + int(dst) + case_offset + trial
            result = _run_case(src, dst, case, unstable, total_samples, seed)
            dyn_back += int(result["dynamic_switched_back"])
            prop_back += int(result["proposed_switched_back"])
            if result["dynamic_switched_back"] == result["proposed_switched_back"]:
                delay_matches += 1
            for k in agg:
                agg[k].append(result[k])
        summary[case] = {
            "dynamic_switchback_rate": dyn_back / SEEDS_PER_PAIR,
            "proposed_switchback_rate": prop_back / SEEDS_PER_PAIR,
            "agreement_rate": delay_matches / SEEDS_PER_PAIR,
            **{k: sum(v) / len(v) for k, v in agg.items()},
        }
    return summary


def labeled_pairs_23(graph: nx.Graph = None) -> List[Tuple[str, Tuple[str, str]]]:
    """
    The same 23-pair sample (17 core 3-5-hop + 6 boundary + PRIMARY_PAIR)
    used by this module's own generalization check, exposed for reuse by
    the other scenarios' generalization scripts (stale_stats,
    increasing_load, priority_policy) so all four draw the identical,
    pre-registered pair sample rather than each re-deriving their own --
    keeps results directly comparable pair-for-pair across scenarios.
    """
    if graph is None:
        from src.monitor.link_capacity import _load_geant_graph
        graph = _load_geant_graph()
    pairs, pool_sizes = select_pairs(graph)
    boundary = select_boundary_pairs(graph)
    result: List[Tuple[str, Tuple[str, str]]] = [("primary", PRIMARY_PAIR)]
    result += [("core", p) for p in pairs]
    result += [("boundary_short", p) for p in boundary["short"]]
    result += [("boundary_long", p) for p in boundary["long"]]
    return result


def main() -> None:
    from src.monitor.link_capacity import _load_geant_graph

    graph = _load_geant_graph()
    labeled_pairs = labeled_pairs_23(graph)
    print("Selected %d pairs total:" % len(labeled_pairs), labeled_pairs)

    rows = []
    for group, (src, dst) in labeled_pairs:
        hops = nx.shortest_path_length(graph, src, dst)
        summary = run_pair(src, dst, group=group, hops=hops)
        rows.append(summary)
        s, u = summary["stable"], summary["unstable"]
        print(
            "%-8s stable: dyn=%.2f prop=%.2f agree=%.2f delay(static/dyn/prop)=%.1f/%.1f/%.1f reroutes(dyn/prop)=%.1f/%.1f"
            % (summary["pair"], s["dynamic_switchback_rate"], s["proposed_switchback_rate"], s["agreement_rate"],
               s["static_delay_ms"], s["dynamic_delay_ms"], s["proposed_delay_ms"],
               s["dynamic_reroutes"], s["proposed_reroutes"])
        )
        print(
            "         unstable: dyn=%.2f prop=%.2f agree=%.2f delay(static/dyn/prop)=%.1f/%.1f/%.1f reroutes(dyn/prop)=%.1f/%.1f"
            % (u["dynamic_switchback_rate"], u["proposed_switchback_rate"], u["agreement_rate"],
               u["static_delay_ms"], u["dynamic_delay_ms"], u["proposed_delay_ms"],
               u["dynamic_reroutes"], u["proposed_reroutes"])
        )

    output_dir = Path("results/failure_recovery_generalization")
    output_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "pair", "group", "hops",
        "stable_dynamic_rate", "stable_proposed_rate", "stable_agreement",
        "stable_static_delay_ms", "stable_dynamic_delay_ms", "stable_proposed_delay_ms",
        "stable_dynamic_reroutes", "stable_proposed_reroutes",
        "unstable_dynamic_rate", "unstable_proposed_rate", "unstable_agreement",
        "unstable_static_delay_ms", "unstable_dynamic_delay_ms", "unstable_proposed_delay_ms",
        "unstable_dynamic_reroutes", "unstable_proposed_reroutes",
    ]
    with (output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fieldnames)
        for row in rows:
            s, u = row["stable"], row["unstable"]
            writer.writerow([
                row["pair"], row["group"], row["hops"],
                s["dynamic_switchback_rate"], s["proposed_switchback_rate"], s["agreement_rate"],
                s["static_delay_ms"], s["dynamic_delay_ms"], s["proposed_delay_ms"],
                s["dynamic_reroutes"], s["proposed_reroutes"],
                u["dynamic_switchback_rate"], u["proposed_switchback_rate"], u["agreement_rate"],
                u["static_delay_ms"], u["dynamic_delay_ms"], u["proposed_delay_ms"],
                u["dynamic_reroutes"], u["proposed_reroutes"],
            ])
    print("Wrote", output_dir / "summary.csv")

    n = len(rows)
    stable_full_agree = sum(1 for r in rows if r["stable"]["agreement_rate"] == 1.0)
    unstable_full_agree = sum(1 for r in rows if r["unstable"]["agreement_rate"] == 1.0)
    mean = lambda vals: sum(vals) / len(vals)
    print(
        "\nFull (5/5 seed) agreement: stable %d/%d pairs, unstable %d/%d pairs"
        % (stable_full_agree, n, unstable_full_agree, n)
    )
    print(
        "Mean delay across pairs -- stable: static=%.1f dynamic=%.1f proposed=%.1f | "
        "unstable: static=%.1f dynamic=%.1f proposed=%.1f"
        % (
            mean([r["stable"]["static_delay_ms"] for r in rows]),
            mean([r["stable"]["dynamic_delay_ms"] for r in rows]),
            mean([r["stable"]["proposed_delay_ms"] for r in rows]),
            mean([r["unstable"]["static_delay_ms"] for r in rows]),
            mean([r["unstable"]["dynamic_delay_ms"] for r in rows]),
            mean([r["unstable"]["proposed_delay_ms"] for r in rows]),
        )
    )
    print(
        "Mean reroutes/run across pairs -- stable: dynamic=%.2f proposed=%.2f | unstable: dynamic=%.2f proposed=%.2f"
        % (
            mean([r["stable"]["dynamic_reroutes"] for r in rows]),
            mean([r["stable"]["proposed_reroutes"] for r in rows]),
            mean([r["unstable"]["dynamic_reroutes"] for r in rows]),
            mean([r["unstable"]["proposed_reroutes"] for r in rows]),
        )
    )


if __name__ == "__main__":
    main()
