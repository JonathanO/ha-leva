# leva! (ito) for Home Assistant

Native Home Assistant integration for espresso machines with the Software & Circuits **ito**
module running **leva!**. Holds one TCP connection to the module (port 23), streams the
Status Monitor report (`MCr`, 4 frames/s) and turns it into entities. No MQTT, no add-on.

## Requirements

- ito module with leva! firmware, connected to your network (station mode)
- The Status Monitor XML that comes with your leva! download
- Home Assistant with HACS (tested with Home Assistant 2026.2)

## Install

1. In HACS: *⋮ → Custom repositories*, add `https://github.com/JonathanO/ha-leva` with type
   *Integration*, then install *leva! (ito)* and restart Home Assistant.
2. Then *Settings → Devices & services → Add integration → leva! (ito)*. Enter
   the module's IP (give it a fixed DHCP lease) and upload the Status Monitor XML from your
   leva! download (`xml file for status monitor/leva!-XXXXXXXX.xml`).
3. After a firmware update, upload the new XML: *Settings → Repairs* shows an entry for the
   missing file, or use *Reconfigure* on the integration (also works before flashing). Uploads
   are stored in `config/leva/` (copying files there works too).

The integration asks the firmware which XML it uses (`MC?`) and reads field positions,
names and units from that file. Without the matching file setup stops and asks for it.

## Entities

- Sensors: boiler temperature, setpoint, heater output (+ 1 min average), pressure, target
  pressure, weight, flow rate (scale / flow meter), volume, mode (`on`, `eco`, `standby`, …).
  PID internals and other fields from the XML exist but start disabled.
- Binary sensor **Brewing**: pump / brew program running (also flushes, water through the wand).
- Buttons **Wake**, **Eco**, **Standby**; select **Profile**. These need
  *Setup → Wifi → Permissions → Serial menu* enabled in leva!, which lets every device on your
  LAN control pump and heating. Leave it off if you only want readings.
- Event `leva_brew_finished` when the pump stops: `duration`, `weight`, `volume`,
  `peak_pressure`, `profile`. Filter shots from flushes by weight or duration.

## Notes

- The report mode is global for the module. If the Virtual Display / Companion app switches
  it to display mode, entities go unavailable and the integration asks for `MCr` again
  after 2 minutes.
- Unverified on hardware: whether `MCr` works with the Serial menu permission off (the leva!
  manual says report commands are always allowed), and that profile slot *n* matches
  `MCcPROFILE n`.
- Connection quirks (lost `MCr` after boot, silent sockets after power cuts, 4-connection
  limit) were learned from [leva2mqtt](https://github.com/stangster-360/leva2mqtt)'s captures.

Not affiliated with Software & Circuits. Sending commands to your machine is at your own risk.
