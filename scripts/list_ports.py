#!/usr/bin/env python3
"""List available CoreMIDI input/output ports."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yamaha01v96.midi import list_ports

if __name__ == "__main__":
    ports = list_ports()
    print("Inputs:")
    for p in ports["inputs"]:
        print(" ", p)
    print("Outputs:")
    for p in ports["outputs"]:
        print(" ", p)
