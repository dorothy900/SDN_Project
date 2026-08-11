#!/usr/bin/env python3
"""
Decision Logger - Record all rerouting decisions
For analysis, debugging, and evaluation
"""

import csv
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class DecisionRecord:
    timestamp: datetime
    decision_type: str  # "reroute", "no_action", "cooldown", "no_improvement"
    reason: Optional[str]
    affected_links: List[str]
    old_path: Optional[List[str]]
    new_path: Optional[List[str]]
    old_cost: Optional[float]
    new_cost: Optional[float]
    stability_mechanisms_applied: List[str]


class DecisionLogger:
    """Log rerouting decisions for analysis."""

    def __init__(self, log_dir: Path = Path("results/decision_engine")):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.records: List[DecisionRecord] = []

    def log_reroute(self, reason: str, affected_links: List[str],
                   old_path: Optional[List[str]], new_path: List[str],
                   old_cost: Optional[float], new_cost: float,
                   stability_used: List[str]):
        """Log a rerouting decision."""
        record = DecisionRecord(
            timestamp=datetime.now(),
            decision_type="reroute",
            reason=reason,
            affected_links=affected_links,
            old_path=old_path,
            new_path=new_path,
            old_cost=old_cost,
            new_cost=new_cost,
            stability_mechanisms_applied=stability_used
        )
        self.records.append(record)

    def log_no_action(self, reason: str):
        """Log that no action was taken."""
        record = DecisionRecord(
            timestamp=datetime.now(),
            decision_type="no_action",
            reason=reason,
            affected_links=[],
            old_path=None,
            new_path=None,
            old_cost=None,
            new_cost=None,
            stability_mechanisms_applied=[]
        )
        self.records.append(record)

    def log_cooldown(self, link_id: str):
        """Log that action was skipped due to cooldown."""
        record = DecisionRecord(
            timestamp=datetime.now(),
            decision_type="cooldown",
            reason=f"Link {link_id} in cooldown period",
            affected_links=[link_id],
            old_path=None,
            new_path=None,
            old_cost=None,
            new_cost=None,
            stability_mechanisms_applied=["cooldown"]
        )
        self.records.append(record)

    def log_no_improvement(self, old_path: List[str], new_path: List[str],
                          old_cost: float, new_cost: float):
        """Log that new path wasn't enough improvement."""
        record = DecisionRecord(
            timestamp=datetime.now(),
            decision_type="no_improvement",
            reason="New path does not meet improvement threshold",
            affected_links=[],
            old_path=old_path,
            new_path=new_path,
            old_cost=old_cost,
            new_cost=new_cost,
            stability_mechanisms_applied=["minimum_improvement"]
        )
        self.records.append(record)

    def save_to_csv(self, filename: str = "decision_log.csv"):
        """Save decision log to CSV file."""
        filepath = self.log_dir / filename

        with filepath.open('w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                'timestamp', 'decision_type', 'reason', 'affected_links',
                'old_path', 'new_path', 'old_cost', 'new_cost',
                'stability_mechanisms'
            ])

            for record in self.records:
                writer.writerow([
                    record.timestamp.isoformat(),
                    record.decision_type,
                    record.reason or "",
                    ",".join(record.affected_links),
                    ",".join(record.old_path) if record.old_path else "",
                    ",".join(record.new_path) if record.new_path else "",
                    f"{record.old_cost:.4f}" if record.old_cost is not None else "",
                    f"{record.new_cost:.4f}" if record.new_cost is not None else "",
                    ",".join(record.stability_mechanisms_applied)
                ])

    def get_reroute_count(self) -> int:
        """Get total number of reroutes."""
        return sum(1 for r in self.records if r.decision_type == "reroute")

    def get_summary(self) -> dict:
        """Get summary statistics."""
        return {
            'total_decisions': len(self.records),
            'reroutes': self.get_reroute_count(),
            'no_actions': sum(1 for r in self.records if r.decision_type == "no_action"),
            'cooldowns': sum(1 for r in self.records if r.decision_type == "cooldown"),
            'no_improvements': sum(1 for r in self.records if r.decision_type == "no_improvement")
        }
