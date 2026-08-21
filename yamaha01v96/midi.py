"""CoreMIDI I/O wrapper (via mido + python-rtmidi) for talking to the
Yamaha 01V96 V2 over its USB-MIDI ports (driver exposes 8 ports named
"YAMAHA 01V96 Port1".."Port8").
"""
from __future__ import annotations

import time

import mido

mido.set_backend("mido.backends.rtmidi")


def list_ports() -> dict:
    return {"inputs": mido.get_input_names(), "outputs": mido.get_output_names()}


class MidiConsole:
    def __init__(self, port_name: str | None = None, input_name: str | None = None):
        """port_name: MIDI output port to send to (defaults to the first
        port whose name contains '01V96'). input_name: MIDI input port to
        receive from (defaults to the matching input port)."""
        out_names = mido.get_output_names()
        in_names = mido.get_input_names()
        if port_name is None:
            candidates = [n for n in out_names if "01V96" in n]
            if not candidates:
                raise RuntimeError(f"No 01V96 MIDI port found. Available outputs: {out_names}")
            port_name = candidates[0]
        if input_name is None:
            input_name = port_name if port_name in in_names else next(
                (n for n in in_names if "01V96" in n), port_name
            )
        self.output = mido.open_output(port_name)
        self.input = mido.open_input(input_name)

    def send_sysex(self, data: bytes) -> None:
        # mido wants the sysex payload WITHOUT the leading F0 / trailing F7
        self.output.send(mido.Message("sysex", data=list(data[1:-1])))

    def receive(self, timeout: float = 1.0):
        """Poll for one incoming message within `timeout` seconds.
        Returns a mido.Message or None."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self.input.poll()
            if msg is not None:
                return msg
            time.sleep(0.01)
        return None

    def close(self) -> None:
        self.output.close()
        self.input.close()
