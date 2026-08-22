"""Tkinter GUI for editing a Yamaha 01V96 V2 scene over USB-MIDI.

Covers the user's core needs: channel routing, channel names, and
compressor/gate/EQ/effect(reverb) settings. Can run in two modes:

- Connected: opens a real CoreMIDI port (via `MidiConsole`) and actually
  sends/receives SysEx to/from the console.
- Simulation (hors ligne): no MIDI port is opened at all; every SysEx
  message that would be sent is only logged (as hex) in the log panel.
  Useful to validate the GUI/parameter wiring without the hardware
  connected.

Run with: `python -m yamaha01v96.gui` (see scripts/run_gui.py too).
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

from . import sysex, converters
from .console import V2Console
from .midi import MidiConsole, list_ports
from .params import ParameterMap, ParamDef
from .strip_widgets import Knob, Toggle, EnumSelector, Fader

SIMULATION_LABEL = "-- Simulation hors ligne --"
DEFAULT_PORT_NAME = "YAMAHA 01V96 Port5"


class LoggingMidi:
    """Drop-in replacement for MidiConsole that only logs SysEx bytes."""

    def __init__(self, log_fn):
        self._log = log_fn

    def send_sysex(self, data: bytes) -> None:
        self._log(f"TX (simulation, non envoyé) : {data.hex(' ')}")

    def receive(self, timeout: float = 1.0):
        return None

    def close(self) -> None:
        pass


def _enum_options(pd: ParamDef) -> list[str] | None:
    """If pd.comment looks like a comma-separated list of labels whose count
    matches (max - min + 1), return that list of labels; else None."""
    if not pd.comment or "," not in pd.comment:
        return None
    labels = [s.strip() for s in pd.comment.split(",")]
    if len(labels) == (pd.max - pd.min + 1) and all(labels):
        return labels
    return None


class ParamRow:
    """One parameter's label + input widget + live value, wired to a
    ParameterMap entry. Builds/sends its own SysEx via the owning App."""

    def __init__(self, app: "App", parent: tk.Widget, group: str, name: str, label: str | None = None):
        self.app = app
        self.group = group
        self.name = name
        self.pd = app.params.get(group, name)
        self.var: tk.Variable
        self.enum_labels = _enum_options(self.pd)

        row = ttk.Frame(parent)
        row.pack(fill="x", padx=4, pady=2)
        ttk.Label(row, text=(label or name), width=22, anchor="w").pack(side="left")

        if self.pd.max - self.pd.min == 1 and not self.enum_labels:
            self.var = tk.IntVar(value=self.pd.default)
            ttk.Checkbutton(
                row, variable=self.var, onvalue=1, offvalue=0, command=self._on_change
            ).pack(side="left")
        elif self.enum_labels:
            self.var = tk.StringVar(value=self.enum_labels[self.pd.default - self.pd.min])
            cb = ttk.Combobox(
                row, textvariable=self.var, values=self.enum_labels, state="readonly", width=18
            )
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", lambda e: self._on_change())
        else:
            self.var = tk.IntVar(value=self.pd.default)
            scale = ttk.Scale(
                row, from_=self.pd.min, to=self.pd.max, orient="horizontal",
                variable=self.var, command=lambda v: self._on_scale(v),
            )
            scale.pack(side="left", fill="x", expand=True)
            self.value_label = ttk.Label(row, text=self._display_text(self.pd.default), width=16)
            self.value_label.pack(side="left")

        # Only show the raw "PRM TABLE #NN" comment when we have no real
        # unit conversion for this parameter (converted values already show
        # the raw index alongside, see _display_text).
        if self.pd.comment and hasattr(self, "value_label") and converters.raw_to_display(self.pd, self.pd.default) is None:
            ttk.Label(row, text=self.pd.comment, foreground="#666").pack(side="left", padx=6)
        elif self.pd.comment and not hasattr(self, "value_label"):
            ttk.Label(row, text=self.pd.comment, foreground="#666").pack(side="left", padx=6)

        ttk.Button(row, text="Lire", width=6, command=self.get_value).pack(side="right")
        app.rows.append(self)

    def _display_text(self, raw: int) -> str:
        converted = converters.raw_to_display(self.pd, raw)
        return converted if converted is not None else str(raw)

    def _on_scale(self, _value) -> None:
        self.value_label.config(text=self._display_text(int(float(self.var.get()))))
        self._on_change()

    def _raw_value(self) -> int:
        if self.enum_labels:
            return self.pd.min + self.enum_labels.index(self.var.get())
        return int(float(self.var.get()))

    def _on_change(self) -> None:
        self.app.set_parameter(self.group, self.name, self._raw_value())

    def set_raw_value(self, value: int) -> None:
        if self.enum_labels:
            self.var.set(self.enum_labels[value - self.pd.min])
        else:
            self.var.set(value)
            if hasattr(self, "value_label"):
                self.value_label.config(text=self._display_text(value))

    def get_value(self) -> None:
        self.app.request_parameter(self.group, self.name, on_result=self.set_raw_value)


class NameRow:
    """Composite control for a multi-character name (channel/bus/effect
    name), built from N consecutive per-character parameters
    (e.g. kChannelNameShort1..4 or kChannelNameLong1..16)."""

    def __init__(self, app: "App", parent: tk.Widget, group: str, base_name: str, count: int, label: str, show_read_button: bool = True):
        self.app = app
        self.group = group
        self.names = [f"{base_name}{i}" for i in range(1, count + 1)]
        self.pds = [app.params.get(group, n) for n in self.names]

        row = ttk.Frame(parent)
        row.pack(fill="x", padx=4, pady=2)
        self.frame = row
        ttk.Label(row, text=label, width=22, anchor="w").pack(side="left")
        self.var = tk.StringVar(value="")
        entry = ttk.Entry(row, textvariable=self.var, width=count + 2)
        entry.pack(side="left")
        ttk.Button(row, text="Envoyer", width=8, command=self.send).pack(side="left", padx=4)
        if show_read_button:
            ttk.Button(row, text="Lire", width=6, command=self.get_value).pack(side="right")
        app.rows.append(self)

    def send(self) -> None:
        text = self.var.get()[: len(self.names)].ljust(len(self.names))
        for name, pd, ch in zip(self.names, self.pds, [ord(c) for c in text]):
            self.app.set_parameter(self.group, name, max(pd.min, min(pd.max, ch)))

    def get_value(self) -> None:
        chars = [""] * len(self.names)

        def make_cb(i, name):
            def cb(value):
                chars[i] = chr(value) if 32 <= value < 127 else " "
                if all(c != "" for c in chars):
                    self.var.set("".join(chars))
            return cb

        for i, name in enumerate(self.names):
            self.app.request_parameter(self.group, name, on_result=make_cb(i, name))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Éditeur 01V96 V2 (remplacement SM2)")
        self.geometry("1480x760")

        self.params = ParameterMap()
        self.console: V2Console | None = None
        self.channel_var = tk.IntVar(value=1)
        self.effect_var = tk.IntVar(value=1)
        self.rows: list[ParamRow | NameRow] = []
        self.channel_clipboard: dict[tuple[str, str], int] | None = None
        self.strip_clipboard: dict[tuple[str, str], int] | None = None
        self.strip_select_mode = False
        self.scene_title_var = tk.StringVar(value="")

        self._build_connection_bar()
        self._build_channel_bar()
        self._build_tabs()
        self._build_log()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if self.port_var.get() != SIMULATION_LABEL:
            self._toggle_connect()

    # -- connection -----------------------------------------------------
    def _build_connection_bar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=6, pady=6)

        ttk.Label(bar, text="Port MIDI :").pack(side="left")
        outputs = list_ports()["outputs"]
        default_port = DEFAULT_PORT_NAME if DEFAULT_PORT_NAME in outputs else SIMULATION_LABEL
        self.port_var = tk.StringVar(value=default_port)
        self.port_combo = ttk.Combobox(
            bar, textvariable=self.port_var, values=[SIMULATION_LABEL] + outputs,
            state="readonly", width=30,
        )
        self.port_combo.pack(side="left", padx=4)

        self.connect_btn = ttk.Button(bar, text="Connecter", command=self._toggle_connect)
        self.connect_btn.pack(side="left", padx=4)

        self.status_label = ttk.Label(bar, text="Déconnecté (simulation)", foreground="#a60")
        self.status_label.pack(side="left", padx=10)

    def _toggle_connect(self) -> None:
        if self.console is not None:
            self.console.close()
            self.console = None
            self.connect_btn.config(text="Connecter")
            self.status_label.config(text="Déconnecté (simulation)", foreground="#a60")
            return

        port = self.port_var.get()
        try:
            if port == SIMULATION_LABEL:
                midi = LoggingMidi(self.log)
                self.console = V2Console(param_map=self.params, midi=midi)
                self.status_label.config(text="Simulation hors ligne (rien n'est envoyé)", foreground="#a60")
            else:
                midi = MidiConsole(port_name=port)
                self.console = V2Console(param_map=self.params, midi=midi)
                self.status_label.config(text=f"Connecté : {port}", foreground="#080")
        except Exception as exc:  # noqa: BLE001 - surfaced to the user directly
            messagebox.showerror("Connexion MIDI", str(exc))
            return
        self.connect_btn.config(text="Déconnecter")

    def _on_close(self) -> None:
        if self.console is not None:
            self.console.close()
        self.destroy()

    # -- shared channel/effect selector ---------------------------------
    def _build_channel_bar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=6)
        ttk.Label(bar, text="Canal d'entrée (1-32) :").pack(side="left")
        ttk.Spinbox(bar, from_=1, to=32, textvariable=self.channel_var, width=5).pack(side="left", padx=4)
        ttk.Label(bar, text="   Effet (1-4) :").pack(side="left")
        ttk.Spinbox(bar, from_=1, to=4, textvariable=self.effect_var, width=5).pack(side="left", padx=4)

    @property
    def channel(self) -> int:
        return self.channel_var.get() - 1

    @property
    def effect_channel(self) -> int:
        return self.effect_var.get() - 1

    # -- tabs -------------------------------------------------------------
    def _build_tabs(self) -> None:
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=6, pady=6)

        self._build_strip_tab(nb)
        self._build_scene_tab(nb)
        self._build_backup_tab(nb)
        self._build_copy_tab(nb)
        self._build_eq_tab(nb)
        self._build_dynamics_tab(nb)
        self._build_routing_tab(nb)
        self._build_name_tab(nb)
        self._build_effect_tab(nb)

    def _build_strip_tab(self, nb: ttk.Notebook) -> None:
        """One-window channel-strip overview (knobs/toggles), à la console
        analogique : Dynamique, EQ, envois AUX, envois bus, fader - tout ce
        qui utilise le canal d'entrée sélectionné ci-dessus."""
        tab = ttk.Frame(nb)
        nb.add(tab, text="Tranche")

        top = ttk.Frame(tab)
        top.pack(fill="x", padx=4, pady=(4, 0))
        name_row = NameRow(self, top, "kInputChannelName", "kChannelNameLong", 16, "Nom du canal", show_read_button=False)
        ttk.Button(name_row.frame, text="Tout lire", command=self.read_all).pack(side="left", padx=8)

        body = ttk.Frame(tab)
        body.pack(fill="both", expand=True, padx=4, pady=4)

        self._build_strip_dynamics(body)
        self._build_strip_eq(body)
        self._build_strip_aux(body)
        self._build_strip_bus(body)
        self._build_strip_fader(body)
        self._build_strip_copy_tools(body)

    def _build_strip_dynamics(self, parent: tk.Widget) -> None:
        frame = ttk.LabelFrame(parent, text="Dynamique")
        frame.pack(side="left", fill="y", padx=3)

        gate = ttk.Frame(frame)
        gate.pack(pady=4)
        gate_label = ttk.Label(gate, text="GATE", font=("", 9, "bold"))
        gate_label.grid(row=0, column=0, columnspan=3, pady=(0, 2))
        gate_on = Toggle(self, gate, "kInputGate", "kGateOn", "On")
        gate_on.grid(row=1, column=0, padx=2)
        gate_type = EnumSelector(self, gate, "kInputGate", "kGateType", "Type")
        gate_type.grid(row=1, column=1, columnspan=2, padx=2)
        gate_thr = Knob(self, gate, "kInputGate", "kGateThreshold", "Seuil")
        gate_thr.grid(row=2, column=0, padx=2, pady=2)
        gate_range = Knob(self, gate, "kInputGate", "kGateRange", "Range")
        gate_range.grid(row=2, column=1, padx=2, pady=2)
        gate_atk = Knob(self, gate, "kInputGate", "kGateAttack", "Atk")
        gate_atk.grid(row=2, column=2, padx=2, pady=2)
        gate_hold = Knob(self, gate, "kInputGate", "kGateHold", "Hold")
        gate_hold.grid(row=3, column=0, padx=2, pady=2)
        gate_decay = Knob(self, gate, "kInputGate", "kGateDecay", "Decay")
        gate_decay.grid(row=3, column=1, padx=2, pady=2)
        self._bind_section_select(
            gate_label, [gate_on, gate_type, gate_thr, gate_range, gate_atk, gate_hold, gate_decay],
        )

        ttk.Separator(frame).pack(fill="x", pady=6)

        comp = ttk.Frame(frame)
        comp.pack(pady=4)
        comp_label = ttk.Label(comp, text="COMP", font=("", 9, "bold"))
        comp_label.grid(row=0, column=0, columnspan=3, pady=(0, 2))
        comp_on = Toggle(self, comp, "kInputComp", "kCompOn", "On")
        comp_on.grid(row=1, column=0, padx=2)
        comp_type = EnumSelector(self, comp, "kInputComp", "kCompType", "Type")
        comp_type.grid(row=1, column=1, columnspan=2, padx=2)
        comp_thr = Knob(self, comp, "kInputComp", "kCompThreshold", "Seuil")
        comp_thr.grid(row=2, column=0, padx=2, pady=2)
        comp_ratio = Knob(self, comp, "kInputComp", "kCompRatio", "Ratio")
        comp_ratio.grid(row=2, column=1, padx=2, pady=2)
        comp_atk = Knob(self, comp, "kInputComp", "kCompAttack", "Atk")
        comp_atk.grid(row=2, column=2, padx=2, pady=2)
        comp_rel = Knob(self, comp, "kInputComp", "kCompRelease", "Rel")
        comp_rel.grid(row=3, column=0, padx=2, pady=2)
        comp_knee = Knob(self, comp, "kInputComp", "kCompKnee", "Knee")
        comp_knee.grid(row=3, column=1, padx=2, pady=2)
        comp_gain = Knob(self, comp, "kInputComp", "kCompGain", "Gain")
        comp_gain.grid(row=3, column=2, padx=2, pady=2)
        self._bind_section_select(
            comp_label, [comp_on, comp_type, comp_thr, comp_ratio, comp_atk, comp_rel, comp_knee, comp_gain],
        )

    def _build_strip_eq(self, parent: tk.Widget) -> None:
        frame = ttk.LabelFrame(parent, text="EQ")
        frame.pack(side="left", fill="y", padx=3)

        top = ttk.Frame(frame)
        top.pack(pady=4)
        Toggle(self, top, "kInputEQ", "kEQOn", "EQ On").grid(row=0, column=0, padx=2)
        Toggle(self, top, "kInputEQ", "kEQHPFOn", "HPF").grid(row=0, column=1, padx=2)
        Toggle(self, top, "kInputEQ", "kEQLPFOn", "LPF").grid(row=0, column=2, padx=2)

        for band, label in [("Low", "GRAVE"), ("LowMid", "BAS-MED"), ("HiMid", "HAUT-MED"), ("Hi", "AIGU")]:
            ttk.Separator(frame).pack(fill="x", pady=4)
            section = ttk.Frame(frame)
            section.pack(pady=2)
            band_label = ttk.Label(section, text=label, font=("", 9, "bold"))
            band_label.grid(row=0, column=0, columnspan=3, pady=(0, 2))
            gain_knob = Knob(self, section, "kInputEQ", f"kEQ{band}G", "Gain")
            gain_knob.grid(row=1, column=0, padx=2)
            freq_knob = Knob(self, section, "kInputEQ", f"kEQ{band}F", "Freq")
            freq_knob.grid(row=1, column=1, padx=2)
            q_knob = Knob(self, section, "kInputEQ", f"kEQ{band}Q", "Q")
            q_knob.grid(row=1, column=2, padx=2)
            self._bind_section_select(band_label, [gain_knob, freq_knob, q_knob])

    def _build_strip_aux(self, parent: tk.Widget) -> None:
        frame = ttk.LabelFrame(parent, text="Envois AUX")
        frame.pack(side="left", fill="y", padx=3)
        for i in range(1, 9):
            row, col = divmod(i - 1, 4)
            cell = ttk.Frame(frame)
            cell.grid(row=row, column=col, padx=3, pady=3)
            ttk.Label(cell, text=f"AUX {i}", font=("", 8, "bold")).pack()
            Toggle(self, cell, "kInputAUX", f"kAUX{i}On", "On").pack()
            Knob(self, cell, "kInputAUX", f"kAUX{i}Level", "Niv.").pack()

    def _build_strip_bus(self, parent: tk.Widget) -> None:
        frame = ttk.LabelFrame(parent, text="Envois bus")
        frame.pack(side="left", fill="y", padx=3)

        top = ttk.Frame(frame)
        top.pack(pady=4)
        Toggle(self, top, "kInputRouting", "kRoutingStereo", "Stéréo").grid(row=0, column=0, padx=2)
        Toggle(self, top, "kInputRouting", "kRoutingDirect", "Direct").grid(row=0, column=1, padx=2)

        ttk.Separator(frame).pack(fill="x", pady=4)
        grid = ttk.Frame(frame)
        grid.pack(pady=2)
        for i in range(1, 9):
            row, col = divmod(i - 1, 4)
            Toggle(self, grid, "kInputRouting", f"kRoutingBus{i}", f"Bus{i}").grid(
                row=row, column=col, padx=3, pady=3,
            )

    def _build_strip_fader(self, parent: tk.Widget) -> None:
        frame = ttk.LabelFrame(parent, text="Fader")
        frame.pack(side="left", fill="both", expand=True, padx=3)
        Toggle(self, frame, "kInputChannelOn", "kChannelOn", "On").pack(pady=4)
        Toggle(self, frame, "kInputPair", "kPair", "Paire").pack(pady=4)
        Knob(self, frame, "kInputChannelPan", "kChannelPan", "Pan").pack(pady=4)
        Fader(self, frame, "kInputFader", "kFader", "Niveau").pack(pady=4, fill="y", expand=True)

    def _build_strip_copy_tools(self, parent: tk.Widget) -> None:
        frame = ttk.LabelFrame(parent, text="Copier / Coller (tranche)")
        frame.pack(side="left", fill="y", padx=3)
        btns = ttk.Frame(frame)
        btns.pack(pady=4)
        ttk.Button(btns, text="COPY ALL", width=10, command=self._strip_copy_all).pack(side="left", padx=2)
        self.strip_copy_sel_btn = ttk.Button(btns, text="COPY SEL.", width=10, command=self._strip_copy_sel_toggle)
        self.strip_copy_sel_btn.pack(side="left", padx=2)
        ttk.Button(btns, text="PASTE TO...", width=10, command=self._strip_paste_to).pack(side="left", padx=2)
        self.strip_clipboard_label = ttk.Label(
            frame, text="Presse-papiers tranche : vide", foreground="#666", wraplength=140,
        )
        self.strip_clipboard_label.pack(anchor="w", padx=2, pady=(2, 4))

    def _build_scene_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Scène")
        ttk.Label(
            tab,
            text="Rappel/mémorisation de scène - indépendant du canal sélectionné ci-dessus.",
            foreground="#666", wraplength=760,
        ).pack(anchor="w", padx=4, pady=(4, 8))

        row = ttk.Frame(tab)
        row.pack(fill="x", padx=4, pady=4)
        ttk.Label(row, text="Numéro de scène :").pack(side="left")
        self.scene_var = tk.IntVar(value=1)
        ttk.Spinbox(row, from_=0, to=99, textvariable=self.scene_var, width=5).pack(side="left", padx=4)

        btns = ttk.Frame(tab)
        btns.pack(fill="x", padx=4, pady=4)
        ttk.Button(btns, text="Recall (rappel)", command=self._recall_scene).pack(side="left", padx=4)
        ttk.Button(btns, text="Store (mémoriser)", command=self._store_scene).pack(side="left", padx=4)
        ttk.Button(btns, text="Clear (effacer)", command=self._clear_scene).pack(side="left", padx=4)
        ttk.Button(btns, text="Tout lire", command=self.read_all).pack(side="left", padx=4)

        ttk.Label(
            tab,
            text="Recall : 0-99 (0 = scène courante). Store/Clear : 1-99. "
                 "Recall relit ensuite automatiquement tous les contrôles (Tout lire).",
            foreground="#666",
        ).pack(anchor="w", padx=4, pady=(8, 0))

        title_row = ttk.Frame(tab)
        title_row.pack(fill="x", padx=4, pady=(12, 4))
        ttk.Label(title_row, text="Nom de la scène :").pack(side="left")
        ttk.Entry(title_row, textvariable=self.scene_title_var, width=18).pack(side="left", padx=4)
        ttk.Button(title_row, text="Lire le nom", command=self._read_scene_title).pack(side="left", padx=4)
        ttk.Button(title_row, text="Écrire le nom", command=self._write_scene_title).pack(side="left", padx=4)

    def _build_backup_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Sauvegarde")
        ttk.Label(
            tab,
            text="Bulk dump (spec 5.8.2) : sauvegarde/restauration complète vers/depuis des "
                 "fichiers .syx bruts.",
            foreground="#666", wraplength=760,
        ).pack(anchor="w", padx=4, pady=(4, 8))

        ttk.Label(tab, text="Scène (une seule)", font=("", 10, "bold")).pack(anchor="w", padx=4)
        row = ttk.Frame(tab)
        row.pack(fill="x", padx=4, pady=4)
        ttk.Label(row, text="Numéro (0-99) :").pack(side="left")
        self.backup_scene_var = tk.IntVar(value=1)
        ttk.Spinbox(row, from_=0, to=99, textvariable=self.backup_scene_var, width=5).pack(side="left", padx=4)
        ttk.Button(row, text="Sauvegarder…", command=self._backup_scene).pack(side="left", padx=4)
        ttk.Button(row, text="Restaurer…", command=self._restore_scene).pack(side="left", padx=4)

        ttk.Separator(tab).pack(fill="x", pady=8)
        ttk.Label(tab, text="Bibliothèque de scènes complète (0-99)", font=("", 10, "bold")).pack(anchor="w", padx=4)
        row2 = ttk.Frame(tab)
        row2.pack(fill="x", padx=4, pady=4)
        ttk.Button(row2, text="Sauvegarder dans un dossier…", command=self._backup_all_scenes).pack(side="left", padx=4)
        ttk.Button(row2, text="Restaurer depuis un dossier…", command=self._restore_all_scenes).pack(side="left", padx=4)

        ttk.Separator(tab).pack(fill="x", pady=8)
        ttk.Label(
            tab, text="Setup (réglages console - hors bibliothèques et mémoires diverses)",
            font=("", 10, "bold"),
        ).pack(anchor="w", padx=4)
        row3 = ttk.Frame(tab)
        row3.pack(fill="x", padx=4, pady=4)
        ttk.Button(row3, text="Sauvegarder…", command=self._backup_setup).pack(side="left", padx=4)
        ttk.Button(row3, text="Restaurer…", command=self._restore_setup).pack(side="left", padx=4)

    def _build_copy_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Copier/Coller")
        ttk.Label(
            tab,
            text="Copie les réglages d'une tranche d'entrée (EQ, Dynamique, Routing) vers une autre. "
                 "N'inclut pas les paramètres de l'onglet Effet (partagés par slot d'effet, pas par canal).",
            foreground="#666", wraplength=760,
        ).pack(anchor="w", padx=4, pady=(4, 8))

        row = ttk.Frame(tab)
        row.pack(fill="x", padx=4, pady=4)
        ttk.Label(row, text="Canal source (1-32) :").pack(side="left")
        self.copy_src_var = tk.IntVar(value=1)
        ttk.Spinbox(row, from_=1, to=32, textvariable=self.copy_src_var, width=5).pack(side="left", padx=4)
        ttk.Label(row, text="Canal destination (1-32) :").pack(side="left", padx=(12, 0))
        self.copy_dst_var = tk.IntVar(value=2)
        ttk.Spinbox(row, from_=1, to=32, textvariable=self.copy_dst_var, width=5).pack(side="left", padx=4)

        self.copy_include_name_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            tab, text="Inclure le nom du canal", variable=self.copy_include_name_var,
        ).pack(anchor="w", padx=4, pady=(4, 0))

        ttk.Button(tab, text="Copier -> Coller directement", command=self._copy_paste_channel).pack(
            anchor="w", padx=4, pady=8,
        )

        ttk.Separator(tab).pack(fill="x", pady=8)
        ttk.Label(
            tab, text="Presse-papiers interne (pratique pour coller sur plusieurs canaux)",
            font=("", 10, "bold"),
        ).pack(anchor="w", padx=4)
        row2 = ttk.Frame(tab)
        row2.pack(fill="x", padx=4, pady=4)
        ttk.Button(row2, text="Copier le canal source", command=self._copy_channel_to_clipboard).pack(
            side="left", padx=4,
        )
        ttk.Button(row2, text="Coller sur la destination", command=self._paste_clipboard_to_channel).pack(
            side="left", padx=4,
        )
        self.channel_clipboard_label = ttk.Label(tab, text="Presse-papiers : vide", foreground="#666")
        self.channel_clipboard_label.pack(anchor="w", padx=4, pady=(4, 0))

    def _build_eq_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="EQ")
        ParamRow(self, tab, "kInputEQ", "kEQOn", "EQ actif")
        ParamRow(self, tab, "kInputEQ", "kEQMode", "Mode")
        ParamRow(self, tab, "kInputEQ", "kEQHPFOn", "HPF actif")
        ParamRow(self, tab, "kInputEQ", "kEQLPFOn", "LPF actif")
        for band, label in [("Low", "Grave"), ("LowMid", "Bas-médium"), ("HiMid", "Haut-médium"), ("Hi", "Aigu")]:
            ttk.Separator(tab).pack(fill="x", pady=4)
            ttk.Label(tab, text=label, font=("", 10, "bold")).pack(anchor="w", padx=4)
            ParamRow(self, tab, "kInputEQ", f"kEQ{band}G", "Gain")
            ParamRow(self, tab, "kInputEQ", f"kEQ{band}F", "Fréquence (index)")
            ParamRow(self, tab, "kInputEQ", f"kEQ{band}Q", "Q (index)")

    def _build_dynamics_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Dynamique")
        ttk.Label(tab, text="Compresseur", font=("", 10, "bold")).pack(anchor="w", padx=4)
        for name, label in [
            ("kCompOn", "Actif"), ("kCompType", "Type"), ("kCompThreshold", "Seuil"),
            ("kCompRatio", "Ratio (index)"), ("kCompAttack", "Attaque (ms)"),
            ("kCompRelease", "Release (index)"), ("kCompKnee", "Knee (index)"),
            ("kCompGain", "Gain (index)"), ("kCompLink", "Lien stéréo"),
        ]:
            ParamRow(self, tab, "kInputComp", name, label)
        ttk.Separator(tab).pack(fill="x", pady=4)
        ttk.Label(tab, text="Gate", font=("", 10, "bold")).pack(anchor="w", padx=4)
        for name, label in [
            ("kGateOn", "Actif"), ("kGateType", "Type"), ("kGateThreshold", "Seuil"),
            ("kGateRange", "Plage"), ("kGateAttack", "Attaque (ms)"),
            ("kGateHold", "Hold (index)"), ("kGateDecay", "Decay (index)"),
            ("kGateKeyIn", "Key In"), ("kGateLink", "Lien stéréo"),
        ]:
            ParamRow(self, tab, "kInputGate", name, label)

    def _build_routing_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Routing")
        ParamRow(self, tab, "kInputRouting", "kRoutingStereo", "Sortie stéréo")
        ParamRow(self, tab, "kInputRouting", "kRoutingPan", "Pan actif")
        ParamRow(self, tab, "kInputRouting", "kRoutingDirect", "Direct out")
        for i in range(1, 9):
            ParamRow(self, tab, "kInputRouting", f"kRoutingBus{i}", f"Bus {i}")
        ttk.Separator(tab).pack(fill="x", pady=4)
        ParamRow(self, tab, "kInputFader", "kFader", "Fader (index)")
        ParamRow(self, tab, "kInputInsert", "kInsertOn", "Insert actif")

    def _build_name_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Noms")
        NameRow(self, tab, "kInputChannelName", "kChannelNameShort", 4, "Nom court (4 car.)")
        NameRow(self, tab, "kInputChannelName", "kChannelNameLong", 16, "Nom long (16 car.)")

    def _build_effect_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Effet / Reverb")
        ttk.Label(
            tab,
            text="Le canal utilisé ici est le sélecteur \"Effet (1-4)\" ci-dessus, pas le canal d'entrée.",
            foreground="#666", wraplength=760,
        ).pack(anchor="w", padx=4, pady=(0, 4))
        NameRow(self, tab, "kEffect", "kEffectTitle", 16, "Titre (16 car.)")
        ParamRow(self, tab, "kEffect", "kEffectType", "Type d'effet (index)")
        ParamRow(self, tab, "kEffect", "kEffectMix", "Mix (%)")
        ParamRow(self, tab, "kEffect", "kEffectBypass", "Bypass")
        ParamRow(self, tab, "kEffect", "kEffectBPM", "BPM")

        ttk.Separator(tab).pack(fill="x", pady=4)
        ttk.Label(
            tab,
            text="Paramètres génériques (signification dépendante du type d'effet, voir manuel) :",
            foreground="#666", wraplength=760,
        ).pack(anchor="w", padx=4)
        canvas_frame = ttk.Frame(tab)
        canvas_frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(canvas_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        for i in range(1, 33):
            ParamRow(self, inner, "kEffect", f"kEffectParam{i}", f"Param {i}")

    # -- log ---------------------------------------------------------------
    def _build_log(self) -> None:
        frame = ttk.LabelFrame(self, text="Journal SysEx")
        frame.pack(fill="x", padx=6, pady=(0, 6))
        self.log_text = tk.Text(frame, height=6, state="disabled", font=("Menlo", 10))
        self.log_text.pack(fill="x", padx=4, pady=4)

    def log(self, message: str) -> None:
        self.log_text.config(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    # -- parameter I/O, routed through self.console -------------------------
    def _channel_for(self, group: str) -> int:
        return self.effect_channel if group == "kEffect" else self.channel

    def set_parameter(self, group: str, name: str, value: int) -> None:
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            return
        channel = self._channel_for(group)
        try:
            self.console.set_parameter(group, name, channel, value)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user directly
            self.log(f"Erreur envoi {group}.{name} : {exc}")
            return
        if not isinstance(self.console.midi, LoggingMidi):
            pd = self.params.get(group, name)
            msg = sysex.build_parameter_change(
                pd.element, pd.param, channel, value, pd.min, pd.max,
                device=self.console.device, model_id=pd.model_id, addr_type=pd.addr_type,
            )
            self.log(f"TX {group}.{name} ch={channel} value={value} : {msg.hex(' ')}")

    def request_parameter(self, group: str, name: str, on_result, on_failure=None) -> None:
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            if on_failure is not None:
                on_failure()
            return
        channel = self._channel_for(group)
        try:
            value = self.console.request_parameter(group, name, channel)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user directly
            self.log(f"Erreur lecture {group}.{name} : {exc}")
            if on_failure is not None:
                on_failure()
            return
        if value is None:
            self.log(f"RX {group}.{name} ch={channel} : pas de réponse (console absente ou simulation).")
            if on_failure is not None:
                on_failure()
            return
        self.log(f"RX {group}.{name} ch={channel} value={value}")
        on_result(value)

    # -- scene store/recall/clear, routed through self.console --------------
    def _scene_action(self, label: str, min_number: int, method_name: str, build_msg) -> bool:
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            return False
        number = self.scene_var.get()
        if not (min_number <= number <= 99):
            messagebox.showerror("Scène", f"Numéro de scène invalide pour {label} (attendu {min_number}-99).")
            return False
        try:
            getattr(self.console, method_name)(number)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user directly
            self.log(f"Erreur {label} scène {number} : {exc}")
            return False
        if not isinstance(self.console.midi, LoggingMidi):
            msg = build_msg(number, self.console.device)
            self.log(f"TX {label} scène {number} : {msg.hex(' ')}")
        return True

    def _recall_scene(self) -> None:
        if self._scene_action(
            "Recall", 0, "recall_scene",
            lambda n, device: sysex.build_function_call(sysex.FUNC_SCENE_RECALL, n, device=device),
        ):
            self.read_all()

    def _read_scene_title(self) -> None:
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            return
        number = self.scene_var.get()
        if not (0 <= number <= 99):
            messagebox.showerror("Scène", "Numéro de scène invalide (attendu 0-99).")
            return
        try:
            title = self.console.get_scene_title(number)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user directly
            self.log(f"Erreur lecture du nom de la scène {number} : {exc}")
            return
        if title is None:
            self.log(f"Nom de la scène {number} : pas de réponse.")
            return
        self.scene_title_var.set(title)
        self.log(f"Nom de la scène {number} : {title!r}")

    def _write_scene_title(self) -> None:
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            return
        number = self.scene_var.get()
        if not (1 <= number <= 99):
            messagebox.showerror("Scène", "Numéro de scène invalide pour l'écriture du nom (attendu 1-99).")
            return
        title = self.scene_title_var.get()
        try:
            self.console.set_scene_title(number, title)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user directly
            self.log(f"Erreur écriture du nom de la scène {number} : {exc}")
            return
        self.log(f"Nom de la scène {number} écrit : {title!r}")

    def read_all(self) -> None:
        """Re-read every parameter control from the console - equivalent to
        clicking "Lire" on every control (e.g. to sync after a scene recall
        or after manual changes made directly on the console)."""
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            return
        self.log(f"Tout lire : {len(self.rows)} contrôle(s)…")
        for row in self.rows:
            row.get_value()
        self.log("Tout lire : terminé.")

    # -- channel strip copy/paste, routed through self.console --------------
    def _channel_param_defs(self, include_name: bool) -> list[tuple[str, str]]:
        """(group, name) pairs for all per-input-channel controls (i.e.
        everything using the shared channel selector - excludes kEffect,
        whose channel dimension is the effect slot, not the input channel)."""
        seen: set[tuple[str, str]] = set()
        pairs: list[tuple[str, str]] = []
        for row in self.rows:
            if row.group == "kEffect":
                continue
            if row.group == "kInputChannelName" and not include_name:
                continue
            names = row.names if isinstance(row, NameRow) else [row.name]
            for name in names:
                key = (row.group, name)
                if key not in seen:
                    seen.add(key)
                    pairs.append(key)
        return pairs

    def _read_channel(self, channel: int, include_name: bool) -> dict[tuple[str, str], int]:
        return self._read_channel_params(channel, self._channel_param_defs(include_name))

    def _read_channel_params(self, channel: int, defs: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
        values: dict[tuple[str, str], int] = {}
        for group, name in defs:
            try:
                value = self.console.request_parameter(group, name, channel)
            except Exception as exc:  # noqa: BLE001 - surfaced to the user directly
                self.log(f"Erreur lecture {group}.{name} (canal {channel + 1}) : {exc}")
                continue
            if value is not None:
                values[(group, name)] = value
        return values

    def _write_channel(self, channel: int, values: dict[tuple[str, str], int]) -> int:
        count = 0
        for (group, name), value in values.items():
            try:
                self.console.set_parameter(group, name, channel, value)
            except Exception as exc:  # noqa: BLE001 - surfaced to the user directly
                self.log(f"Erreur écriture {group}.{name} (canal {channel + 1}) : {exc}")
                continue
            count += 1
        return count

    def _refresh_if_visible(self, channel: int) -> None:
        """If `channel` is the one currently shown, re-read its controls."""
        if self.channel_var.get() - 1 == channel:
            self.read_all()

    # -- Tranche tab copy/paste (COPY ALL / COPY SEL. / PASTE TO) -----------
    def _bind_section_select(self, label: tk.Widget, members: list) -> None:
        """While COPY SEL. is active, clicking a section title (GATE, COMP,
        GRAVE...) selects/deselects every control of that section at once,
        instead of clicking each knob/toggle individually."""
        def on_click(_event: tk.Event) -> None:
            if not self.strip_select_mode:
                return
            want = not all(w.selected for w in members)
            for w in members:
                w.selected = want
                w._redraw()
            label.config(foreground="#c00" if want else "")
        label.bind("<Button-1>", on_click)

    def _strip_param_defs(self) -> list[tuple[str, str]]:
        """(group, name) pairs for every control on the Tranche tab, except
        stereo pairing (kInputPair) which "COPY ALL" must not touch."""
        seen: set[tuple[str, str]] = set()
        pairs: list[tuple[str, str]] = []
        for row in self.rows:
            if not isinstance(row, (Knob, Toggle, EnumSelector, Fader)):
                continue
            if row.group == "kInputPair":
                continue
            key = (row.group, row.name)
            if key not in seen:
                seen.add(key)
                pairs.append(key)
        return pairs

    def _strip_exit_select_mode(self, cancelled: bool = False) -> None:
        if not self.strip_select_mode:
            return
        self.strip_select_mode = False
        self.strip_copy_sel_btn.config(text="COPY SEL.")
        for row in self.rows:
            if isinstance(row, (Knob, Toggle, EnumSelector, Fader)) and row.selected:
                row.selected = False
                row._redraw()
        if cancelled:
            self.log("Tranche : sélection annulée.")

    def _strip_ask_destination_channel(self) -> int | None:
        dst = simpledialog.askinteger(
            "PASTE TO", "Canal destination (1-32) :", parent=self, minvalue=1, maxvalue=32,
        )
        return None if dst is None else dst - 1

    def _strip_copy_all(self) -> None:
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            return
        self._strip_exit_select_mode()
        src = self.channel
        values = self._read_channel_params(src, self._strip_param_defs())
        if not values:
            self.log("COPY ALL : annulé, aucune valeur lue (console absente ou simulation).")
            return
        self.strip_clipboard = values
        self.strip_clipboard_label.config(
            text=f"Presse-papiers tranche : canal {src + 1} ({len(values)} réglage(s), sans appairage)"
        )
        self.log(f"COPY ALL : {len(values)} réglage(s) du canal {src + 1} (hors appairage).")

    def _strip_copy_sel_toggle(self) -> None:
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            return
        if self.strip_select_mode:
            self._strip_exit_select_mode(cancelled=True)
            return
        self.strip_clipboard = None
        self.strip_clipboard_label.config(text="Presse-papiers tranche : vide")
        self.strip_select_mode = True
        self.strip_copy_sel_btn.config(text="Annuler la sélection")
        self.log(
            "COPY SEL. : cliquez sur un contrôle (knob, interrupteur, type, fader) ou sur un titre de "
            "section (GATE, COMP, GRAVE...) pour le/la sélectionner (rouge), re-cliquez pour désélectionner, "
            "puis PASTE TO... pour confirmer."
        )

    def _strip_paste_to(self) -> None:
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            return

        if self.strip_select_mode:
            selected_defs = [
                (row.group, row.name) for row in self.rows
                if isinstance(row, (Knob, Toggle, EnumSelector, Fader)) and row.selected
            ]
            if not selected_defs:
                messagebox.showerror("PASTE TO", "Aucun réglage sélectionné (COPY SEL.).")
                return
            src = self.channel
            dst = self._strip_ask_destination_channel()
            if dst is None:
                return
            if dst == src:
                messagebox.showerror("PASTE TO", "Le canal destination doit être différent du canal affiché.")
                return
            values = self._read_channel_params(src, selected_defs)
            count = self._write_channel(dst, values)
            self.log(f"PASTE TO (sélection) : {count}/{len(selected_defs)} réglage(s) canal {src + 1} -> canal {dst + 1}.")
            self._strip_exit_select_mode()
            self._refresh_if_visible(dst)
            return

        if not self.strip_clipboard:
            messagebox.showerror("PASTE TO", "Rien à coller : utilisez COPY ALL ou COPY SEL. d'abord.")
            return
        dst = self._strip_ask_destination_channel()
        if dst is None:
            return
        count = self._write_channel(dst, self.strip_clipboard)
        self.log(f"PASTE TO : {count} réglage(s) collé(s) sur le canal {dst + 1}.")
        self._refresh_if_visible(dst)

    def _copy_paste_channel(self) -> None:
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            return
        src, dst = self.copy_src_var.get() - 1, self.copy_dst_var.get() - 1
        if src == dst:
            messagebox.showerror("Copier/Coller", "Le canal source et destination doivent être différents.")
            return
        include_name = self.copy_include_name_var.get()
        self.log(f"Copie canal {src + 1} -> canal {dst + 1}…")
        values = self._read_channel(src, include_name)
        if not values:
            self.log("Copie annulée : aucune valeur lue (console absente ou simulation).")
            return
        count = self._write_channel(dst, values)
        self.log(f"Copie terminée : {count}/{len(values)} paramètre(s) écrits sur le canal {dst + 1}.")
        self._refresh_if_visible(dst)

    def _copy_channel_to_clipboard(self) -> None:
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            return
        src = self.copy_src_var.get() - 1
        include_name = self.copy_include_name_var.get()
        self.log(f"Copie du canal {src + 1} dans le presse-papiers…")
        values = self._read_channel(src, include_name)
        if not values:
            self.log("Copie annulée : aucune valeur lue (console absente ou simulation).")
            return
        self.channel_clipboard = values
        self.channel_clipboard_label.config(
            text=f"Presse-papiers : canal {src + 1} ({len(values)} paramètre(s))"
        )
        self.log(f"Presse-papiers : {len(values)} paramètre(s) du canal {src + 1}.")

    def _paste_clipboard_to_channel(self) -> None:
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            return
        if not self.channel_clipboard:
            messagebox.showerror("Coller", "Le presse-papiers est vide : copiez d'abord un canal.")
            return
        dst = self.copy_dst_var.get() - 1
        count = self._write_channel(dst, self.channel_clipboard)
        self.log(f"Collé : {count} paramètre(s) sur le canal {dst + 1}.")
        self._refresh_if_visible(dst)

    def _store_scene(self) -> None:
        self._scene_action(
            "Store", 1, "store_scene",
            lambda n, device: sysex.build_function_call(sysex.FUNC_SCENE_STORE, n, device=device),
        )

    def _clear_scene(self) -> None:
        self._scene_action(
            "Clear", 1, "clear_scene",
            lambda n, device: sysex.build_function_clear(sysex.FUNC_SCENE_CLEAR, n, device=device),
        )

    # -- bulk dump backup/restore, routed through self.console --------------
    def _backup_scene(self) -> None:
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            return
        number = self.backup_scene_var.get()
        data = self.console.request_scene_dump(number)
        if data is None:
            self.log(f"Backup scène {number} : pas de réponse (console absente ou simulation).")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".syx", initialfile=f"scene_{number:03d}.syx",
            filetypes=[("SysEx dump", "*.syx")],
        )
        if not path:
            return
        with open(path, "wb") as f:
            f.write(data)
        self.log(f"Backup scène {number} : {len(data)} octets écrits dans {path}")

    def _restore_scene(self) -> None:
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            return
        number = self.backup_scene_var.get()
        path = filedialog.askopenfilename(filetypes=[("SysEx dump", "*.syx"), ("Tous les fichiers", "*.*")])
        if not path:
            return
        with open(path, "rb") as f:
            data = f.read()
        try:
            self.console.send_scene_dump(number, data)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user directly
            self.log(f"Erreur restauration scène {number} : {exc}")
            return
        self.log(f"Restauration scène {number} depuis {path} ({len(data)} octets) envoyée.")

    def _backup_all_scenes(self) -> None:
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            return
        folder = filedialog.askdirectory(title="Dossier de sauvegarde des scènes")
        if not folder:
            return
        count = 0
        for number in range(100):
            data = self.console.request_scene_dump(number)
            if data is None:
                self.log(f"Backup bibliothèque : scène {number} ignorée (pas de réponse).")
                continue
            with open(os.path.join(folder, f"scene_{number:03d}.syx"), "wb") as f:
                f.write(data)
            count += 1
        self.log(f"Backup bibliothèque de scènes : {count}/100 scènes sauvegardées dans {folder}")

    def _restore_all_scenes(self) -> None:
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            return
        folder = filedialog.askdirectory(title="Dossier contenant les fichiers scene_NNN.syx")
        if not folder:
            return
        count = 0
        for number in range(100):
            path = os.path.join(folder, f"scene_{number:03d}.syx")
            if not os.path.exists(path):
                continue
            with open(path, "rb") as f:
                data = f.read()
            try:
                self.console.send_scene_dump(number, data)
            except Exception as exc:  # noqa: BLE001 - surfaced to the user directly
                self.log(f"Erreur restauration scène {number} : {exc}")
                continue
            count += 1
        self.log(f"Restauration bibliothèque de scènes : {count} fichier(s) envoyé(s) depuis {folder}")

    def _backup_setup(self) -> None:
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            return
        data = self.console.request_setup_dump()
        if data is None:
            self.log("Backup setup : pas de réponse (console absente ou simulation).")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".syx", initialfile="setup_current.syx",
            filetypes=[("SysEx dump", "*.syx")],
        )
        if not path:
            return
        with open(path, "wb") as f:
            f.write(data)
        self.log(f"Backup setup : {len(data)} octets écrits dans {path}")

    def _restore_setup(self) -> None:
        if self.console is None:
            self.log("Non connecté : ouvrez une connexion (réelle ou simulation) d'abord.")
            return
        path = filedialog.askopenfilename(filetypes=[("SysEx dump", "*.syx"), ("Tous les fichiers", "*.*")])
        if not path:
            return
        with open(path, "rb") as f:
            data = f.read()
        try:
            self.console.send_setup_dump(data)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user directly
            self.log(f"Erreur restauration setup : {exc}")
            return
        self.log(f"Restauration setup depuis {path} ({len(data)} octets) envoyée.")


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
