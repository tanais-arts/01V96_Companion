#!/usr/bin/env python3
"""Launch the 01V96 V2 editor GUI (tkinter).

Usage:
    python scripts/run_gui.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yamaha01v96.gui import main

if __name__ == "__main__":
    main()
