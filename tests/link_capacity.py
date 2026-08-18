#!/usr/bin/env python3
"""
Test Link Capacity
"""
from src.monitor.link_capacity import REAL_LINK_LABEL_TO_MBPS, resolve_link_bw_mbps


def test_known_real_labels_map_to_their_scaled_mbps():
    assert resolve_link_bw_mbps("10 Gbps", default_mbps=100) == REAL_LINK_LABEL_TO_MBPS["10 Gbps"]
    assert resolve_link_bw_mbps("155 Mbps", default_mbps=100) == REAL_LINK_LABEL_TO_MBPS["155 Mbps"]


def test_faster_real_tiers_get_more_simulated_bandwidth():
    slow = resolve_link_bw_mbps("155 Mbps", default_mbps=100)
    mid = resolve_link_bw_mbps("1 Gbps", default_mbps=100)
    fast = resolve_link_bw_mbps("10 Gbps", default_mbps=100)
    assert slow < mid < fast


def test_missing_label_falls_back_to_default_not_a_guess():
    assert resolve_link_bw_mbps(None, default_mbps=100) == 100
    assert resolve_link_bw_mbps("", default_mbps=100) == 100


def test_unrecognized_label_falls_back_to_default():
    assert resolve_link_bw_mbps("some future label sndlib never told us about", default_mbps=100) == 100


def test_lit_fibre_treated_as_top_tier():
    assert resolve_link_bw_mbps("Lit Fibre", default_mbps=100) == REAL_LINK_LABEL_TO_MBPS["10 Gbps"]
