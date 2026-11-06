# Changelog

## v0.3.1

- Console permissions: clearer answers on errors (console cannot show a code, not available, wrong number of digits,
  could not save), an invalid pairing starts re-pairing, "already allowed" after a new code closes the dialog.
- Nothing changed in the permissions dialog: it closes without a call and without reloading.
- Messages for refused rest mode, power off and fan control name the options (Console permissions).
- Manifest category `media_entertainment.gaming`.

## v0.3.0

Needs the ps5-hass payload v0.3.0 for the new options; older payloads keep working.

- Options are a menu: **Sources and favourites** (as before) and **Console permissions**.
- Console permissions: tick rest mode, power off or fan control and enter the code shown on the TV; untick to take a
  permission away at once. No FTP and no `config.json` editing needed any more.
- The entities follow the permissions right after the dialog closes.

## v0.2.0

Needs the ps5-hass payload v0.2.0 for the new entities; with v0.1.0 they stay unknown or are not created.

- `notify.ps5`: messages on the TV (title in front, 200 bytes, 5 per 30 s).
- Media browser: folders Games and Apps with covers; a click starts the title.
- `sensor.ps5_payloads` (attribute `names`) and binary sensors for kstuff, ShadowMount, ftpsrv and websrv.
- `button.ps5_power_off` when the console allows power off (`power_off` in the payload's `config.json`).
- Fan threshold: `switch.ps5_fan_control`, `number.ps5_fan_threshold` (45–85 °C, never above the factory value),
  `sensor.ps5_fan_threshold_current`; only when the console allows `fan_control`.
- Entities appear or disappear by themselves when the console's permissions change.

## v0.1.0

First release: media player with sources and cover, temperatures, free storage, running app, power state, cover
image, app events, close app, rest mode (opt-in), favourite start buttons, pairing with a code on the TV, mDNS
discovery, reauth, diagnostics. Translations English and German.
