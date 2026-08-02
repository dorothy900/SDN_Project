#!/usr/bin/env python3
"""
Failure Handler - Detect failures and emergency reroute conditions.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence


class FailureHandler:
    """Track failed links and expose emergency reroute triggers."""

    def __init__(self):
        self.failed_links = set()

    def detect_failure(self, link_id: str, current_path: Optional[Sequence[str]] = None) -> Dict[str, object]:
        """Mark a link as failed and report whether the current path is impacted."""
        self.failed_links.add(link_id)
        path_contains_failure = False
        if current_path and len(current_path) > 1:
            path_links = {
                self._link_id(u, v)
                for u, v in zip(current_path, current_path[1:])
            }
            path_contains_failure = link_id in path_links

        return {
            "link_id": link_id,
            "failed": True,
            "path_contains_failure": path_contains_failure,
            "emergency_reroute": path_contains_failure,
        }

    def recover_link(self, link_id: str) -> None:
        """Mark a previously failed link as recovered."""
        self.failed_links.discard(link_id)

    def is_link_failed(self, link_id: str) -> bool:
        """Return whether a link is still marked failed."""
        return link_id in self.failed_links

    def get_failed_links(self) -> List[str]:
        """Return all currently failed links."""
        return sorted(self.failed_links)

    @staticmethod
    def _link_id(u: str, v: str) -> str:
        nodes = sorted([str(u), str(v)])
        return "%s-%s" % (nodes[0], nodes[1])
