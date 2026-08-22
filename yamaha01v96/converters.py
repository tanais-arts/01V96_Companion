"""Raw SysEx value -> real-world unit (dB, ms, Hz, %, ratio...) conversions.

These formulas/tables were reverse-engineered empirically against a real
01V96 V2 console (see /memories/repo/01v96-project-notes.md for the full
calibration log). Some tables (Gate Hold/Decay, Comp Release, Fader/Aux
Level below their linear segment) are only known via a handful of measured
points plus a curve fit - conversions for those are interpolated/extrapolated
and shown as approximate (the raw value is always kept alongside in the GUI
so nothing is hidden).

Entry point: `raw_to_display(pd, raw) -> str | None`. Returns None when no
conversion is known for that parameter (caller should fall back to showing
the raw ParamDef comment, e.g. "PRM TABLE #08").
"""
from __future__ import annotations

import math

from .params import ParamDef

# ---------------------------------------------------------------------
# PRM TABLE #06 (Gate Hold) and #07 (Gate Decay / Comp Release, shared) -
# only a handful of measured (raw, ms) points exist; interpolate in
# log-space between them and extrapolate geometrically past the last one.
_TABLE_06_HOLD = [
    (0, 0.02), (8, 0.13), (20, 0.44), (60, 2.35), (67, 3.19),
    (150, 117.0), (215, 1960.0),
]
_TABLE_07_DECAY = [
    (0, 5.0), (5, 32.0), (15, 85.0), (20, 112.0), (37, 229.0), (43, 293.0),
    (44, 304.0), (80, 1370.0), (100, 3410.0), (159, 42300.0),
]
# PRM TABLE #11 (EQ Q factor), numeric range only (shelf/HPF/LPF handled separately)
_TABLE_11_Q = [
    (0, 10.0), (14, 2.0), (15, 1.8), (23, 0.70), (37, 0.14), (39, 0.11), (40, 0.10),
]
_COMP_RATIO_TABLE = [
    "1.0:1", "1.1:1", "1.3:1", "1.5:1", "1.7:1", "2.0:1", "2.5:1", "3.0:1",
    "3.5:1", "4.0:1", "5.0:1", "6.0:1", "8.0:1", "10:1", "20:1", "\u221e:1",
]


def _interp_log(points: list[tuple[int, float]], raw: int) -> float:
    if raw <= points[0][0]:
        return points[0][1]
    if raw >= points[-1][0]:
        (r0, v0), (r1, v1) = points[-2], points[-1]
        b = (math.log(v1) - math.log(v0)) / (r1 - r0)
        return v1 * math.exp(b * (raw - r1))
    for (r0, v0), (r1, v1) in zip(points, points[1:]):
        if r0 <= raw <= r1:
            t = (raw - r0) / (r1 - r0)
            return math.exp(math.log(v0) + t * (math.log(v1) - math.log(v0)))
    return points[-1][1]


def _interp_linear(points: list[tuple[int, float]], raw: int) -> float:
    if raw <= points[0][0]:
        return points[0][1]
    if raw >= points[-1][0]:
        return points[-1][1]
    for (r0, v0), (r1, v1) in zip(points, points[1:]):
        if r0 <= raw <= r1:
            t = (raw - r0) / (r1 - r0)
            return v0 + t * (v1 - v0)
    return points[-1][1]


def _format_ms(ms: float) -> str:
    return f"{ms/1000:.2f} s" if ms >= 1000 else f"{ms:.2f} ms"


def _format_hz(hz: float) -> str:
    return f"{hz/1000:.2f} kHz" if hz >= 1000 else f"{hz:.1f} Hz"


def _db_div10(raw: int) -> str:
    return f"{raw/10:+.1f} dB"


def _eq_freq(raw: int) -> str:
    # PRM TABLE #12, 12 raw steps per octave, anchored at raw=36 -> 125 Hz.
    return _format_hz(125.0 * 2 ** ((raw - 36) / 12))


def _eq_q_numeric(raw: int) -> str:
    return f"Q {_interp_linear(_TABLE_11_Q, raw):.2f}"


def _eq_low_q(raw: int) -> str:
    if raw >= 44:
        return "HPF"
    if raw >= 41:
        return "Shelf"
    return _eq_q_numeric(raw)


def _eq_hi_q(raw: int) -> str:
    if raw >= 43:
        return "LPF"
    if raw >= 41:
        return "Shelf"
    return _eq_q_numeric(raw)


def _fader_aux_db(raw: int) -> str:
    # PRM TABLE #05-2 - linear near unity (raw>=543), log10-ish below that,
    # unmodeled hard floor below ~raw 100 (falls back to the log formula anyway).
    if raw <= 0:
        return "-\u221e dB"
    db = (raw - 823) * 0.05 if raw >= 543 else 68.59 * math.log10(raw) - 201.78
    return f"{db:+.2f} dB"


def _pan(raw: int) -> str:
    if raw == 0:
        return "C"
    return f"L{-raw}" if raw < 0 else f"R{raw}"


# name -> converter function(raw) -> str. Matched against ParamDef.name,
# shared across Input/Bus/AUX/Matrix/Stereo groups that reuse the same table.
_BY_NAME = {
    "kGateThreshold": _db_div10,
    "kCompThreshold": _db_div10,
    "kCompGain": _db_div10,
    "kEQLowG": _db_div10,
    "kEQLowMidG": _db_div10,
    "kEQHiMidG": _db_div10,
    "kEQHiG": _db_div10,
    "kGateRange": lambda raw: f"{raw} dB",
    "kGateAttack": lambda raw: f"{raw} ms",
    "kCompAttack": lambda raw: f"{raw} ms",
    "kInDelayTime": lambda raw: _format_ms(raw / 48),
    "kInDelayMix": lambda raw: f"{raw}%",
    "kInDelayFBGain": lambda raw: f"{raw}%",
    "kEQLowF": _eq_freq,
    "kEQLowMidF": _eq_freq,
    "kEQHiMidF": _eq_freq,
    "kEQHiF": _eq_freq,
    "kEQLowQ": _eq_low_q,
    "kEQHiQ": _eq_hi_q,
    "kEQLowMidQ": _eq_q_numeric,
    "kEQHiMidQ": _eq_q_numeric,
    "kCompRatio": lambda raw: _COMP_RATIO_TABLE[raw] if 0 <= raw < len(_COMP_RATIO_TABLE) else str(raw),
    "kCompKnee": lambda raw: "Hard" if raw == 0 else str(raw),
    "kGateHold": lambda raw: _format_ms(_interp_log(_TABLE_06_HOLD, raw)),
    "kGateDecay": lambda raw: _format_ms(_interp_log(_TABLE_07_DECAY, raw)),
    "kCompRelease": lambda raw: _format_ms(_interp_log(_TABLE_07_DECAY, raw)),
    "kFader": _fader_aux_db,
    "kChannelPan": _pan,
    "kBalance": _pan,
    "kGateKeyCh": lambda raw: f"Canal {raw + 1}",
    "kGateKeyAUX": lambda raw: f"Aux {raw + 1}",
}


def raw_to_display(pd: ParamDef, raw: int) -> str | None:
    """Best-effort raw -> human string for `pd`, or None if unknown."""
    fn = _BY_NAME.get(pd.name)
    if fn is not None:
        return fn(raw)
    if pd.name.startswith("kAUX") and pd.name.endswith("Level"):
        return _fader_aux_db(raw)
    if pd.name.startswith("kAUX") and pd.name.endswith("Pan"):
        return _pan(raw)
    return None
