"""Loads docs/parameter_map.json (extracted from the official
"01V96 V2 Parameter Change List.xls") and exposes a simple lookup API:
group name + parameter name -> ParamDef(element, param, min, max, default).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAP_PATH = Path(__file__).resolve().parents[1] / "docs" / "parameter_map.json"


def _to_int(value, default: int = 0) -> int:
    """Some rows use '-' or blank for min/max/default when not applicable."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ParamDef:
    group: str
    name: str
    model_id: int
    addr_type: int
    element: int
    param: int
    min: int
    max: int
    default: int
    comment: str
    max_ch: object


class ParameterMap:
    def __init__(self, path: Path = DEFAULT_MAP_PATH):
        with open(path) as f:
            raw = json.load(f)
        self._by_group_name: dict[tuple[str, str], ParamDef] = {}
        self.groups: dict[str, list[str]] = {}
        for group in raw:
            gname = group["element"]
            names = []
            for p in group["params"]:
                cf = p["change_format"]
                # cf = [F0,43,1n,3E,model_id,tt,ee,pp,cc,dd...,F7] - see sysex.py.
                # model_id/tt vary by group: e.g. 7F/01 (Universal/Edit Buffer)
                # for EQ/comp/routing, 0D/02 (01V96/Patch data) for channel names.
                model_id = int(cf[4], 16)
                addr_type = int(cf[5], 16)
                element = int(cf[6], 16)
                param_no = int(cf[7], 16)
                pd = ParamDef(
                    group=gname,
                    name=p["name"],
                    model_id=model_id,
                    addr_type=addr_type,
                    element=element,
                    param=param_no,
                    min=_to_int(p["min"]),
                    max=_to_int(p["max"]),
                    default=_to_int(p["default"]),
                    comment=p.get("comment", ""),
                    max_ch=group.get("max_ch"),
                )
                self._by_group_name[(gname, p["name"])] = pd
                names.append(p["name"])
            self.groups[gname] = names

    def get(self, group: str, name: str) -> ParamDef:
        return self._by_group_name[(group, name)]

    def find(self, name: str) -> list[ParamDef]:
        """Search a parameter by name only, across all groups."""
        return [pd for (_, n), pd in self._by_group_name.items() if n == name]
