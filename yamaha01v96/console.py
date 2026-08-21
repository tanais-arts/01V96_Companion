"""High-level API: get/set 01V96 V2 parameters by human-readable name."""
from __future__ import annotations

import time

from . import sysex
from .midi import MidiConsole
from .params import ParameterMap


class V2Console:
    def __init__(
        self,
        port_name: str | None = None,
        device: int = 0,
        param_map: ParameterMap | None = None,
        midi=None,
    ):
        # `midi` lets callers inject a non-CoreMIDI backend (e.g. a GUI's
        # offline/simulation logger) instead of opening a real port.
        self.midi = midi if midi is not None else MidiConsole(port_name)
        self.params = param_map or ParameterMap()
        self.device = device

    def set_parameter(self, group: str, name: str, channel: int, value: int) -> None:
        pd = self.params.get(group, name)
        msg = sysex.build_parameter_change(
            pd.element, pd.param, channel, value, pd.min, pd.max, device=self.device,
            model_id=pd.model_id, addr_type=pd.addr_type,
        )
        self.midi.send_sysex(msg)

    def request_parameter(self, group: str, name: str, channel: int, timeout: float = 1.0):
        pd = self.params.get(group, name)
        req = sysex.build_parameter_request(
            pd.element, pd.param, channel, device=self.device,
            model_id=pd.model_id, addr_type=pd.addr_type,
        )
        self.midi.send_sysex(req)
        # On real hardware, unsolicited/echoed messages (e.g. someone moving
        # a different fader) can arrive between our request and its reply -
        # keep polling until we see one whose element/param/channel actually
        # match what we asked for, or the overall timeout budget runs out.
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            msg = self.midi.receive(timeout=remaining)
            if msg is None or msg.type != "sysex":
                return None
            full = bytes([0xF0, *msg.data, 0xF7])
            parsed = sysex.parse_parameter_change(
                full, pd.min, pd.max, model_id=pd.model_id, addr_type=pd.addr_type,
            )
            if parsed is None:
                continue
            if parsed["element"] != pd.element or parsed["param"] != pd.param or parsed["channel"] != channel:
                continue
            return parsed["value"]

    def get_scene_title(self, number: int, timeout: float = 1.0) -> str | None:
        req = sysex.build_title_request(sysex.FUNC_SCENE_TITLE, number, device=self.device)
        self.midi.send_sysex(req)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            msg = self.midi.receive(timeout=remaining)
            if msg is None or msg.type != "sysex":
                return None
            full = bytes([0xF0, *msg.data, 0xF7])
            parsed = sysex.parse_title_change(full)
            if parsed is None:
                continue
            if parsed["function"] != sysex.FUNC_SCENE_TITLE or parsed["number"] != number:
                continue
            return parsed["title"]

    def set_scene_title(self, number: int, title: str) -> None:
        msg = sysex.build_title_change(sysex.FUNC_SCENE_TITLE, number, title, device=self.device)
        self.midi.send_sysex(msg)

    def recall_scene(self, number: int) -> None:
        """Recall scene memory `number` (0-99; 0 = current scene) on the console."""
        msg = sysex.build_function_call(sysex.FUNC_SCENE_RECALL, number, device=self.device)
        self.midi.send_sysex(msg)

    def store_scene(self, number: int) -> None:
        """Store the console's current state into scene memory `number` (1-99)."""
        msg = sysex.build_function_call(sysex.FUNC_SCENE_STORE, number, device=self.device)
        self.midi.send_sysex(msg)

    def clear_scene(self, number: int) -> None:
        """Clear scene memory `number` (1-99)."""
        msg = sysex.build_function_clear(sysex.FUNC_SCENE_CLEAR, number, device=self.device)
        self.midi.send_sysex(msg)

    def _collect_bulk_dump(self, expected_data_name: bytes, timeout: float) -> bytes | None:
        """Receive and reassemble a (possibly multi-block) bulk dump, then
        unpack it back to raw 8-bit bytes. Returns None on timeout/mismatch."""
        blocks: dict[int, bytes] = {}
        block_total = None
        while block_total is None or len(blocks) < block_total + 1:
            msg = self.midi.receive(timeout=timeout)
            if msg is None or msg.type != "sysex":
                return None
            full = bytes([0xF0, *msg.data, 0xF7])
            parsed = sysex.parse_bulk_dump(full)
            if parsed is None or parsed["data_name"] != expected_data_name:
                continue
            block_total = parsed["block_total"]
            blocks[parsed["block_current"]] = parsed["payload"]
        ordered = b"".join(blocks[i] for i in range(block_total + 1))
        return sysex.unpack_8to7(ordered)

    def request_scene_dump(self, number: int, timeout: float = 2.0) -> bytes | None:
        """Request & receive a Scene memory bulk dump (spec 5.8.2.1/.2).

        `number`: 0-99 (scene), sysex.SCENE_EDIT_BUFFER (256) or
        sysex.SCENE_UNDO_BUFFER (8192). Returns the raw scene data bytes
        (already unpacked from the 7-bit bulk format), or None on timeout.
        """
        data_name = sysex.scene_data_name(number)
        self.midi.send_sysex(sysex.build_bulk_dump_request(data_name, device=self.device))
        return self._collect_bulk_dump(data_name, timeout=timeout)

    def send_scene_dump(self, number: int, data: bytes) -> None:
        """Upload (restore) raw scene data to scene memory `number`."""
        data_name = sysex.scene_data_name(number)
        msg = sysex.build_bulk_dump(data_name, sysex.pack_7to8(data), device=self.device)
        self.midi.send_sysex(msg)

    def request_setup_dump(self, timeout: float = 2.0) -> bytes | None:
        """Request & receive the Setup memory ("Current") bulk dump (spec 5.8.2.3/.4)."""
        self.midi.send_sysex(sysex.build_bulk_dump_request(sysex.SETUP_DATA_NAME, device=self.device))
        return self._collect_bulk_dump(sysex.SETUP_DATA_NAME, timeout=timeout)

    def send_setup_dump(self, data: bytes) -> None:
        """Upload (restore) raw setup data to the console's current setup."""
        msg = sysex.build_bulk_dump(sysex.SETUP_DATA_NAME, sysex.pack_7to8(data), device=self.device)
        self.midi.send_sysex(msg)

    def close(self) -> None:
        self.midi.close()
