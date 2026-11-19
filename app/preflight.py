#!/usr/bin/env python3
"""Standalone LH2 preflight; does not launch monitor.py."""
from lh2_monitor.preflight import main

if __name__ == "__main__":
    raise SystemExit(main())
