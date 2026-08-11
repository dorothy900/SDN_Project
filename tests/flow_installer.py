#!/usr/bin/env python3
"""
Test Flow Installer
"""
from src.routing.flow_installer import FlowInstaller


def test_default_naming_without_mapping():
    installer = FlowInstaller()
    assert installer._switch_name("10") == "s11"
    assert installer._host_name("10") == "h11"


def test_real_mapping_overrides_default_naming():
    mapping = {"10": ("s3", "h3")}
    installer = FlowInstaller(node_mapping=mapping)
    assert installer._switch_name("10") == "s3"
    assert installer._host_name("10") == "h3"


def test_nodes_missing_from_mapping_fall_back_to_default_naming():
    mapping = {"10": ("s3", "h3")}
    installer = FlowInstaller(node_mapping=mapping)
    assert installer._switch_name("5") == "s6"
    assert installer._host_name("5") == "h6"


def test_build_flow_rules_uses_real_mapping_for_every_hop():
    mapping = {
        "2": ("s13", "h13"),
        "0": ("s1", "h1"),
        "34": ("s29", "h29"),
        "7": ("s38", "h38"),
    }
    installer = FlowInstaller(node_mapping=mapping)
    rules = installer.build_flow_rules(["2", "0", "34", "7"])
    switches = {rule["switch"] for rule in rules}
    assert switches == {"s13", "s1", "s29", "s38"}
    # Every switch/host reference must come from the real mapping, not the
    # guess-based s{id+1}/h{id+1} formula (which for this exact path would
    # wrongly produce s3/h3, s35/h35, s8/h8) -- confirms this is the fix
    # verified against live Mininet.
    real_names = {"s13", "h13", "s1", "h1", "s29", "h29", "s38", "h38"}
    for rule in rules:
        assert rule["switch"] in real_names
        assert rule["ingress"] in real_names
        assert rule["egress"] in real_names
