"""ito / leva! Status Monitor protocol. No Home Assistant imports, so it runs standalone.

Framing (ito Hardware Reference Manual, "Creating compatible firmware"):
- `MC?` -> `XML=<name>`: name of the Status Monitor XML that describes the frame.
- `MCr` -> one `{...}` line per cycle (250 ms for leva!). Char 2 is `*` while the pump /
  brew program runs, `-` otherwise. Optional `~XX` hex checksum before `}`.
- Values are fixed-width at positions given by the XML `<value at= len=>`; `???` = no reading.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    key: str
    name: str
    at: int
    len: int
    type: str  # float | int | string
    unit: str | None = None
    factor: float | None = None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _fields(rows) -> list[Field]:
    """Build fields; a name that occurs twice ("Flow rate" in ml/s and g/s) gets its unit appended."""
    names = [r[0] for r in rows]
    out = []
    for name, at, ln, typ, unit, factor in rows:
        key = name if names.count(name) == 1 else f"{name} {unit}"
        out.append(Field(_slug(key), name, at, ln, typ, unit, factor))
    return out


def fields_from_xml(text: str) -> list[Field]:
    """Fields from a Status Monitor XML. Only <value> elements with an export name are used."""
    root = ET.fromstring(re.sub(r"^<\?xml[^>]*\?>", "", text.lstrip()))
    rows = []
    for v in root.iter("value"):
        name = v.get("export")
        if not name:
            continue
        f = v.get("factor")
        rows.append((name, int(v.get("at")), int(v.get("len")), v.get("type", "float"),
                     v.get("unit") or None, float(f) if f else None))
    return _fields(rows)


def checksum_ok(line: str) -> bool:
    """Two's complement of the 8-bit sum of all chars before `~`. The manual says "binary
    complement"; two's complement is what real frames match. Frames without `~` pass."""
    i = line.rfind("~")
    if i < 0:
        return True
    try:
        want = int(line[i + 1:].strip(" }"), 16)
    except ValueError:
        return False
    return -sum(line[:i].encode("latin-1", "replace")) & 0xFF == want


def _value(raw: str, f: Field):
    if "?" in raw:
        return None
    if f.type == "string":
        return raw.strip() or None
    try:
        v = float(raw)
    except ValueError:
        return None
    if f.factor:
        return round(v * f.factor, 3)
    return int(v) if f.type == "int" else v


@dataclass
class Frame:
    brewing: bool
    mode: str | None  # PID, ECO, OFF, CLD, ATN; None in standby
    values: dict


def parse(line: str, fields: list[Field]) -> Frame | None:
    """Decode one `{...}` line, None if it is not a valid frame."""
    if not (line.startswith("{") and line.endswith("}")) or len(line) < 8 or not checksum_ok(line):
        return None
    # ponytail: mode at 4-6 is leva!-specific and has no export name in the XML.
    mode = line[4:7] if line[4:7].isalpha() else None
    end = line.rfind("~") if "~" in line else len(line) - 1
    # A field past the end of the data (shorter frame than the layout expects) is None,
    # never a number cut in half.
    return Frame(line[2] == "*", mode, {
        f.key: _value(line[f.at:f.at + f.len], f) if f.at + f.len <= end else None for f in fields})


# Display frames (`MC@`, ito manual "Creating compatible firmware"): `<BKL` + four 16-char
# lines + `>`. B backlight, K button, L layout (2 = bold title + 3 lines, the main screen).
# OLED glyphs: 0xDF degree sign, 0x04 arrow before a program-controlled setpoint, low codes
# draw the heat bargraph.
_GLYPHS = {"\xdf": "°", "\x04": "►"}
# Runtime alerts from the leva! firmware manual; shown in place of the setpoint, blinking.
ALERTS = ("SETUP", "PROTECT", "FLWMTR", "FILTER", "DSCALE", "BACKFL", "REFILL", "NO SNS", "GAUGE",
          "SCALE", "BLE OFF", "VALVE")
_ALERT_RE = re.compile(r"(?<![A-Z])(" + "|".join(sorted(map(re.escape, ALERTS), key=len, reverse=True)) + r")(?![A-Z])")
# Countdown of a running program at the start of line 2: "04m:41s" (or "1h:05m").
_COUNTDOWN_RE = re.compile(r"^\s*(\d{1,2})([hm]):(\d\d)([ms])")
# Programs of the run menu. The title shows the name for the whole program; line 2 shows a
# countdown only in timed phases (warm-up first heats to its boost temperature, untimed).
# Any other title shown with a countdown counts as a program too.
PROGRAMS = ("WARM-UP", "FLUSH", "PURGE", "DESCALE", "BACKFLUSH", "AUTOTUNE")


@dataclass(frozen=True)
class Display:
    backlight: bool
    layout: str
    lines: tuple[str, ...]  # four lines, glyphs mapped to text
    program: str | None  # title while a program counts down
    time_left: int | None  # seconds
    alerts: tuple[str, ...]


def _text(raw: str) -> str:
    return "".join(_GLYPHS.get(c, "▮" if c < " " else c) for c in raw)


def parse_display(line: str) -> Display | None:
    """Decode one `<...>` display line, None if it is not a valid one."""
    if len(line) != 69 or line[0] != "<" or line[-1] != ">":
        return None
    raw = [line[4 + 16 * i:20 + 16 * i] for i in range(4)]
    lines = tuple(_text(r).rstrip() for r in raw)
    time_left = program = None
    title = lines[0].strip()
    if line[3] == "2" and (m := _COUNTDOWN_RE.match(raw[1])) and m[2] + m[4] in ("ms", "hm"):
        scale = 60 if m[2] == "m" else 3600
        time_left = int(m[1]) * scale + int(m[3]) * scale // 60
        program = title or None
    elif line[3] == "2" and title in PROGRAMS:
        program = title
    alerts = tuple(dict.fromkeys(_ALERT_RE.findall(raw[1]))) if line[3] == "2" else ()
    return Display(line[1] == "1", line[3], lines, program, time_left, alerts)


if __name__ == "__main__":
    # Hand-made test layout (not a vendor file): fixed-width fields in a 289-char frame.
    xml = """<?xml version="1.0"?><cfg><values>
        <value at="4" len="3" type="string">1:%</value>
        <value at="7" len="6" export="Temperature PID1" type="float" unit="&#xb0;C"/>
        <value at="18" len="3" export="Heat PID" type="int" unit="%"/>
        <value at="160" len="3" export="Flow rate" factor="0.0166" type="int" unit="ml/s"/>
        <value at="168" len="5" export="PCB sensor" type="float" unit="&#xb0;C"/>
        <value at="197" len="5" export="Pressure" type="float" unit="bar"/>
        <value at="226" len="5" export="Weight" type="float" unit="g"/>
        <value at="231" len="5" export="Flow rate" type="float" unit="g/s"/>
        <value at="236" len="9" export="Profile 1" type="string"/>
        <value at="254" len="9" export="Profile 3" type="string"/>
        </values></cfg>"""
    fields = fields_from_xml(xml)
    assert [f.key for f in fields] == ["temperature_pid1", "heat_pid", "flow_rate_ml_s", "pcb_sensor", "pressure",
                                       "weight", "flow_rate_g_s", "profile_1", "profile_3"]
    assert fields[0].unit == "°C" and fields[2].factor == 0.0166

    def build(vals: dict[int, str], flag="-", mode="PID", length=289) -> str:
        body = list("{ " + flag + "1" + mode + "?" * (length - 12))
        for at, s in vals.items():
            body[at:at + len(s)] = s
        s = "".join(body[:length - 5]) + "~ "
        return s + f"{-sum(s[:-2].encode('latin-1')) & 0xFF:02X}}}"

    vals = {7: "090.15", 18: "017", 160: "500", 197: "08.22", 226: "042.1", 231: "03.12",
            236: "     9BAR", 254: " " * 9}
    line = build(vals, flag="*")
    fr = parse(line, fields)
    assert fr and fr.brewing and fr.mode == "PID"
    v = fr.values
    assert v["temperature_pid1"] == 90.15 and v["heat_pid"] == 17 and v["pressure"] == 8.22
    assert v["flow_rate_ml_s"] == 8.3 and v["flow_rate_g_s"] == 3.12 and v["weight"] == 42.1
    assert v["profile_1"] == "9BAR" and v["profile_3"] is None and v["pcb_sensor"] is None
    assert parse(line[:100] + "X" + line[101:], fields) is None  # bad checksum
    sv = parse(build(vals, length=200), fields).values  # shorter frame than the layout
    assert sv["temperature_pid1"] == 90.15 and sv["pressure"] is None and sv["weight"] is None
    standby = parse(build({}, mode="???"), fields)
    assert standby and standby.mode is None and not standby.brewing

    def screen(*lines: str, flags="002") -> str:
        return "<" + flags + "".join(f"{s:<16}"[:16] for s in (*lines, "", "", "")[:4]) + ">"

    idle = parse_display(screen("PROFILE:-", "1: 93.1\xdf \x04 93.0\xdf", "\x01            5%", " 0.0b   1s 178ml"))
    assert idle and idle.lines[1] == "1: 93.1° ► 93.0°" and idle.lines[2].startswith("▮")
    assert idle.program is None and idle.time_left is None and idle.alerts == () and not idle.backlight
    warm = parse_display(screen("WARM-UP", "04m:41s   BACKFL", "\x03\x03       28%", "0.0b  3s  41ml"))
    assert warm and warm.program == "WARM-UP" and warm.time_left == 281 and warm.alerts == ("BACKFL",)
    assert parse_display(screen("DESCALE", "1h:05m   REFILL")).time_left == 3900
    heating = parse_display(screen("WARM-UP", "1:109.4\xdf \x04110.0\xdf", "\x03" * 12 + "100%"))
    assert heating.program == "WARM-UP" and heating.time_left is None
    assert parse_display(screen("WARM-UP", "04m:41s", flags="001")).program is None  # not the main screen
    assert parse_display(screen("x")[:-2] + ">") is None
    print("ok")
