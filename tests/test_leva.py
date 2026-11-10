"""End-to-end tests against a fake ito module on 127.0.0.1."""
import asyncio
import contextlib
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry

import custom_components.leva.client as client

pytestmark = pytest.mark.enable_socket  # the fake module listens on 127.0.0.1

FIRMWARE = "leva!-65FCB201"
# Hand-made test layout (not a vendor file).
TEST_XML = b"""<?xml version="1.0"?><cfg><values>
<value at="7" len="6" export="Temperature PID1" type="float" unit="&#xb0;C"/>
<value at="13" len="5" export="Setpoint PID" type="float" unit="&#xb0;C"/>
<value at="18" len="3" export="Heat PID" type="int" unit="%"/>
<value at="53" len="5" export="Kc PID" type="float" unit="%/&#xb0;"/>
<value at="197" len="5" export="Pressure" type="float" unit="bar"/>
<value at="226" len="5" export="Weight" type="float" unit="g"/>
<value at="231" len="5" export="Flow rate" type="float" unit="g/s"/>
<value at="236" len="9" export="Profile 1" type="string"/>
<value at="245" len="9" export="Profile 2" type="string"/>
<value at="254" len="9" export="Profile 3" type="string"/>
<value at="263" len="9" export="Profile 4" type="string"/>
<value at="272" len="9" export="Selected profile" type="string"/>
</values></cfg>"""
NEW_XML = (b'<?xml version="1.0"?><cfg><values>'
           b'<value at="7" len="6" export="Temperature PID1" type="float" unit="&#xb0;C"/></values></cfg>')
VALUES = {7: "090.15", 13: "090.0", 18: "017", 197: "08.22", 226: "042.1", 231: "03.12",
          236: "     9BAR", 245: "GEN.LEVER", 254: " " * 9, 263: " " * 9, 272: "GEN.LEVER"}
TEMP = "sensor.espresso_machine_boiler_temperature"
SETUP_DUMP = ["PID 1...", "| Control     PID", "| Setpoint  90.0°", "$id: leva! (3.3)",
              "<0012Auto save" + " " * 52 + ">"]


def frame(flag: str = "-") -> bytes:
    body = list("{ " + flag + "1PID" + "?" * 277)
    for at, s in VALUES.items():
        body[at:at + len(s)] = s
    s = "".join(body) + "~ "
    return f"{s}{-sum(s[:-2].encode('latin-1')) & 0xFF:02X}}}\r\n".encode("latin-1")


class FakeIto:
    """Answers MC? with self.xml, streams frames after MCr, records commands."""

    def __init__(self) -> None:
        self.xml, self.flag, self.streaming = FIRMWARE, "-", False
        self.received: list[str] = []
        self.writers: list[asyncio.StreamWriter] = []
        self.tasks: list[asyncio.Task] = []

    async def handle(self, reader, writer) -> None:
        async def stream():
            while True:
                if self.streaming:
                    writer.write(frame(self.flag))
                await asyncio.sleep(0.05)

        self.writers.append(writer)
        self.tasks.append(asyncio.create_task(stream()))
        with contextlib.suppress(ConnectionError):
            while line := await reader.readline():
                cmd = line.decode().strip()
                self.received.append(cmd)
                if cmd == "MC?":
                    writer.write(f"XML={self.xml}\r\n".encode())
                elif cmd == "MCr":
                    self.streaming = True
                elif cmd == "MCu":  # setup dump interrupts the report
                    self.streaming = False
                    for dump_line in SETUP_DUMP:
                        writer.write(dump_line.encode("latin-1") + b"\r\n")
                        await asyncio.sleep(0.01)
        writer.close()

    def reboot(self) -> None:
        for w in self.writers:
            w.close()
        self.writers.clear()


@pytest.fixture
async def ito():
    fake = FakeIto()
    server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    fake.port = server.sockets[0].getsockname()[1]
    yield fake
    server.close()
    for t in fake.tasks:
        t.cancel()


@pytest.fixture(autouse=True)
def xml_dir(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "leva").mkdir()
    (tmp_path / "leva" / f"{FIRMWARE}.xml").write_bytes(TEST_XML)
    return tmp_path / "leva"


def fake_upload(tmp_path, filename: str, content: bytes):
    f = tmp_path / "upload" / filename
    f.parent.mkdir(exist_ok=True)
    f.write_bytes(content)
    return patch("custom_components.leva.config_flow.process_uploaded_file",
                 lambda hass, file_id: contextlib.nullcontext(f))


def upload_id(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012d}"


async def wait_for(cond, timeout: float = 3) -> None:
    for _ in range(int(timeout / 0.05)):
        if cond():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timeout")


async def setup(hass, port: int) -> MockConfigEntry:
    entry = MockConfigEntry(domain="leva", data={CONF_HOST: "127.0.0.1", CONF_PORT: port})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_end_to_end(hass, ito, tmp_path) -> None:
    r = await hass.config_entries.flow.async_init("leva", context={"source": config_entries.SOURCE_USER})
    with fake_upload(tmp_path, f"{FIRMWARE}.xml", TEST_XML):
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: ito.port, "xml": upload_id(1)})
    assert r["type"] is FlowResultType.CREATE_ENTRY and "xml" not in r["data"], r
    assert r["data"]["firmware"] == FIRMWARE
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries("leva")[0]
    assert entry.state is config_entries.ConfigEntryState.LOADED

    await wait_for(lambda: (s := hass.states.get(TEMP)) and s.state == "90.15")
    assert hass.states.get(TEMP).attributes["unit_of_measurement"] == "°C"
    assert hass.states.get("sensor.espresso_machine_pressure").state == "8.22"
    assert hass.states.get("sensor.espresso_machine_mode").state == "on"
    assert hass.states.get("binary_sensor.espresso_machine_brewing").state == "off"
    assert hass.states.get("sensor.espresso_machine_kc_pid") is None  # disabled by default
    assert hass.states.get("sensor.espresso_machine_profile").state == "GEN.LEVER"

    events = []
    hass.bus.async_listen("leva_brew_finished", lambda e: events.append(e.data))
    ito.flag = "*"
    await wait_for(lambda: hass.states.get("binary_sensor.espresso_machine_brewing").state == "on")
    ito.flag = "-"
    await wait_for(lambda: events)
    assert events[0]["weight"] == 42.1 and events[0]["peak_pressure"] == 8.22, events

    await hass.services.async_call("button", "press", {"entity_id": "button.espresso_machine_wake"}, blocking=True)
    await wait_for(lambda: "MCa" in ito.received)

    # Power cut: the module goes silent without closing the socket.
    ito.streaming = False
    await wait_for(lambda: hass.states.get(TEMP).state == "unavailable", timeout=8)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_machine_off_at_setup(hass, monkeypatch) -> None:
    """With the firmware name remembered, setup succeeds while the machine is off and data
    appears as soon as it is switched on, without waiting for Home Assistant's retries."""
    monkeypatch.setattr(client, "RETRY_S", 0.1)
    fake = FakeIto()
    server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()  # machine off
    entry = MockConfigEntry(domain="leva", data={CONF_HOST: "127.0.0.1", CONF_PORT: port, "firmware": FIRMWARE})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert hass.states.get("sensor.mock_title_boiler_temperature").state == "unavailable"

    server = await asyncio.start_server(fake.handle, "127.0.0.1", port)  # switched on
    try:
        await wait_for(lambda: hass.states.get("sensor.mock_title_boiler_temperature").state == "90.15")
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)
        server.close()
        for t in fake.tasks:
            t.cancel()


async def test_unreachable_module_retries(hass) -> None:
    """A new entry whose firmware was never seen still needs the machine once."""
    entry = await setup(hass, 1)
    assert entry.state is config_entries.ConfigEntryState.SETUP_RETRY


async def test_unknown_firmware_without_xml_is_refused(hass, ito) -> None:
    ito.xml = "leva!-NEW"
    entry = await setup(hass, ito.port)
    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    assert "leva!-NEW.xml" in entry.reason


async def test_xml_defines_entities(hass, ito, xml_dir) -> None:
    ito.xml = "leva!-NEW"
    (xml_dir / "leva!-NEW.xml").write_bytes(NEW_XML)
    entry = await setup(hass, ito.port)
    assert entry.state is config_entries.ConfigEntryState.LOADED
    await wait_for(lambda: (s := hass.states.get("sensor.mock_title_boiler_temperature")) and s.state == "90.15")
    assert hass.states.get("sensor.mock_title_pressure") is None  # not in this XML
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_reconfigure_upload(hass, ito, tmp_path, xml_dir) -> None:
    ito.xml = "leva!-NEW"
    entry = await setup(hass, ito.port)
    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    r = await entry.start_reconfigure_flow(hass)
    with fake_upload(tmp_path, "junk.txt", b"nope"):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"xml": upload_id(2)})
    assert r["errors"] == {"xml": "invalid_xml"}
    with fake_upload(tmp_path, "leva!-NEW.xml", NEW_XML):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"xml": upload_id(3)})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "reconfigure_successful", r
    await hass.async_block_till_done()
    assert (xml_dir / "leva!-NEW.xml").exists()
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_repair_issue_upload(hass, ito, tmp_path) -> None:
    assert await async_setup_component(hass, "repairs", {})
    ito.xml = "leva!-NEW"
    entry = await setup(hass, ito.port)
    issue = ir.async_get(hass).async_get_issue("leva", entry.entry_id)
    assert issue and issue.is_fixable and issue.translation_placeholders == {"name": "leva!-NEW"}
    flows = repairs_flow_manager(hass)
    r = await flows.async_init("leva", data={"issue_id": entry.entry_id})
    assert r["type"] is FlowResultType.FORM and r["description_placeholders"] == {"name": "leva!-NEW"}
    with fake_upload(tmp_path, "leva!-NEW.xml", NEW_XML):
        r = await flows.async_configure(r["flow_id"], {"xml": upload_id(4)})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert ir.async_get(hass).async_get_issue("leva", entry.entry_id) is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_reflash_switches_to_new_xml(hass, ito, xml_dir, monkeypatch) -> None:
    monkeypatch.setattr(client, "RETRY_S", 0.1)
    (xml_dir / "leva!-NEW.xml").write_bytes(NEW_XML)  # uploaded before flashing
    entry = await setup(hass, ito.port)
    assert entry.runtime_data.xml == FIRMWARE
    ito.xml = "leva!-NEW"
    ito.reboot()
    await wait_for(lambda: entry.state is config_entries.ConfigEntryState.LOADED
                   and entry.runtime_data.xml == "leva!-NEW", timeout=5)
    assert [f.key for f in entry.runtime_data.fields] == ["temperature_pid1"]
    assert entry.data["firmware"] == "leva!-NEW"  # remembered for setups while the machine is off
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_diagnostics_include_setup_dump(hass, ito) -> None:
    from custom_components.leva.diagnostics import async_get_config_entry_diagnostics

    entry = await setup(hass, ito.port)
    await wait_for(lambda: entry.runtime_data.frame is not None)
    ito.received.clear()
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["setup"] == ["PID 1...", "| Control     PID", "| Setpoint  90.0°"]  # no heartbeat/display
    assert diag["entry"][CONF_HOST] == "**REDACTED**" and diag["firmware"] == FIRMWARE
    assert diag["frame"]["values"]["temperature_pid1"] == 90.15
    await wait_for(lambda: ito.received == ["MCu", "MCr"])  # report restarted after the dump
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_overlapping_diagnostics_downloads(hass, ito) -> None:
    from custom_components.leva.diagnostics import async_get_config_entry_diagnostics

    entry = await setup(hass, ito.port)
    await wait_for(lambda: entry.runtime_data.frame is not None)
    a, b = await asyncio.gather(async_get_config_entry_diagnostics(hass, entry),
                                async_get_config_entry_diagnostics(hass, entry))
    assert a["setup"] == b["setup"] == ["PID 1...", "| Control     PID", "| Setpoint  90.0°"]
    assert await hass.config_entries.async_unload(entry.entry_id)
