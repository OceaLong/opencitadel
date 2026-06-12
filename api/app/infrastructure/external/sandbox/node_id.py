#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Resolve node identity for per-node sandbox quota buckets."""
from __future__ import annotations

import os
import socket


def resolve_node_id() -> str:
    """Return the node identifier used for quota bucketing."""
    k8s_node = os.environ.get("MANUS_NODE_NAME") or os.environ.get("NODE_NAME")
    if k8s_node:
        return k8s_node.strip()
    return socket.gethostname() or "unknown-node"
