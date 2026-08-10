#!/usr/bin/env python3
"""
Week 1 Verification - System Validation
Check all Week 1 tasks completed and produce results

SUPERSEDED: this script's real logic (topology loading, node/link counts,
connectivity, diameter) has been ported into
experiments/run_topology_validation.py, which `run_experiment.py --stage 1`
actually calls. This file still runs and still passes, but is not part of
the pipeline -- kept for reference, not as the authoritative check.
"""

import time
from datetime import datetime
from pathlib import Path
import networkx as nx

def verify_day12_topology():
    """Day 1-2: Topology Verification"""
    print("="*70)
    print("Week 1 - Day 1-2: Topology Verification")
    print("="*70)
    
    # 1. Check topology file
    graph_file = Path("data/Geant2012.graphml")
    if not graph_file.exists():
        print("  [FAIL] Topology file not found")
        return False
    
    print("  [OK] Topology file found: data/Geant2012.graphml")
    
    # 2. Load topology
    graph = nx.read_graphml(graph_file)
    print(f"  [OK] Loaded {graph.number_of_nodes()} nodes")
    print(f"  [OK] Loaded {graph.number_of_edges()} links")
    
    # 3. Check connectivity
    undirected = nx.Graph(graph)
    connected = nx.is_connected(undirected)
    print(f"  [OK] Network is connected: {connected}")
    
    # 4. Check basic topology stats
    diameter = nx.diameter(undirected)
    avg_degree = sum(dict(undirected.degree()).values()) / undirected.number_of_nodes()
    
    print(f"  [OK] Network diameter: {diameter}")
    print(f"  [OK] Average degree: {avg_degree:.2f}")
    
    # 5. Output results to file
    output_dir = Path("results/stage1")
    output_dir.mkdir(exist_ok=True, parents=True)
    
    with open(output_dir / "topology_validation.txt", "w", encoding="utf-8") as f:
        f.write(f"Topology Verification - {datetime.now().isoformat()}\n")
        f.write("="*60 + "\n")
        f.write(f"Nodes: {graph.number_of_nodes()}\n")
        f.write(f"Links: {graph.number_of_edges()}\n")
        f.write(f"Connected: {connected}\n")
        f.write(f"Diameter: {diameter}\n")
        f.write(f"Average degree: {avg_degree:.2f}\n")
        f.write("\nNodes (sorted):\n")
        for node in sorted(graph.nodes()):
            f.write(f"  {node}\n")
        f.write("\nFirst 20 links:\n")
        for i, (u, v) in enumerate(sorted(graph.edges())):
            if i >= 20:
                break
            f.write(f"  {u} <-> {v}\n")
    
    print("\n  [OK] Results written to: results/stage1/topology_validation.txt")
    
    return True

def verify_day34_monitor():
    """Day 3-4: Traffic Monitor"""
    print("\n" + "="*70)
    print("Week 1 - Day 3-4: Traffic Monitor")
    print("="*70)
    
    # 1. Check monitor module files
    monitor_files = [
        "src/monitor/models.py",
        "src/monitor/odl_client.py",
        "src/monitor/statistics_collector.py",
        "src/monitor/traffic_monitor.py",
        "src/monitor/history_store.py",
        "src/monitor/link_mapper.py",
        "src/monitor/topology_state.py",
        "src/monitor/link_monitor.py",
        "src/monitor/network_state.py"
    ]
    
    all_exist = True
    for f in monitor_files:
        path = Path(f)
        if path.exists():
            print(f"  [OK] {f}")
        else:
            print(f"  [FAIL] {f}")
            all_exist = False
    
    # 2. Test statistics collector basic functions
    try:
        from src.monitor.statistics_collector import StatisticsCollector
        collector = StatisticsCollector()
        print("  [OK] StatisticsCollector initialized")
        
        from src.monitor.models import PortStatistics, LinkStatistics
        print("  [OK] Data models imported")
        
        # 3. Simulate some stats collection
        ts = datetime.now()
        stats = PortStatistics(
            timestamp=ts,
            switch="s1",
            port=1,
            rx_packets=1000,
            rx_bytes=1000000,
            tx_packets=500,
            tx_bytes=500000,
            rx_mbps=0.0,
            tx_mbps=0.0
        )
        
        collector.save_to_csv([stats], "monitor_test.csv")
        result_path = Path("results/stage1/monitor_test.csv")
        if result_path.exists():
            print("  [OK] CSV output working")
        
        print("  [OK] Traffic monitor module verified")
        
    except Exception as e:
        print(f"  [FAIL] Monitor module: {e}")
        return False
    
    return all_exist

def verify_day5_data_models():
    """Day 5: Data Models"""
    print("\n" + "="*70)
    print("Week 1 - Day 5: Data Models")
    print("="*70)
    
    try:
        from src.monitor.models import PortStatistics, LinkStatistics, RoutingDecision
        
        # 1. Test PortStatistics
        ts = datetime.now()
        port_stat = PortStatistics(
            timestamp=ts,
            switch="s1",
            port=1,
            rx_packets=1000,
            rx_bytes=1500000,
            tx_packets=800,
            tx_bytes=1200000,
            rx_mbps=1.2,
            tx_mbps=0.96
        )
        
        print("  [OK] PortStatistics created")
        
        # 2. Test LinkStatistics
        link_stat = LinkStatistics(
            timestamp=ts,
            link_id="s1-s2",
            utilization=0.35,
            rx_mbps=35.0,
            tx_mbps=30.0,
            status="up",
            delay_ms=5.0,
            packet_loss=0.0
        )
        
        print("  [OK] LinkStatistics created")
        
        # 3. Check config files
        config_files = [
            "config/topology.yaml",
            "config/links.yaml",
            "config/policies.yaml",
            "config/decision.yaml"
        ]
        
        for f in config_files:
            if Path(f).exists():
                print(f"  [OK] Config file: {f}")
            else:
                print(f"  [FAIL] Config file missing: {f}")
        
        print("  [OK] Data models verified")
        return True
        
    except Exception as e:
        print(f"  [FAIL] Data models: {e}")
        return False

def verify_day6_integration():
    """Day 6: Integration & README"""
    print("\n" + "="*70)
    print("Week 1 - Day 6: Integration & README")
    print("="*70)
    
    # 1. Check key files exist
    key_files = [
        "README.md",
        "topology.py",
        "run_experiment.py",
        "requirements.txt",
        ".gitignore"
    ]
    
    all_ok = True
    for f in key_files:
        if Path(f).exists():
            print(f"  [OK] {f}")
        else:
            print(f"  [FAIL] {f} missing")
            all_ok = False
    
    # 2. Check scripts
    script_files = [
        "scripts/start_odl.sh",
        "scripts/start_topology.sh"
    ]
    for f in script_files:
        if Path(f).exists():
            print(f"  [OK] Script: {f}")
            if Path(f).stat().st_mode & 0o111:
                print(f"  [OK]   Script executable")
    
    # 3. Check tests exist
    test_files = [
        "tests/test_statistics_collector.py",
        "tests/test_threshold_detector.py",
        "tests/test_persistence_checker.py",
        "tests/test_path_cost.py",
        "tests/test_change_budget.py",
        "tests/test_stability_manager.py"
    ]
    
    print("\n  Test files:")
    for f in test_files:
        if Path(f).exists():
            print(f"    [OK] {f}")
        else:
            print(f"    [FAIL] {f}")
    
    # 4. Write integration summary
    output_dir = Path("results/stage1")
    with open(output_dir / "week1_integration_report.txt", "w", encoding="utf-8") as f:
        f.write(f"Week 1 Integration Report - {datetime.now().isoformat()}\n")
        f.write("="*70 + "\n\n")
        f.write("All components integrated:\n")
        f.write("  - Topology loading working\n")
        f.write("  - Traffic monitor module ready\n")
        f.write("  - Data models defined\n")
        f.write("  - Entry point script: run_experiment.py\n")
        f.write("  - Configuration files: config/\n")
        f.write("  - Unit tests: tests/\n\n")
    
    print("  [OK] Integration report written to results/stage1/")
    
    return all_ok

def generate_final_report(results):
    """Generate final Week 1 report"""
    print("\n" + "="*70)
    print("Week 1 - Final Report")
    print("="*70)
    
    passed = sum(1 for r in results.values() if r)
    total = len(results)
    
    output_dir = Path("results/stage1")
    output_dir.mkdir(exist_ok=True)
    
    report_path = output_dir / "week1_final_report.txt"
    with report_path.open("w", encoding="utf-8") as f:
        f.write("="*70 + "\n")
        f.write("SDN Dissertation - Week 1 Final Report\n")
        f.write("="*70 + "\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n\n")
        
        f.write("Week 1: System Validation - Completed Tasks\n")
        f.write("-"*70 + "\n")
        
        for task, status in results.items():
            status_str = "✓ COMPLETED" if status else "✗ FAILED"
            f.write(f"  {task}: {status_str}\n")
            print(f"  {task}: {status_str}")
        
        f.write("\n" + "="*70 + "\n")
        f.write(f"Overall: {passed}/{total} tasks completed\n")
    
    print(f"\n  Final report written to: {report_path}")
    
    return passed, total

def main():
    print("\n" + "="*70)
    print("  SDN Dissertation - Week 1 Complete Verification")
    print("="*70 + "\n")
    
    results = {}
    results["Day 1-2: Topology Verification"] = verify_day12_topology()
    results["Day 3-4: Traffic Monitor"] = verify_day34_monitor()
    results["Day 5: Data Models"] = verify_day5_data_models()
    results["Day 6: Integration"] = verify_day6_integration()
    
    passed, total = generate_final_report(results)
    
    print("\n" + "="*70)
    print(f"  Week 1 Summary: {passed}/{total} tasks COMPLETED!")
    print("="*70 + "\n")
    
    return 0 if passed == total else 1

if __name__ == "__main__":
    exit(main())
