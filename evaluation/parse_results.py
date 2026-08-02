#!/usr/bin/env python3
"""
Parse Results - Convert raw experiment outputs into structured records.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List


class ResultParser:
    """Parse raw CSV experiment outputs and simple OVS-style text dumps."""

    def __init__(self):
        pass

    def parse_csv(self, filepath: str) -> List[Dict[str, object]]:
        """Load a CSV file and coerce common numeric/boolean fields."""
        path = Path(filepath)
        with path.open("r", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        return [self._coerce_row(row) for row in rows]

    def parse_directory(self, directory: str) -> List[Dict[str, object]]:
        """Load all CSV files within a directory tree."""
        base = Path(directory)
        rows: List[Dict[str, object]] = []
        for path in sorted(base.rglob("*.csv")):
            rows.extend(self.parse_csv(str(path)))
        return rows

    def parse_ovs_dump(self, filepath: str) -> List[Dict[str, object]]:
        """Parse a minimal OVS-style dump into rule records."""
        path = Path(filepath)
        records: List[Dict[str, object]] = []
        if not path.exists():
            return records
        current_switch = None
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.endswith(":"):
                current_switch = stripped[:-1]
                continue
            records.append({"switch": current_switch, "rule": stripped})
        return records

    @staticmethod
    def _coerce_row(row: Dict[str, str]) -> Dict[str, object]:
        """Convert common CSV string values into bool/int/float where possible."""
        numeric_fields = {
            "run",
            "sample",
            "offered_load_mbps",
            "delay_ms",
            "throughput_mbps",
            "packet_loss",
            "flow_updates",
            "decision_time_ms",
            "failure_sample",
            "recovery_sample",
        }
        boolean_fields = {
            "reroute",
            "measurement_stale",
            "failure_active",
            "recovered",
            "switched_back",
            "active",
        }

        result: Dict[str, object] = {}
        for key, value in row.items():
            if value is None:
                result[key] = value
                continue
            if key in boolean_fields:
                result[key] = value == "True"
            elif key in numeric_fields and value != "":
                if key in {"run", "sample", "flow_updates", "failure_sample", "recovery_sample"}:
                    result[key] = int(float(value))
                else:
                    result[key] = float(value)
            else:
                result[key] = value
        return result
