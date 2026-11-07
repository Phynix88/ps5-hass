# Changelog

## v0.4.1

**Update the payload to v0.4.1 as well.** v0.4.0 allowed stopping payloads while a game ran; stopping CheatRunner
during a game hung a console. Update to v0.4.1.

- Payload switches only work while no game or app runs; otherwise "Close the running game or app on the PS5 first."
- CheatRunner (any cheat payload) and ps5debug never get a switch (the payload protects them).

## v0.4.0

Needs the ps5-hass payload v0.4.0 for the new features; a v0.3 payload keeps working without them (the new entities
do not appear).

- Play time: `sensor.ps5_playtime_today` and `sensor.ps5_playtime_week` (minutes, games in the foreground only) and
  one sensor per game with its total (attribute `console_total_min`: the console's own count).
- Blueprint **PS5 play time limit**: warning on the TV before the daily limit, then close, minimise or rest mode;
  again when a game is started after the limit.
- Payload switches: `switch.ps5_payload_<name>` starts and stops payloads through the Payload Manager (protected
  ones, such as kstuff, never get one).
- `update.ps5_payload`: a newer ps5-hass payload on GitHub (checked every 6 hours; installed in the Payload Manager).
- `switch.ps5_keep_awake`, `button.ps5_minimize`, `sensor.ps5_soc_power` (chip only, not the wall) and
  `sensor.ps5_storage_free_<drive>` for external and extended drives.
- `sensor.ps5_power` shows rest mode (`standby`) at once.
- Removed: the **Console permissions** dialog. Rest mode, power off and the fan need no permission with payload
  v0.4; with an older payload, entities that were allowed before keep working. The options are the one form for
  sources and favourites again.
- Messages for refused rest mode, power off and fan control now say to update the payload.

## v0.3.2

- README: installation, updates and removal through the Marketplace of Home Assistant 2026.11; screenshots; network
  requirements, troubleshooting and removal in more detail. No functional changes.

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
