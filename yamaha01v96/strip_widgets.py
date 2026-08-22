"""Compact "channel strip" widgets (round knobs, LED toggles, linear fader)
used by the "Tranche" tab in gui.py - a single-window, Max-for-Live-ish
overview of one channel's Dynamics/EQ/AUX/Bus/Fader settings, instead of the
raw parameter sliders used everywhere else in the app.

All widgets share the same small API expected by App (see gui.py):
`.group`, `.name`, `.get_value()` (re-read from the console) and
`.set_raw_value(value)` (used by get_value's callback) - this lets them
participate in App.read_all()/copy-paste exactly like ParamRow does.
"""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk

from . import converters
from .params import ParamDef


def _enum_options(pd: ParamDef) -> list[str] | None:
    if not pd.comment or "," not in pd.comment:
        return None
    labels = [s.strip() for s in pd.comment.split(",")]
    if len(labels) == (pd.max - pd.min + 1) and all(labels):
        return labels
    return None


class Knob(tk.Frame):
    """Rotary knob bound to one ParamDef. Click-drag vertically (or scroll)
    to change the value; always sends immediately, like a real pot."""

    SIZE = 40
    SWEEP_DEG = 270
    SENSITIVITY = 150  # pixels of vertical drag needed to cover the full range

    def __init__(self, app, parent: tk.Widget, group: str, name: str, label: str):
        super().__init__(parent)
        self.app = app
        self.group = group
        self.name = name
        self.pd: ParamDef = app.params.get(group, name)
        self.value = self.pd.default
        self._drag_start_y: int | None = None
        self._drag_start_value: int | None = None

        ttk.Label(self, text=label, font=("", 8), anchor="center").pack()
        self.canvas = tk.Canvas(self, width=self.SIZE, height=self.SIZE, highlightthickness=0)
        self.canvas.pack()
        self.value_label = ttk.Label(self, text="", font=("", 8), anchor="center", width=8)
        self.value_label.pack()

        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda _e: self._nudge(1))
        self.canvas.bind("<Button-5>", lambda _e: self._nudge(-1))

        self._redraw()
        app.rows.append(self)

    def _angle_for(self, value: int) -> float:
        span = self.pd.max - self.pd.min
        frac = 0.0 if span == 0 else (value - self.pd.min) / span
        return math.radians(135 - frac * self.SWEEP_DEG)

    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        s = self.SIZE
        pad = 3
        c.create_oval(pad, pad, s - pad, s - pad, fill="#e0e0e0", outline="#888")
        angle = self._angle_for(self.value)
        cx, cy, r = s / 2, s / 2, s / 2 - pad - 3
        c.create_line(
            cx, cy, cx + r * math.cos(angle), cy - r * math.sin(angle),
            width=2, fill="#c33",
        )
        self.value_label.config(text=self._display_text())

    def _display_text(self) -> str:
        converted = converters.raw_to_display(self.pd, self.value)
        return converted if converted is not None else str(self.value)

    def _on_press(self, event: tk.Event) -> None:
        self._drag_start_y = event.y
        self._drag_start_value = self.value

    def _on_drag(self, event: tk.Event) -> None:
        if self._drag_start_y is None or self._drag_start_value is None:
            return
        span = self.pd.max - self.pd.min
        delta = self._drag_start_y - event.y
        self._set_value(int(round(self._drag_start_value + delta * span / self.SENSITIVITY)))

    def _on_wheel(self, event: tk.Event) -> None:
        self._nudge(1 if event.delta > 0 else -1)

    def _nudge(self, direction: int) -> None:
        self._set_value(self.value + direction)

    def _set_value(self, value: int) -> None:
        value = max(self.pd.min, min(self.pd.max, value))
        if value == self.value:
            return
        self.value = value
        self._redraw()
        self.app.set_parameter(self.group, self.name, self.value)

    def get_value(self) -> None:
        self.app.request_parameter(self.group, self.name, on_result=self.set_raw_value)

    def set_raw_value(self, value: int) -> None:
        self.value = max(self.pd.min, min(self.pd.max, value))
        self._redraw()


class Toggle(tk.Frame):
    """LED-style square button bound to a boolean (min=0, max=1) ParamDef."""

    def __init__(self, app, parent: tk.Widget, group: str, name: str, label: str):
        super().__init__(parent)
        self.app = app
        self.group = group
        self.name = name
        self.pd: ParamDef = app.params.get(group, name)
        self.value = self.pd.default

        ttk.Label(self, text=label, font=("", 8), anchor="center").pack()
        self.canvas = tk.Canvas(self, width=32, height=18, highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_click)

        self._redraw()
        app.rows.append(self)

    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        on = self.value >= self.pd.max
        c.create_rectangle(1, 1, 31, 17, fill=("#2a2" if on else "#555"), outline="#222")

    def _on_click(self, _event: tk.Event) -> None:
        self.value = self.pd.min if self.value >= self.pd.max else self.pd.max
        self._redraw()
        self.app.set_parameter(self.group, self.name, self.value)

    def get_value(self) -> None:
        self.app.request_parameter(self.group, self.name, on_result=self.set_raw_value)

    def set_raw_value(self, value: int) -> None:
        self.value = value
        self._redraw()


class EnumSelector(tk.Frame):
    """Compact label + combobox for small enum parameters (Type, Mode...)."""

    def __init__(self, app, parent: tk.Widget, group: str, name: str, label: str):
        super().__init__(parent)
        self.app = app
        self.group = group
        self.name = name
        self.pd: ParamDef = app.params.get(group, name)
        self.labels = _enum_options(self.pd) or [str(v) for v in range(self.pd.min, self.pd.max + 1)]
        self.var = tk.StringVar(value=self.labels[self.pd.default - self.pd.min])

        ttk.Label(self, text=label, font=("", 8), anchor="center").pack()
        combo = ttk.Combobox(self, textvariable=self.var, values=self.labels, state="readonly", width=9)
        combo.pack()
        combo.bind("<<ComboboxSelected>>", lambda _e: self._on_change())

        app.rows.append(self)

    def _on_change(self) -> None:
        value = self.pd.min + self.labels.index(self.var.get())
        self.app.set_parameter(self.group, self.name, value)

    def get_value(self) -> None:
        self.app.request_parameter(self.group, self.name, on_result=self.set_raw_value)

    def set_raw_value(self, value: int) -> None:
        self.var.set(self.labels[value - self.pd.min])


class Fader(tk.Frame):
    """Linear vertical fader bound to a ParamDef (e.g. kInputFader.kFader)."""

    def __init__(self, app, parent: tk.Widget, group: str, name: str, label: str, length: int = 240):
        super().__init__(parent)
        self.app = app
        self.group = group
        self.name = name
        self.pd: ParamDef = app.params.get(group, name)
        self.var = tk.IntVar(value=self.pd.default)

        ttk.Label(self, text=label, font=("", 9, "bold"), anchor="center").pack()
        # from_ at the top (max = loudest), to at the bottom (min = quietest).
        ttk.Scale(
            self, from_=self.pd.max, to=self.pd.min, orient="vertical",
            variable=self.var, length=length, command=self._on_change,
        ).pack()
        self.value_label = ttk.Label(self, text="", font=("", 8), anchor="center", width=10)
        self.value_label.pack()

        self._redraw()
        app.rows.append(self)

    def _display_text(self) -> str:
        converted = converters.raw_to_display(self.pd, self.var.get())
        return converted if converted is not None else str(self.var.get())

    def _redraw(self) -> None:
        self.value_label.config(text=self._display_text())

    def _on_change(self, _value: str) -> None:
        self.app.set_parameter(self.group, self.name, int(float(self.var.get())))
        self._redraw()

    def get_value(self) -> None:
        self.app.request_parameter(self.group, self.name, on_result=self.set_raw_value)

    def set_raw_value(self, value: int) -> None:
        self.var.set(value)
        self._redraw()
