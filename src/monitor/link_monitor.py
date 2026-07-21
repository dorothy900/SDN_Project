#!/usr/bin/env python3
"""
Link Monitor - Monitor individual links
Week 2 Day 4: Detect link up/down events
"""
import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Tuple

from .models import LinkStatistics


class LinkEvent:
    """Represents a link status change event."""
    
    def __init__(
        self,
        timestamp: datetime,
        link_id: str,
        old_status: str,
        new_status: str,
    ):
        self.timestamp = timestamp
        self.link_id = link_id
        self.old_status = old_status
        self.new_status = new_status
    
    def __str__(self):
        return f"[{self.timestamp}] Link {self.link_id}: {self.old_status} -> {self.new_status}"


class LinkMonitor:
    """
    Monitor individual link statistics and track status changes.
    Detects link up/down events.
    """

    def __init__(self, output_dir: Path = Path("results/stage2")):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.link_statuses: Dict[str, str] = {}  # link_id -> status ("up" or "down")
        self.link_stats: Dict[str, Optional[LinkStatistics]] = {}
        self.link_events: List[LinkEvent] = []
        self.event_count = 0
    
    def update_link_stats(self, link_stats: LinkStatistics) -> None:
        """
        Update link statistics and detect status changes.
        
        Args:
            link_stats: Link statistics to update
        """
        link_id = link_stats.link_id
        old_status = self.link_statuses.get(link_id, "unknown")
        new_status = link_stats.status
        
        # Check if status changed
        if old_status != new_status:
            event = LinkEvent(
                timestamp=link_stats.timestamp,
                link_id=link_id,
                old_status=old_status,
                new_status=new_status,
            )
            self.link_events.append(event)
            self.event_count += 1
        
        self.link_statuses[link_id] = new_status
        self.link_stats[link_id] = link_stats
    
    def set_link_status(self, link_id: str, status: str, timestamp: Optional[datetime] = None) -> None:
        """
        Explicitly set a link's status (for manual testing or external updates).
        
        Args:
            link_id: Link identifier
            status: New status ("up" or "down")
            timestamp: Optional event timestamp
        """
        old_status = self.link_statuses.get(link_id, "unknown")
        ts = timestamp or datetime.now()
        
        if old_status != status:
            event = LinkEvent(ts, link_id, old_status, status)
            self.link_events.append(event)
            self.event_count += 1
        
        self.link_statuses[link_id] = status
    
    def get_link_status(self, link_id: str) -> Optional[str]:
        """Get current status of a link."""
        return self.link_statuses.get(link_id)
    
    def get_link_stats(self, link_id: str) -> Optional[LinkStatistics]:
        """Get latest statistics for a link."""
        return self.link_stats.get(link_id)
    
    def get_all_link_stats(self) -> Dict[str, Optional[LinkStatistics]]:
        """Get statistics for all links."""
        return self.link_stats.copy()
    
    def get_all_statuses(self) -> Dict[str, str]:
        """Get status of all links."""
        return self.link_statuses.copy()
    
    def get_events_since(self, index: int) -> List[LinkEvent]:
        """Get events starting from a specific index."""
        return self.link_events[index:]
    
    def get_recent_events(self, count: int = 10) -> List[LinkEvent]:
        """Get N most recent events."""
        return self.link_events[-count:]
    
    def save_events_to_csv(self, filename: str = "link_status_events.csv") -> None:
        """
        Save link status change events to CSV.
        Week 2 Day 4 output.
        """
        filepath = self.output_dir / filename
        
        with filepath.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp",
                "link_id",
                "old_status",
                "new_status",
                "event_number",
            ])
            
            for i, event in enumerate(self.link_events, 1):
                writer.writerow([
                    event.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"),
                    event.link_id,
                    event.old_status,
                    event.new_status,
                    i,
                ])
