#!/usr/bin/env python3
"""
Week 2 Integration Test
Day 6: Stage 2 full end-to-end verification
"""
import time
import random
from datetime import datetime
from pathlib import Path

from src.monitor.models import LinkStatistics
from src.monitor.network_state import NetworkState
from src.monitor.link_mapper import LinkMapper


def test_network_state_integration():
    """
    Complete Stage 2 integration test.
    Injects multiple flows, verifies consistency, no missing links.
    """
    print("=" * 70)
    print("Stage 2 Integration Test")
    print("=" * 70)
    
    # Initialize state
    output_dir = Path("results/network_state")
    output_dir.mkdir(parents=True, exist_ok=True)
    state = NetworkState(output_dir=output_dir)
    
    # Register some test links
    mapper = LinkMapper()
    test_links = [
        ("s1-s2", "s1", 1, "s2", 1),
        ("s2-s3", "s2", 2, "s3", 1),
        ("s3-s4", "s3", 2, "s4", 1),
    ]
    
    for link_id, s1, p1, s2, p2 in test_links:
        mapper.register_link(link_id, s1, p1, s2, p2)
    
    # Inject multiple samples to simulate traffic
    print("\nInjecting test traffic samples...")
    base_time = time.time()
    
    for i in range(30):
        ts = datetime.fromtimestamp(base_time + i * 2)
        
        for link_id, _, _, _, _ in test_links:
            # Simulate varying traffic
            utilization = 0.1 + random.random() * 0.5
            rx_mbps = 10 + random.random() * 40
            tx_mbps = 8 + random.random() * 35
            
            stats = LinkStatistics(
                timestamp=ts,
                link_id=link_id,
                utilization=utilization,
                rx_mbps=rx_mbps,
                tx_mbps=tx_mbps,
                status="up",
                delay_ms=5.0 + random.random() * 5.0,
                packet_loss=random.random() * 0.01,
            )
            
            state.update_link_statistics(stats)
    
    # Test 1: Verify get_network_state()
    print("\n1. Testing get_network_state()...")
    network_state = state.get_network_state()
    
    assert "timestamp" in network_state
    assert "topology" in network_state
    assert "links" in network_state
    assert "history_summaries" in network_state
    print("   ✓ Network state structure complete")
    
    # Test 2: Verify all links are present
    print("\n2. Verifying all links present...")
    for link_id, _, _, _, _ in test_links:
        assert link_id in network_state["links"]
        print(f"   ✓ Link {link_id} present")
    
    # Test 3: Verify history summaries
    print("\n3. Verifying history summaries...")
    for link_id, _, _, _, _ in test_links:
        summary = network_state["history_summaries"][link_id]
        assert "current" in summary
        assert "mean" in summary
        assert "max" in summary
        print(f"   ✓ {link_id}: current={summary['current']:.2f}, mean={summary['mean']:.2f}")
    
    # Test 4: Simulate a link failure and recovery
    print("\n4. Simulating link failure and recovery...")
    failed_link = "s1-s2"
    
    # Fail the link
    state.set_link_status(failed_link, is_up=False)
    network_state = state.get_network_state()
    assert network_state["links"][failed_link]["status"] == "down"
    print(f"   ✓ Link {failed_link} marked as down")
    
    # Recover the link
    state.set_link_status(failed_link, is_up=True)
    network_state = state.get_network_state()
    assert network_state["links"][failed_link]["status"] == "up"
    print(f"   ✓ Link {failed_link} recovered")
    
    # Save output
    print("\n5. Saving output artifacts...")
    state.save_state_snapshot("network_state_snapshot.json")
    state.link_monitor.save_events_to_csv("link_status_events.csv")
    state.history.save_history_to_csv("history_window_test.csv")
    print("   ✓ Output artifacts saved")
    
    # Generate integration report
    print("\n6. Generating integration report...")
    report_path = output_dir / "stage2_integration_report.md"
    
    with report_path.open("w", encoding="utf-8") as f:
        f.write("# Stage 2 Integration Report\n\n")
        f.write("## Summary\n")
        f.write("- All links: {}\n".format([l for l, _, _, _, _ in test_links]))
        f.write("- Test samples: 30\n")
        f.write("- Status: PASSED\n\n")
        f.write("## Output Files\n")
        f.write("- network_state_snapshot.json\n")
        f.write("- link_status_events.csv\n")
        f.write("- history_window_test.csv\n")
    
    print(f"   ✓ Integration report saved: {report_path}")
    
    print("\n" + "=" * 70)
    print("Stage 2 Integration Test: PASSED")
    print("=" * 70)
    
    return True


if __name__ == "__main__":
    success = test_network_state_integration()
    exit(0 if success else 1)
