#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Flow control interrupts for human-in-the-loop gates."""


class GateWaitInterrupt(Exception):
    """Raised when a gate pauses the agent loop until user input."""
