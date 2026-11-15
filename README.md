# leva! (ito) for Home Assistant

Native Home Assistant integration for espresso machines with an
[ito](https://www.softwareandcircuits.com/division/circuits/products/ito/index.html) controller
module running the [leva!](http://projectcaffe.bplaced.net/) firmware. Holds one TCP connection
to the module (port 23), streams the Status Monitor report (`MCr`, 4 frames/s) and turns it
into entities.

## Requirements

- ito module with leva! firmware, connected to your network (station mode)
- The Status Monitor XML that comes with your leva! download
- Home Assistant 2026.11 or newer (installs through the built-in Marketplace)

## Install

1. *Settings → Marketplace → Add from link*, enter `JonathanO/ha-leva` and select *Add*.
   Install *leva! (ito)* and restart Home Assistant.
2. Then *Settings → Devices & services → Add integration → leva! (ito)*. Enter
   the module's IP (give it a fixed DHCP lease) and upload the Status Monitor XML from your
   leva! download (`xml file for status monitor/leva!-XXXXXXXX.xml`).
3. Before a firmware update, upload the new firmware's XML with *Reconfigure* on the
   integration. When the machine comes back with the new firmware, the integration switches to
   it automatically. Forgot? *Settings → Repairs* shows an entry for the missing file and takes
   the upload there. Uploads are kept in `config/leva/` (copying files there works too).

The integration asks the firmware which XML it uses (`MC?`) and reads field positions,
names and units from that file. Without the matching file setup stops and asks for it.

## Entities

- Sensors: boiler temperature, setpoint, heater output (+ 1 min average), pressure, target
  pressure, weight, flow rate (scale / flow meter), volume, selected pressure profile, mode
  (`on`, `eco`, `standby`, …). PID internals and other fields from the XML exist but start
  disabled.
- Binary sensor **Brewing**: pump / brew program running (also flushes, water through the wand).
- From the machine's display (see Notes):
  - **Program**: the program counting down on the display (`warm-up`, `flush`, `backflush`,
    …), `none` otherwise. **Program end**: when it will finish (timestamp).
  - **Alert**: runtime alert on the display (`refill`, `backfl`, `filter`, `dscale`,
    `protect`, …), `none` otherwise; attribute `active` lists all current ones.
  - Binary sensor **Water tank empty**: the `REFILL` alert. Needs a tank sensor
    (*Setup → Contacts*, `TANK` function and *Tank logic*).
  - **Display** (disabled by default): the display's title, with its four lines as attributes.
- Buttons **Wake**, **Eco**, **Standby**. These need
  *Setup → Wifi → Permissions → Serial menu* enabled in leva!, which lets every device on your
  LAN control pump and heating. Leave it off if you only want readings.
- **Last shot** sensors: when it finished (timestamp), duration, volume, average flow rate
  (volume / duration), average boiler temperature, weight and peak pressure when the pump
  stopped, profile. A pump run counts as a shot when it lasts at least 12 s and no run-menu
  program (warm-up pulses, flush, descale, …) is on the display. They keep their values while
  the machine is off and across restarts. Weight, pressure and volume ones exist only when the
  XML has the field, and stay `unknown` without a scale or pressure sensor.
- Event `leva_brew_finished` when the pump stops, for every pump run: `duration`, `weight`,
  `volume`, `peak_pressure`, `profile`, `shot` (counted as a shot, as above) and `program`
  (the program on the display while it ran, or none).
- *Download diagnostics* on the device page includes the machine's full setup menu (PID
  settings, pressure profiles, doses) next to the current readings. The module's Wi-Fi
  bridge drops parts of the dump when the link stalls, so it is read up to three times
  until two copies agree (`setup_complete`); readings pause for a few seconds per read.

## Notes

- The report mode is global for the module. The report has no program, countdown or alerts,
  so every 2 s the integration switches to display frames (`MC@`) for one line and back
  (`MCr`), about 0.2 s, without losing readings. Other clients get an occasional display line
  and a repeated report frame in between.
- If the Virtual Display / Companion app (or the Status Monitor's Virtual LCD) switches the
  module to display mode, the integration leaves it there: the report entities go
  unavailable, the display entities keep updating from that app's display frames, and
  `MCr` is requested again after 2 minutes.
- Sending commands to your machine is at your own risk.

## Development

```sh
pip install -r requirements_test.txt   # Python 3.14
pytest
```
