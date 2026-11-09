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


# Layout reported as XML=leva!-64EA1111 / leva!-65FCB201 (leva! 3.x, 289-char frame).
# Used when no XML file is installed. Names match the XML's export names so entity ids
# stay the same whether the table or the XML is used.
BUILTIN_LEN = 289
BUILTIN = _fields([
    ("Temperature PID1", 7, 6, "float", "°C", None),
    ("Setpoint PID", 13, 5, "float", "°C", None),
    ("Heat PID", 18, 3, "int", "%", None),
    ("Heat PID (P)", 21, 6, "float", "%", None),
    ("Heat PID (I)", 27, 6, "float", "%", None),
    ("Heat PID (D)", 33, 6, "float", "%", None),
    ("Heat PID (feed forward)", 39, 6, "float", "%", None),
    ("dT/dt PID1", 45, 8, "float", None, None),
    ("Kc PID", 53, 5, "float", "%/°", None),
    ("Ti PID", 58, 5, "float", "s", None),
    ("Td PID", 63, 5, "float", "s", None),
    ("Windup guard PID1", 68, 5, "float", "%", None),
    ("Heat PID (average)", 73, 5, "float", "%", None),
    ("Temperature PID2", 81, 5, "float", "°C", None),
    ("Setpoint PID2", 87, 5, "float", "°C", None),
    ("Heat PID2", 92, 3, "int", "%", None),
    ("Heat PID2 (P term)", 95, 6, "float", "%", None),
    ("Heat PID2 (I term)", 101, 6, "float", "%", None),
    ("Heat PID2 (D term)", 107, 6, "float", "%", None),
    ("Heat PID2 (feed forward)", 113, 6, "float", "%", None),
    ("dT/dt PID2", 119, 7, "float", None, None),
    ("Kc PID2", 127, 5, "float", "%/°", None),
    ("Ti PID2", 132, 5, "float", "s", None),
    ("Td PID2", 137, 5, "float", "s", None),
    ("Windup guard PID2", 142, 5, "float", "%", None),
    ("Heat PID2 (average)", 147, 5, "float", "%", None),
    ("Volume", 152, 3, "int", "ml", None),
    ("Flow rate", 160, 3, "int", "ml/s", 0.0166),
    ("PCB sensor", 168, 5, "float", "°C", None),
    ("Temperature Aux1", 174, 5, "float", "°C", None),
    ("Temperature Aux2", 180, 5, "float", "°C", None),
    ("Temperature Aux3", 186, 5, "float", "°C", None),
    ("Pressure", 197, 5, "float", "bar", None),
    ("Pressure SP", 192, 5, "float", "bar", None),
    ("Pressure band (min)", 202, 5, "float", "bar", None),
    ("Pressure band (max)", 207, 5, "float", "bar", None),
    ("Phase", 212, 3, "int", "°", None),
    ("Phase (Bias)", 215, 3, "int", "°", None),
    ("Phase (P)", 218, 4, "int", "°", None),
    ("Phase (I)", 222, 4, "int", "°", None),
    ("Weight", 226, 5, "float", "g", None),
    ("Flow rate", 231, 5, "float", "g/s", None),
    ("Profile 1", 236, 9, "string", None, None),
    ("Profile 2", 245, 9, "string", None, None),
    ("Profile 3", 254, 9, "string", None, None),
    ("Profile 4", 263, 9, "string", None, None),
    ("Selected profile", 272, 9, "string", None, None),
    ("Flow tracking", 281, 3, "int", "ml/s", 0.01666),
])


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
    return Frame(line[2] == "*", mode, {f.key: _value(line[f.at:f.at + f.len], f) for f in fields})


if __name__ == "__main__":
    import sys

    def build(vals: dict[int, str], flag="-", mode="PID") -> str:
        body = list("{ " + flag + "1" + mode + "?" * (BUILTIN_LEN - 12))
        for at, s in vals.items():
            body[at:at + len(s)] = s
        s = "".join(body) + "~ "
        return s + f"{-sum(s[:-2].encode('latin-1')) & 0xFF:02X}}}"

    line = build({7: "090.15", 13: "090.0", 18: "017", 160: "500", 197: "08.22",
                  226: "042.1", 231: "03.12", 236: "     9BAR", 245: "GEN.LEVER",
                  254: " " * 9, 272: "GEN.LEVER"}, flag="*")
    assert len(line) == BUILTIN_LEN, len(line)
    fr = parse(line, BUILTIN)
    assert fr and fr.brewing and fr.mode == "PID"
    v = fr.values
    assert v["temperature_pid1"] == 90.15 and v["heat_pid"] == 17 and v["pressure"] == 8.22
    assert v["flow_rate_ml_s"] == 8.3 and v["flow_rate_g_s"] == 3.12 and v["weight"] == 42.1
    assert v["profile_1"] == "9BAR" and v["profile_3"] is None and v["selected_profile"] == "GEN.LEVER"
    assert v["pcb_sensor"] is None
    assert parse(line[:100] + "X" + line[101:], BUILTIN) is None  # bad checksum
    standby = parse(build({}, mode="???"), BUILTIN)
    assert standby and standby.mode is None and not standby.brewing
    xml = ('<?xml version="1.0"?><cfg><values><value at="4" len="3" type="string">1:%</value>'
           '<value at="7" len="6" export="Temperature PID1" type="float" unit="&#xb0;C"/>'
           '<value at="160" len="3" export="Flow rate" factor="0.0166" type="int" unit="ml/s"/>'
           '<value at="231" len="5" export="Flow rate" type="float" unit="g/s"/></values></cfg>')
    fx = fields_from_xml(xml)
    assert [f.key for f in fx] == ["temperature_pid1", "flow_rate_ml_s", "flow_rate_g_s"]
    assert fx[0].unit == "°C" and fx[1].factor == 0.0166
    # Optional: compare the builtin table against a real XML file passed as argument.
    if len(sys.argv) > 1:
        real = fields_from_xml(open(sys.argv[1], encoding="latin-1").read())
        assert real == BUILTIN, set(real) ^ set(BUILTIN)
        print("builtin table matches", sys.argv[1])
    print("ok")
