#!/usr/bin/env python3
"""
Failure Handler - Handle link/node failures
"""
class FailureHandler:
    """Detect and handle failures."""

    def __init__(self):
        self.failed_links = set()

    def detect_failure(self, link_id: str):
        self.failed_links.add(link_id)

    def is_link_failed(self, link_id: str) -> bool:
        return link_id in self.failed_links

    def get_failed_links(self):
        return list(self.failed_links)
