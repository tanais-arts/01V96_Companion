#!/usr/bin/env python3
"""Request a parameter value from the 01V96 V2 over MIDI.

Usage:
    python scripts/get_param.py kInputEQ kEQLowG 0
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yamaha01v96.console import V2Console


def main() -> None:
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <group> <param_name> <channel>")
        sys.exit(1)
    group, name, channel = sys.argv[1], sys.argv[2], int(sys.argv[3])
    console = V2Console()
    try:
        value = console.request_parameter(group, name, channel)
        print(f"{group}.{name}[ch={channel}] = {value}")
    finally:
        console.close()


if __name__ == "__main__":
    main()
