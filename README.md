# leva! (ito) for Home Assistant

Native Home Assistant integration for espresso machines with the Software & Circuits **ito**
module running **leva!**. Holds one TCP connection to the module (port 23), streams the
Status Monitor report (`MCr`, 4 frames/s) and turns it into entities. No MQTT, no add-on.

## Install

1. Copy `custom_components/leva` into your HA `config/custom_components/`.
2. Restart HA, then *Settings → Devices & services → Add integration → leva! (ito)*, enter
   the module's IP. Give the module a fixed DHCP lease.
3. Copy the Status Monitor XML that came with your firmware (e.g. `leva!-65FCB201.xml`) to
   `config/leva/`. The integration asks the firmware which XML it uses (`MC?`) and reads
   field positions, names and units from it. For `leva!-64EA1111` and `leva!-65FCB201` a
   built-in table is used if the file is missing; any other firmware without its XML is
   refused (setup error) rather than shown with a guessed layout.

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
