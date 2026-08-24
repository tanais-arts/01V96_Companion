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

    def __init__(self, app, parent: tk.Widget, group: str, name: str, label: str, channel: int | None = None, ui_scale: float = 1.0):
        super().__init__(parent)
        self.app = app
        self.group = group
        self.name = name
        self.channel = channel  # None = shared channel selector, else a fixed hardware channel (MIX tab)
        self.pd: ParamDef = app.params.get(group, name)
        self.value = self.pd.default
        self._stale = False
        self.selected = False
        self._drag_start_y: int | None = None
        self._drag_start_value: int | None = None
        self.size = round(self.SIZE * ui_scale)
        font_size = max(1, round(8 * ui_scale))

        ttk.Label(self, text=label, font=("", font_size), anchor="center").pack()
        self.canvas = tk.Canvas(self, width=self.size, height=self.size, highlightthickness=0)
        self.canvas.pack()
        self.value_label = ttk.Label(self, text="", font=("", font_size), anchor="center", width=8)
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
        # +90° : le centre du sweep pointe vers midi (12h), pas 3h.
        return math.radians(225 - frac * self.SWEEP_DEG)

    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        s = self.size
        pad = 3
        fill = "#e33" if self.selected else "#e0e0e0"
        outline = "#a00" if self.selected else "#888"
        c.create_oval(pad, pad, s - pad, s - pad, fill=fill, outline=outline, width=(2 if self.selected else 1))
        angle = self._angle_for(self.value)
        cx, cy, r = s / 2, s / 2, s / 2 - pad - 3
        c.create_line(
            cx, cy, cx + r * math.cos(angle), cy - r * math.sin(angle),
            width=2, fill="#c33",
        )
        self.value_label.config(text=self._display_text(), foreground=("#e80" if self._stale else ""))

    def _display_text(self) -> str:
        converted = converters.raw_to_display(self.pd, self.value)
        return converted if converted is not None else str(self.value)

    def _on_press(self, event: tk.Event) -> None:
        if self.app.strip_select_mode:
            self.selected = not self.selected
            self._redraw()
            return
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
        if self.app.strip_select_mode:
            return
        self._set_value(self.value + direction)

    def _set_value(self, value: int) -> None:
        value = max(self.pd.min, min(self.pd.max, value))
        if value == self.value:
            return
        self.value = value
        self._stale = False
        self._redraw()
        self.app.set_parameter(self.group, self.name, self.value, channel=self.channel)

    def get_value(self) -> None:
        self.app.request_parameter(
            self.group, self.name, on_result=self.set_raw_value, on_failure=self._on_read_failed, channel=self.channel,
        )

    def set_raw_value(self, value: int) -> None:
        self.value = max(self.pd.min, min(self.pd.max, value))
        self._stale = False
        self._redraw()

    def push_value(self) -> None:
        self.app.set_parameter(self.group, self.name, self.value, channel=self.channel)

    def _on_read_failed(self) -> None:
        self._stale = True
        self._redraw()


class Toggle(tk.Frame):
    """LED-style square button bound to a boolean (min=0, max=1) ParamDef."""

    def __init__(self, app, parent: tk.Widget, group: str, name: str, label: str, channel: int | None = None, ui_scale: float = 1.0):
        super().__init__(parent)
        self.app = app
        self.group = group
        self.name = name
        self.channel = channel  # None = shared channel selector, else a fixed hardware channel (MIX tab)
        self.pd: ParamDef = app.params.get(group, name)
        self.value = self.pd.default
        self._stale = False
        self.selected = False
        self._tw = round(32 * ui_scale)
        self._th = round(18 * ui_scale)
        font_size = max(1, round(8 * ui_scale))

        ttk.Label(self, text=label, font=("", font_size), anchor="center").pack()
        self.canvas = tk.Canvas(self, width=self._tw, height=self._th, highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_click)

        self._redraw()
        app.rows.append(self)

    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        w, h = self._tw - 1, self._th - 1
        if self.selected:
            c.create_rectangle(1, 1, w, h, fill="#e33", outline="#a00", width=2)
            return
        on = self.value >= self.pd.max
        outline, width = ("#e80", 2) if self._stale else ("#222", 1)
        c.create_rectangle(1, 1, w, h, fill=("#2a2" if on else "#555"), outline=outline, width=width)

    def _on_click(self, _event: tk.Event) -> None:
        if self.app.strip_select_mode:
            self.selected = not self.selected
            self._redraw()
            return
        self.value = self.pd.min if self.value >= self.pd.max else self.pd.max
        self._stale = False
        self._redraw()
        self.app.set_parameter(self.group, self.name, self.value, channel=self.channel)

    def get_value(self) -> None:
        self.app.request_parameter(
            self.group, self.name, on_result=self.set_raw_value, on_failure=self._on_read_failed, channel=self.channel,
        )

    def set_raw_value(self, value: int) -> None:
        self.value = value
        self._stale = False
        self._redraw()

    def push_value(self) -> None:
        self.app.set_parameter(self.group, self.name, self.value, channel=self.channel)

    def _on_read_failed(self) -> None:
        self._stale = True
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
        self.selected = False

        self.label = ttk.Label(self, text=label, font=("", 8), anchor="center")
        self.label.pack()
        combo = ttk.Combobox(self, textvariable=self.var, values=self.labels, state="readonly", width=9)
        combo.pack()
        combo.bind("<<ComboboxSelected>>", lambda _e: self._on_change())
        combo.bind("<Button-1>", self._on_combo_click)

        app.rows.append(self)

    def _on_combo_click(self, _event: tk.Event) -> str | None:
        if self.app.strip_select_mode:
            self.selected = not self.selected
            self._redraw()
            return "break"
        return None

    def _redraw(self) -> None:
        self.label.config(foreground="#c00" if self.selected else "")

    def _on_change(self) -> None:
        value = self.pd.min + self.labels.index(self.var.get())
        self.app.set_parameter(self.group, self.name, value)

    def get_value(self) -> None:
        self.app.request_parameter(self.group, self.name, on_result=self.set_raw_value, on_failure=self._on_read_failed)

    def _on_read_failed(self) -> None:
        pass  # combobox has no compact way to flag staleness; failures still show in the log panel

    def set_raw_value(self, value: int) -> None:
        self.var.set(self.labels[value - self.pd.min])

    def push_value(self) -> None:
        self._on_change()


class Fader(tk.Frame):
    """Linear vertical fader bound to a ParamDef (e.g. kInputFader.kFader)."""

    def __init__(
        self, app, parent: tk.Widget, group: str, name: str, label: str, length: int = 240,
        channel: int | None = None, ui_scale: float = 1.0, label_var: tk.StringVar | None = None,
    ):
        super().__init__(parent)
        self.app = app
        self.group = group
        self.name = name
        self.channel = channel  # None = shared channel selector, else a fixed hardware channel (MIX tab)
        self.pd: ParamDef = app.params.get(group, name)
        self.var = tk.IntVar(value=self.pd.default)
        self.selected = False
        title_font_size = max(1, round(9 * ui_scale))
        value_font_size = max(1, round(8 * ui_scale))

        if label_var is not None:
            self.title_label = ttk.Label(self, textvariable=label_var, font=("", title_font_size, "bold"), anchor="center")
        else:
            self.title_label = ttk.Label(self, text=label, font=("", title_font_size, "bold"), anchor="center")
        self.title_label.pack()
        # from_ at the top (max = loudest), to at the bottom (min = quietest).
        self.scale = ttk.Scale(
            self, from_=self.pd.max, to=self.pd.min, orient="vertical",
            variable=self.var, length=length, command=self._on_change,
        )
        self.scale.pack()
        self.scale.bind("<Button-1>", self._on_scale_click)
        self.value_label = ttk.Label(self, text="", font=("", value_font_size), anchor="center", width=10)
        self.value_label.pack()

        self._stale = False
        self._redraw()
        app.rows.append(self)

    def _on_scale_click(self, _event: tk.Event) -> str | None:
        if self.app.strip_select_mode:
            self.selected = not self.selected
            self._redraw()
            return "break"
        return None

    def _display_text(self) -> str:
        converted = converters.raw_to_display(self.pd, self.var.get())
        return converted if converted is not None else str(self.var.get())

    def _redraw(self) -> None:
        self.value_label.config(text=self._display_text(), foreground=("#e80" if self._stale else ""))
        self.title_label.config(foreground=("#c00" if self.selected else ""))

    def _on_change(self, _value: str) -> None:
        self._stale = False
        self.app.set_parameter(self.group, self.name, int(float(self.var.get())), channel=self.channel)
        self._redraw()

    def get_value(self) -> None:
        self.app.request_parameter(
            self.group, self.name, on_result=self.set_raw_value, on_failure=self._on_read_failed, channel=self.channel,
        )

    def set_raw_value(self, value: int) -> None:
        self.var.set(value)
        self._stale = False
        self._redraw()

    def push_value(self) -> None:
        self.app.set_parameter(self.group, self.name, int(float(self.var.get())), channel=self.channel)

    def _on_read_failed(self) -> None:
        self._stale = True
        self._redraw()


class MixName(tk.Frame):
    """Editable 4-char short-name label for a FIXED channel (MIX tab
    overview) - reads/writes `{group}.{base_name}1-4`, joined. Double-click
    to edit inline, like the "Nom du canal" field on the Tranche tab."""

    def __init__(
        self, app, parent: tk.Widget, channel: int, group: str = "kInputChannelName",
        base_name: str = "kChannelNameShort", width: int = 4, ui_scale: float = 1.0,
    ):
        super().__init__(parent)
        self.app = app
        self.group = group
        self.name = base_name
        self.channel = channel
        self._names = [f"{base_name}{i}" for i in range(1, 5)]
        self._pds = [app.params.get(group, n) for n in self._names]
        self._chars = [32, 32, 32, 32]
        self._width = width
        # Toujours lisible : contrairement aux Knob/Toggle/Fader compacts de
        # la vue MIX, ce nom doit rester grand même quand ui_scale est petit.
        font_size = max(12, round(16 * ui_scale))
        self._font = ("", font_size, "bold")
        self.var = tk.StringVar(value=str(channel + 1))
        self.label = ttk.Label(self, textvariable=self.var, font=self._font, width=width, anchor="center")
        self.label.pack(fill="x")
        self.label.bind("<Double-Button-1>", self._start_edit)
        self.entry: ttk.Entry | None = None
        app.rows.append(self)

    def _redraw(self) -> None:
        text = "".join(chr(c) for c in self._chars).strip()
        self.var.set(text or str(self.channel + 1))

    def _start_edit(self, _event: tk.Event | None = None) -> None:
        if self.entry is not None:
            return
        self.label.pack_forget()
        self.entry = ttk.Entry(self, width=self._width, justify="center", font=self._font)
        self.entry.insert(0, "".join(chr(c) for c in self._chars).strip())
        self.entry.pack(fill="x")
        self.entry.focus_set()
        self.entry.select_range(0, "end")
        self.entry.bind("<Return>", self._commit_edit)
        self.entry.bind("<FocusOut>", self._commit_edit)
        self.entry.bind("<Escape>", lambda _e: self._end_edit())

    def _commit_edit(self, _event: tk.Event | None = None) -> None:
        if self.entry is None:
            return
        text = self.entry.get()[:4].ljust(4)
        for name, pd, ch in zip(self._names, self._pds, [ord(c) for c in text]):
            self.app.set_parameter(self.group, name, max(pd.min, min(pd.max, ch)), channel=self.channel)
        self._chars = [ord(c) for c in text]
        self._end_edit()

    def _end_edit(self) -> None:
        if self.entry is not None:
            self.entry.destroy()
            self.entry = None
        self._redraw()
        self.label.pack(fill="x")

    def get_value(self) -> None:
        for i, name in enumerate(self._names):
            self.app.request_parameter(
                self.group, name, on_result=lambda v, i=i: self._set_char(i, v), channel=self.channel,
            )

    def _set_char(self, index: int, value: int) -> None:
        self._chars[index] = value
        self._redraw()

    def push_value(self) -> None:
        for name, pd, ch in zip(self._names, self._pds, self._chars):
            self.app.set_parameter(self.group, name, max(pd.min, min(pd.max, ch)), channel=self.channel)
