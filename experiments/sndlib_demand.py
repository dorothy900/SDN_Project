#!/usr/bin/env python3
"""
SNDlib Demand Mapping - real GEANT traffic-demand data, mapped onto our
Topology Zoo Geant2012.graphml nodes by country identity, to calibrate
offline scenario experiments' background link baselines.

Background: `simulation_common.py::build_network_state()`'s ~60 non-hotspot
"background" links were seeded with a purely arbitrary formula
(`0.22 + 0.01*(link_index % 6) + small jitter`) -- no real data behind it,
just a fixed pattern by iteration order. User asked whether this could be
replaced with something grounded in real traffic data instead ("SNDlib" --
the Survivable Network Design Library, sndlib.zib.de). Checked directly:

- `data/sndlib_geant.xml` (fetched from the SNDlib network-data mirror at
  https://github.com/arekolek/MaxIST/tree/master/sndlib-networks-xml,
  2005 vintage, 4-month-granularity peak demand matrix in Mbps) is a real,
  independent GEANT-instance dataset: 22 nodes (ISO-country-code IDs like
  `de1.de`, `ch1.ch`), a complete 22x21=462-entry pairwise demand matrix.
- 21 of those 22 nodes match our Geant2012.graphml's country labels
  exactly by identity (`ch1.ch` -> our node "8", labeled "Switzerland",
  etc.) -- the only unmatched SNDlib node is `ny1.ny` (a transatlantic New
  York node our topology doesn't have). The other 19 of our 40 nodes
  (smaller/peripheral countries this 2005-vintage SNDlib instance simply
  doesn't cover -- Balkans, ex-USSR states, Baltics, Iceland, Denmark)
  have no real data to borrow and are NOT guessed at here -- callers fall
  back to the pre-existing arbitrary formula for those, same as before.
- Real per-node aggregate demand spans a real ~42x range (Switzerland's
  total ~1.21M Mbps vs Luxembourg's ~29K) -- a genuine, non-trivial
  statistical signal, not noise.

Verified before adopting (per explicit user-requested sanity pass, not
skipped): (1) the resulting edge-baseline distribution across our
topology's real edges actually spreads out (30/61 edges have both
endpoints matched; histogram over the target range is bell-shaped, not
collapsed to one point); (2) hand-checked specific edges against
intuition -- Luxembourg-touching edges land low (0.212-0.218), the
Switzerland-Germany edge (the two single highest-demand matched nodes)
lands highest among all matched edges (0.333), Austria-Germany lands
solidly upper-middle (0.304) -- no edge that should intuitively be
peripheral came out inflated or vice versa.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Dict, Optional

_SNDLIB_XML_PATH = Path(__file__).resolve().parents[1] / "data" / "sndlib_geant.xml"
_SNDLIB_NS = {"sndlib": "http://sndlib.zib.de/network"}

# SNDlib's 2-letter-country-code node id -> our Geant2012.graphml node id,
# matched by real country identity (both datasets describe the same real
# GEANT backbone, just different snapshots/years). Built and verified
# 2026-08-20 -- see module docstring.
_CODE_TO_OUR_NODE: Dict[str, str] = {
    "at": "29", "be": "1", "ch": "8", "cz": "5", "de": "4", "es": "25",
    "fr": "7", "gr": "15", "hr": "27", "hu": "22", "ie": "33", "il": "17",
    "it": "9", "lu": "6", "nl": "0", "pl": "3", "pt": "24", "se": "36",
    "si": "28", "sk": "23", "uk": "34",
}

# Target range for the log-scaled, normalized per-node baseline -- these
# are *background* (non-hotspot) links, meant to carry mild ambient
# traffic, not spike to congestion just because one endpoint is a real
# high-demand hub. Originally chosen to bracket the pre-existing arbitrary
# formula's own range (0.22-0.28); as of 2026-08-20 it's the other way
# around -- FALLBACK_LOW/FALLBACK_HIGH below make the *fallback* formula
# match *this* range instead, see that constant's docstring for why.
_BASELINE_LOW = 0.15
_BASELINE_HIGH = 0.35

# Public: the same range, for simulation_common.py's fallback tier (edges
# with no real or diversity data at all) to draw from. Added 2026-08-20
# after finding the old fallback formula (`0.22 + 0.01*(index%6)`, a narrow
# ~0.05-wide band) created a real, *systematic* -- not random -- bias: real
# demand-calibrated edges between busy hub countries legitimately read
# higher than this narrow band's ceiling, so any candidate path touching
# even one uncalibrated node looked artificially cheap by comparison,
# deterministically (verified across 20 different seeds, same result every
# time). The fix isn't to fake real data for uncovered nodes -- it's to
# stop *underrepresenting* their plausible range: draw uniformly across
# the same [FALLBACK_LOW, FALLBACK_HIGH] real tiers use, so "no data" reads
# as genuine uncertainty (could be a quiet edge, could be a busy one)
# instead of a confident, artificially narrow "always moderate" guess.
FALLBACK_LOW = _BASELINE_LOW
FALLBACK_HIGH = _BASELINE_HIGH

_node_demand_baseline_cache: Optional[Dict[str, float]] = None


def _load_node_demand_baselines() -> Dict[str, float]:
    """
    Parse data/sndlib_geant.xml once per process (cached), sum each node's
    total real demand (sum of every pairwise demand value it's a source or
    target of), log-scale (the real range spans ~42x -- linear scaling
    would flatten everything but the top node), then min-max normalize
    into [_BASELINE_LOW, _BASELINE_HIGH]. Keyed by *our* node ids (via
    _CODE_TO_OUR_NODE), not SNDlib's own.
    """
    global _node_demand_baseline_cache
    if _node_demand_baseline_cache is not None:
        return _node_demand_baseline_cache

    tree = ET.parse(_SNDLIB_XML_PATH)
    root = tree.getroot()
    demands = root.find("sndlib:demands", _SNDLIB_NS)

    node_total: Dict[str, float] = defaultdict(float)
    for d in demands:
        source = d.find("sndlib:source", _SNDLIB_NS).text
        target = d.find("sndlib:target", _SNDLIB_NS).text
        value = float(d.find("sndlib:demandValue", _SNDLIB_NS).text)
        node_total[source] += value
        node_total[target] += value

    our_node_totals = {
        our_node: node_total[f"{code}1.{code}"]
        for code, our_node in _CODE_TO_OUR_NODE.items()
    }

    log_vals = {n: math.log(v) for n, v in our_node_totals.items()}
    lo, hi = min(log_vals.values()), max(log_vals.values())
    _node_demand_baseline_cache = {
        n: _BASELINE_LOW + (lv - lo) / (hi - lo) * (_BASELINE_HIGH - _BASELINE_LOW)
        for n, lv in log_vals.items()
    }
    return _node_demand_baseline_cache


def resolve_edge_demand_baseline(u: str, v: str) -> Optional[float]:
    """
    Real-data-calibrated background utilization baseline for edge (u, v),
    or None if either endpoint has no real SNDlib-matched demand (caller
    should fall back to the pre-existing arbitrary formula in that case).

    Geometric mean of both endpoints' normalized demand: an edge between
    two high-demand real hubs (e.g. Switzerland-Germany) should read high;
    an edge touching even one low-demand node (e.g. anything-Luxembourg)
    should be pulled down, not dominated by its busier neighbor -- matches
    how a real access/backbone edge's utilization reflects the smaller of
    the two sides' typical demand, not just the larger.
    """
    baselines = _load_node_demand_baselines()
    if u not in baselines or v not in baselines:
        return None
    return math.sqrt(baselines[u] * baselines[v])


# ---------------------------------------------------------------------------
# Tier 2: SNDlib "nobel-eu" -- scenario-diversity only, NOT a realism claim.
#
# Checked directly (2026-08-20) whether nobel-eu could extend the real-data
# tier above: it cannot, and is kept structurally separate here so it never
# gets confused with it. SNDlib's own literature classifies its network
# instances into three backgrounds -- (a) real industrial-project data,
# (b) "reference networks" defined *for* international research projects,
# built to correspond to realistic planning scenarios rather than measured
# from an operating network, (c) NDA-protected/undisclosed. nobel-eu is
# case (b): it's the network defined by the EU-funded NOBEL project
# (telecom operators + equipment vendors + academic partners), a
# constructed planning scenario, not a measured one -- confirmed further by
# the file itself carrying no <meta>/origin/unit tag at all (unlike
# sndlib_geant.xml's explicit <unit>MBITPERSEC</unit> and
# <origin>Steve Uhlig, real scaled peak matrix</origin>), and by its demand
# values sitting on a wildly different, undocumented scale (e.g.
# Amsterdam-Athens = 6.0, versus the real geant matrix's comparable-distance
# pairs in the hundreds-to-thousands).
#
# Usable for a narrower, honest purpose instead: nobel-eu's 28 city nodes
# cover 3 of our topology's countries the real geant matrix doesn't
# (Denmark/Copenhagen, Norway/Oslo, Serbia/Belgrade -- checked: even
# combined with the real tier, 16 of our 40 nodes remain uncovered by
# either SNDlib instance, so this is not a path to full coverage). Using
# its *relative* demand pattern gives those background links real
# structural variation (some higher, some lower, shaped by an actual
# published reference topology) instead of the flat formula -- more
# *scenario diversity* than a repeating index-based pattern gives, without
# claiming it represents real Copenhagen/Oslo/Belgrade traffic. Kept as a
# fully separate function/cache from resolve_edge_demand_baseline() and
# only ever consulted as a fallback *after* the real tier has already been
# tried and failed for a given edge -- it never overrides or blends with a
# real-tier value.
# ---------------------------------------------------------------------------

_NOBEL_EU_XML_PATH = Path(__file__).resolve().parents[1] / "data" / "sndlib_nobel_eu.xml"

# nobel-eu city node id -> our Geant2012.graphml node id. Includes cities
# for countries already covered by the real tier (e.g. Berlin/Frankfurt/
# Hamburg/Munich -> Germany) so per-edge fallback resolution stays simple
# (see resolve_edge_diversity_baseline's docstring for why that's still
# safe to do); the 3 *new* countries this tier actually adds are Denmark,
# Norway, and Serbia.
_CITY_TO_OUR_NODE: Dict[str, str] = {
    "Amsterdam": "0", "Athens": "15", "Barcelona": "25", "Belgrade": "26",
    "Berlin": "4", "Bordeaux": "7", "Brussels": "1", "Budapest": "22",
    "Copenhagen": "2", "Dublin": "33", "Frankfurt": "4", "Glasgow": "34",
    "Hamburg": "4", "London": "34", "Lyon": "7", "Madrid": "25",
    "Milan": "9", "Munich": "4", "Oslo": "35", "Paris": "7",
    "Prague": "5", "Rome": "9", "Stockholm": "36", "Strasbourg": "7",
    "Vienna": "29", "Warsaw": "3", "Zagreb": "27", "Zurich": "8",
}

_diversity_baseline_cache: Optional[Dict[str, float]] = None


def _load_diversity_baselines() -> Dict[str, float]:
    """Same log-scale + min-max-to-[_BASELINE_LOW,_BASELINE_HIGH] method as
    the real tier, applied to nobel-eu's demand matrix instead. Multiple
    cities mapping to the same of our nodes (e.g. 4 German cities) are
    summed together first, same idea as the real tier's per-node total.

    Re-centered (added 2026-08-20, see docstring below) to match tier 1's
    empirical mean/std instead of trusting the raw min-max-to-[LOW,HIGH]
    output -- min-max only guarantees the *full* nobel-eu distribution
    spans [LOW,HIGH], not that whichever small subset of it maps onto our
    topology's edges will. Checked directly: it doesn't -- the 6 our-edges
    this tier actually serves came out with mean 0.186 vs tier 1's 0.256,
    a real, deterministic (not seed-dependent) ~0.07 gap, found by a direct
    user challenge ("would a background-generation preference bias which
    path gets chosen?") right after the FALLBACK_LOW/HIGH fix above shipped.
    Same underlying failure mode as that fix (a background tier reading
    systematically low relative to the others, deterministically favoring
    whichever candidate path happens to route through it) just surfacing
    in a different, smaller tier (6 edges, not 25) that the first fix
    didn't touch. Re-centering (z-score against tier 1's node-level mean/
    std, not tier 2's own) fixes the *level* while preserving tier 2's own
    relative shape -- Denmark still reads higher or lower than Norway
    exactly as nobel-eu's own data implies, it just no longer reads
    systematically low against tier 1 as a whole.
    """
    global _diversity_baseline_cache
    if _diversity_baseline_cache is not None:
        return _diversity_baseline_cache

    tree = ET.parse(_NOBEL_EU_XML_PATH)
    root = tree.getroot()
    demands = root.find("sndlib:demands", _SNDLIB_NS)

    city_total: Dict[str, float] = defaultdict(float)
    for d in demands:
        source = d.find("sndlib:source", _SNDLIB_NS).text
        target = d.find("sndlib:target", _SNDLIB_NS).text
        value = float(d.find("sndlib:demandValue", _SNDLIB_NS).text)
        city_total[source] += value
        city_total[target] += value

    our_node_totals: Dict[str, float] = defaultdict(float)
    for city, our_node in _CITY_TO_OUR_NODE.items():
        our_node_totals[our_node] += city_total.get(city, 0.0)

    log_vals = {n: math.log(v) for n, v in our_node_totals.items() if v > 0}
    lo, hi = min(log_vals.values()), max(log_vals.values())
    raw = {
        n: _BASELINE_LOW + (lv - lo) / (hi - lo) * (_BASELINE_HIGH - _BASELINE_LOW)
        for n, lv in log_vals.items()
    }

    tier1_vals = list(_load_node_demand_baselines().values())
    tier1_mean = sum(tier1_vals) / len(tier1_vals)
    tier1_std = math.sqrt(sum((v - tier1_mean) ** 2 for v in tier1_vals) / len(tier1_vals))
    raw_vals = list(raw.values())
    raw_mean = sum(raw_vals) / len(raw_vals)
    raw_std = math.sqrt(sum((v - raw_mean) ** 2 for v in raw_vals) / len(raw_vals))

    _diversity_baseline_cache = {
        n: min(max(tier1_mean + (v - raw_mean) / raw_std * tier1_std, _BASELINE_LOW), _BASELINE_HIGH)
        for n, v in raw.items()
    }
    return _diversity_baseline_cache


def resolve_edge_diversity_baseline(u: str, v: str) -> Optional[float]:
    """
    Scenario-diversity-only background baseline for edge (u, v), sourced
    from SNDlib's synthetic nobel-eu reference network -- NOT a realism
    claim (see this section's module-level comment for why). Callers must
    only consult this *after* resolve_edge_demand_baseline() has already
    returned None for the same edge, so a real-tier value is never
    overridden or blended with this one.
    """
    baselines = _load_diversity_baselines()
    if u not in baselines or v not in baselines:
        return None
    return math.sqrt(baselines[u] * baselines[v])
