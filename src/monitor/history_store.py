#!/usr/bin/env python3
"""
History Store - Store historical network data
"""
import csv
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


class HistoryStore:
    """Store historical network state data."""

    def __init__(self, max_entries: int = 1000, output_dir: Path = Path("results/stage2")):
        self.max_entries = max_entries
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.data: deque = deque(maxlen=max_entries)

    def add_entry(self, timestamp: datetime, data: dict):
        self.data.append({'timestamp': timestamp, **data})

    def get_recent_entries(self, count: int = 100):
        return list(self.data)[-count:]

    def save_to_csv(self, filename: str = "history_window_test.csv"):
        filepath = self.output_dir / filename
        with filepath.open('w', newline='', encoding='utf-8') as f:
            if self.data:
                writer = csv.DictWriter(f, fieldnames=self.data[0].keys())
                writer.writeheader()
                writer.writerows(self.data)
