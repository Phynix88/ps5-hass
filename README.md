# PS5 (ps5-hass) for Home Assistant

Brings a jailbroken PS5 into Home Assistant: what is running (with cover), all installed games and apps in the media
browser and as sources, start/switch/close/minimise apps, play time per day, week and game (with a ready-made
play time limit), temperatures, chip power and free storage (also external drives), running payloads (start and stop
them), messages on the TV, app started/stopped events, rest mode, keeping the console awake, power off and the fan
threshold. No YAML, no MQTT broker, no PSN account, no cloud.

It talks to the [ps5-hass payload](https://github.com/Phynix88/ps5-hass-payload) running on the console and to
Sony's own status query (to tell "rest mode" from "off").

![PS5 dashboard in Home Assistant](https://raw.githubusercontent.com/Phynix88/ps5-hass/main/docs/images/dashboard.png)

## Requirements

- A PS5 with a jailbreak and the **ps5-hass payload** loaded (see the payload's README; it can be installed through
  the Payload Manager and put into the autoload). Payload **v0.4.1 or later** (v0.4.0 can hang a console when a payload is stopped during a game) for everything described here: play
  time, payload switches, keeping awake, minimising, chip power, external drives, and rest mode, power off and the
  fan without any setting. A **v0.3** payload still works, without these new features (their entities do not appear),
  and rest mode, power off and the fan only if they were allowed on the console before. v0.1.0 and v0.2.0 also lack
  messages, payloads, power off and the fan.
- Home Assistant 2026.10 or later, in the same local network.
- Home Assistant must reach the console on **TCP 8181** (payload) and **UDP 9302** (Sony's status query; without it
  rest mode shows as `off`). Automatic discovery needs mDNS: Home Assistant in Docker needs host networking, and the
  console must be in the same subnet.
- Give the console a fixed IP address (DHCP reservation in your router).

## Installation

[![Open in the Home Assistant Marketplace](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Phynix88&repository=ps5-hass&category=integration)

**Marketplace (Home Assistant 2026.11 and later):** Settings → **Marketplace** → **Add from link** → enter
`Phynix88/ps5-hass` → **Add** → open **PS5 (ps5-hass)** → **Install** → restart Home Assistant. Updates show up under
Settings → **Updates**.

**HACS (Home Assistant 2026.10):** HACS → ⋮ → **Custom repositories** → add `https://github.com/Phynix88/ps5-hass`
with type *Integration* → search **PS5 (ps5-hass)** → **Download** → restart Home Assistant.

**Manually:** copy `custom_components/ps5_hass/` to `<config>/custom_components/ps5_hass/` and restart. To update,
replace the folder and restart again.

## Setup

1. Load the payload on the PS5. Home Assistant shows **"PS5 found"** (mDNS). Otherwise add the integration
   **PS5 (ps5-hass)** and enter the console's IP address (port 8181) or its `.local` name. Other host names must
   first be added to `"hosts"` in the payload's `config.json`, or Home Assistant reports "Cannot reach the payload".
2. The console shows `ps5-hass pairing code: 123456` on the TV. Enter the 6 digits within two minutes.
   **Missed it or too slow?** Tick **"Show a new code"** and press *Submit*; the TV shows a fresh code (at most one
   every 30 s). Closing the dialog and starting again works too.

If the console forgets the pairing (for example after its `config.json` was deleted), Home Assistant asks you to
pair again. Removing the integration gives the pairing slot back to the console (only while the console is on and
the payload runs; otherwise the slot stays used until the pairings are reset on the console).

If the console gets a new IP address, add the integration again with the new address: Home Assistant updates the
existing entry and reports "already set up".

## What you get

| Entity | Notes |
|---|---|
| `media_player.ps5` | `playing` (app running), `idle` (home screen), `on` (console on, payload not loaded), `off` (rest mode or off; `sensor.ps5_power` tells them apart). Source list = installed titles, games first; selecting a source starts it (a running app is closed first). Stop closes the running app. Turn off = rest mode, only when the console can do it. Cover of the running app as picture. Media browser, see below. |
| `notify.ps5` | message on the TV, see below |
| `sensor.ps5_cpu_temperature`, `sensor.ps5_soc_temperature` | °C |
| `sensor.ps5_storage_free` | GiB free on the internal SSD |
| `sensor.ps5_storage_free_m2`, `sensor.ps5_storage_free_extended`, `sensor.ps5_storage_free_usb0` … | GiB free on each external or extended drive, attribute `total_gib`; one sensor per drive, created when the drive first shows up (unavailable while it is missing). See *External drives* |
| `sensor.ps5_soc_power` | Watts drawn by the console's chip, see *Power draw* |
| `sensor.ps5_playtime_today`, `sensor.ps5_playtime_week` | game time today and this week, in minutes. See *Play time* |
| `sensor.ps5_playtime_<title id>` | total play time of one game in minutes (for example `sensor.ps5_playtime_ppsa01234`), created when the game is first played. See *Play time* |
| `sensor.ps5_running_app` | name, attributes `title_id`, `kind` |
| `sensor.ps5_power` | `on`, `standby` (rest mode), `off`. With payload v0.4 rest mode shows at once |
| `sensor.ps5_payloads` | number of running payloads, attribute `names`; unknown while the console is not on |
| `binary_sensor.ps5_jailbreak_kstuff`, `binary_sensor.ps5_shadowmount`, `binary_sensor.ps5_ftp_server`, `binary_sensor.ps5_websrv` | whether that payload runs (`kstuff.elf`, `shadowmountplus.elf`, `ftpsrv.elf`, `websrv.elf`; diagnostic; unknown while the console is not on) |
| `update.ps5_payload` | a newer ps5-hass payload is on GitHub (checked every 6 hours); install it in the Payload Manager on the console, not from Home Assistant. See *Payload updates* |
| `switch.ps5_payload_<name>` | starts and stops a payload through the Payload Manager, one per payload it lists, for example `switch.ps5_payload_ftpsrv`. See *Payload switches* |
| `sensor.ps5_uptime`, `sensor.ps5_fan_raw` | diagnostic, disabled by default (the fan value is the raw console value) |
| `image.ps5_cover` | cover of the running app |
| `event.ps5_app` | `started` / `stopped` with `title_id`, `name`, `kind`. No event when the console goes to rest mode or off; if the app is gone afterwards, `stopped` fires when the payload is back. Use `sensor.ps5_power` for power changes. |
| `button.ps5_close_app` | closes the app that Home Assistant shows as running |
| `button.ps5_minimize` | minimises the running game, see *Minimise* |
| `switch.ps5_keep_awake` | keeps the console from powering down by itself, see *Keep awake* |
| `button.ps5_rest_mode` | only when the console can do it |
| `button.ps5_power_off` | only when the console can do it |
| `switch.ps5_fan_control`, `number.ps5_fan_threshold` | only when the console can do it, see below |
| `sensor.ps5_fan_threshold_current` | threshold the console uses right now (diagnostic) |
| `button.ps5_start_<app>` | one per favourite (options) |

Entity IDs follow your Home Assistant language (for example `sensor.ps5_cpu_temperatur` in German), except for
`sensor.ps5_playtime_today`, `sensor.ps5_playtime_week`, `sensor.ps5_playtime_<title id>`,
`sensor.ps5_storage_free_<drive>` and `switch.ps5_payload_<name>`, which are the same in every language. The IDs of
the other new entities (`button.ps5_minimize`, `switch.ps5_keep_awake`, `sensor.ps5_soc_power`) are the English ones
in this README; in German they are `button.ps5_minimieren`, `switch.ps5_wach_halten` and `sensor.ps5_soc_leistung`.
Automations that should work in any language pick entities from the lists in the editor (the blueprint below does).

### Options

Settings → Devices & services → PS5 → **Configure** opens one form:

- **Sources and favourites:** source list (all apps and games, or games only) and favourite apps (each gets its own
  start button).

## Play time

The payload counts how long each game is **in the foreground** and keeps the numbers on the console, so nothing is
lost while Home Assistant is down.

- `sensor.ps5_playtime_today` and `sensor.ps5_playtime_week`: minutes of game time, by Home Assistant's time zone (the
  day ends at midnight, the week on Monday; the value can lag up to a minute behind a change).
- `sensor.ps5_playtime_<title id>`: total play time of one game, created when the game is first played, named after
  the game. Home Assistant's long-term statistics turn it into play time per day or week for that game (statistics
  graph card).
- **Foreground only:** the home screen and settings do not count. The control centre keeps the game in front, so it
  **does** count. A minimised game does not count.
- **Games only:** apps (streaming, media, the store) do not count towards the day and week sensors and get no
  sensor of their own.
- Once they have a value, the sensors stay available while the console is off; the total is still true. After a
  restart of Home Assistant while the console is off they are unavailable until the payload answers again.
- The values never go down: if the payload loses its last minute of counting (update, power cut), the sensors keep
  the higher value. Today starts again at midnight, the week on Monday.
- Keep the sensors' unit in minutes: the play time limit blueprint compares the state with minutes.
- Attribute `console_total_min` of a game's sensor is the **console total**: the console's own count for that game
  (all users, also from before ps5-hass was installed), when the console knows it. It is Sony's number and can
  differ from the sensor, which counts only from the first start of the payload.

### Play time limit

[![Open your Home Assistant instance and show the blueprint import dialog with this blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FPhynix88%2Fps5-hass%2Fmain%2Fblueprints%2Fautomation%2Fps5_hass%2Fplaytime_limit.yaml)

The blueprint **PS5 play time limit** warns on the TV shortly before the daily limit, then closes the game, minimises
it or puts the console into rest mode. Starting a game again after the limit runs the action again, with the notice
"Play time for today is over". Press the button above, then Settings → Automations → **Create automation** from the
blueprint and fill in:

| Input | Meaning |
|---|---|
| Play time sensor | `sensor.ps5_playtime_today` (its unit must stay minutes) |
| Daily limit, warn this long before | minutes (default 120 and 15; 0 turns the warning off) |
| TV notifications | `notify.ps5` |
| At the limit | close the game, minimise it or rest mode |
| Close app / Minimise / Rest mode button | the button for the action you chose |
| Warning text, text when play time is over | optional; English by default |

The warning comes once per day and the action at the limit once; no helpers are needed (the comments in the
blueprint explain how). The action follows within about a minute of the sensor reaching the limit, and within about a
minute of a new game starting. Minimising keeps the game open and paused, so the player can come back to it
tomorrow; closing it loses unsaved progress.

## Payload switches

`switch.ps5_payload_<name>` starts and stops the payloads that the console's **Payload Manager** lists, for example
`switch.ps5_payload_ftpsrv`. The state is on while the payload runs; the attribute `version` is the Payload
Manager's version. The switches are unavailable while the Payload Manager does not answer (it must be running on the
console, and the console on).

- **Only while no game or app runs.** Stopping or starting a payload during a game can hang the console, so the
  payload (v0.4.1 or later; update from v0.4.0) refuses with "Close the running game or app on the PS5 first."
- **Protected payloads never get a switch:** kstuff, etaHEN, the ELF loader, the Payload Manager, ps5-hass itself,
  CheatRunner (any cheat payload) and ps5debug can be neither started nor stopped from Home Assistant.
- Stopping a payload ends its process. A payload that was updated in the Payload Manager keeps its switch.
- The binary sensors above and `sensor.ps5_payloads` show what runs; the switches change it.

## Keep awake

`switch.ps5_keep_awake`: while on, the payload keeps the console from powering down by itself (the "turn off after"
timer of the power saving settings). It does **not** stop rest mode that you start with a button or the controller.
The switch is off again after a payload restart or a console restart, and Home Assistant cannot switch it on while
the console is in rest mode.

## Minimise

`button.ps5_minimize` minimises the running game: it stays open and paused, and the console shows its home screen.
Open it again from the home screen. While a game is minimised the console reports no running app, so
`media_player.ps5` is `idle` and the play time stops. Only for games; nothing running gives an error.

## Power draw

`sensor.ps5_soc_power` shows the Watts drawn by the console's chip, read from the console's own sensor. **Chip only,
not the wall:** the drives, fan, power supply and the rest of the console are not included, so the socket draws
more. The attribute `note` says so.

## External drives

`sensor.ps5_storage_free_<drive>` (`m2`, `extended`, `usb0` … `usb7`) shows the free space of each drive the console
reports, in GiB; the attribute `total_gib` is its size. A sensor is created when its drive first shows up and stays
(unavailable) while the drive is missing.

## Payload updates

`update.ps5_payload` compares the payload on the console (from the payload itself) with the latest release on
GitHub, checked every 6 hours; it shows "unknown" when GitHub cannot be reached. Home Assistant cannot install it:
replace `ps5-hass.elf` in the Payload Manager on the console. The installed version is also shown while the
console is off.

## Media browser

**Media → PS5** shows the folders *Games* and *Apps* with covers. Clicking a title starts it on the console (a
running app is closed first). The same works from a media card on a dashboard. While the console is off the browser
is empty.

Starting a title from an automation: `media_player.select_source` with the title's name, or `media_player.play_media`
with its title id.

```yaml
action: media_player.play_media
target:
  entity_id: media_player.ps5
data:
  media_content_type: app
  media_content_id: PPSA01234
```

## Messages on the TV

`notify.ps5` shows a message as a notification on the TV. A title is put in front
of the message (`title: message`). At most 200 bytes (longer texts are cut), 5 messages per 30 seconds.

```yaml
action: notify.send_message
target:
  entity_id: notify.ps5
data:
  title: Door
  message: Someone is at the front door
```

## Rest mode, power off and the fan

Pairing is the only step: after it, rest mode, power off and the fan threshold work (payload v0.4). Power off needs
the power button or the controller's PS button to switch on again.

| Entity | Does |
|---|---|
| `button.ps5_rest_mode` | rest mode (also the media player's turn off) |
| `button.ps5_power_off` | power off |
| fan threshold | switch, slider and current value |

**Fan threshold:** the temperature at which the console speeds up its fan. Lower = cooler and louder.

- `switch.ps5_fan_control` on: the console uses `number.ps5_fan_threshold` (45–85 °C, never above the console's
  factory value, 91 °C on FW 13.60). Switching on again uses the last threshold (80 °C the first time). Setting
  `number.ps5_fan_threshold` also switches fan control on.
- Off: the console's factory value again.
- `sensor.ps5_fan_threshold_current` is what the console uses right now.
- The payload sets the value again if the console resets it. If another tool changes the fan, the payload leaves
  it alone and reports it (`/api/v1/info`, `last_error`); setting a threshold in Home Assistant again takes over
  again.

Example: cooler while a game runs, factory value again on the home screen.

```yaml
triggers:
  - trigger: state
    entity_id: media_player.ps5
    to: playing
    id: game
  - trigger: state
    entity_id: media_player.ps5
    to: idle
    id: home
actions:
  - choose:
      - conditions: "{{ trigger.id == 'game' }}"
        sequence:
          - action: number.set_value
            target:
              entity_id: number.ps5_fan_threshold
            data:
              value: 75
      - conditions: "{{ trigger.id == 'home' }}"
        sequence:
          - action: switch.turn_off
            target:
              entity_id: switch.ps5_fan_control
```

## Dashboard example

```yaml
type: vertical-stack
cards:
  - type: media-control
    entity: media_player.ps5
  - type: tile
    entity: sensor.ps5_cpu_temperature
  - type: tile
    entity: switch.ps5_fan_control
  - type: tile
    entity: number.ps5_fan_threshold
    features:
      - type: numeric-input
        style: slider
  - type: tile
    entity: button.ps5_rest_mode
    hide_state: true
    tap_action:
      action: perform-action
      perform_action: button.press
      target:
        entity_id: button.ps5_rest_mode
      confirmation:
        text: Put the PS5 into rest mode?
```

To send your own text from a dashboard, create a text helper (`input_text`) and a script that calls
`notify.send_message` with its value.

## How it works

- Every 10 s Home Assistant asks the payload for the state; with payload v0.4 the payload reports rest mode itself,
  so `sensor.ps5_power` shows `standby` at once. When it does not answer, the last state is kept for 30 s, then
  Sony's status query decides between `on`, `standby` and `off`.
- Play time is asked for once a minute, and at once when the running app changes. Payload switches ask the Payload
  Manager every 30 s.
- The app list is fetched every 10 minutes, and at once when the console reports a change.
- Covers go through Home Assistant's image proxy; only PNG/JPEG up to 1 MB are passed on.

## Security

Plain HTTP in the local network; the token is stored in Home Assistant's config entry (removed from diagnostics).
Pairing needs a code from the TV. See the payload README, *Security and limits*.

## Limits

- ps5-hass cannot wake the console. The [PlayStation 5 integration](https://github.com/SH1FT-W/playstation-5) wakes
  it from rest mode through Remote Play pairing and can run alongside ps5-hass (separate integration, separate
  entities). From power off, only the power button or the controller's PS button switches it on.
- No controller input, no screenshots, no controller battery (the console refuses pad access from a payload).
- PS4 titles are listed with their title id as name.
- The fan threshold is tested on FW 13.60 (CFI-2116) only.

## Troubleshooting

- **Not found automatically:** add it by IP address; check that the payload runs (`http://<ps5>:8181/api/v1/info`).
  mDNS does not cross subnets.
- **"Cannot reach the payload" with a host name:** use the IP address or the `.local` name, or add the name to
  `"hosts"` in the payload's `config.json`.
- **Media player says `on`, everything else is unavailable:** the payload is not running. After every console restart
  run the jailbreak again; the Payload Manager's autoload then starts ps5-hass.
- **Rest mode shows as `off`:** UDP 9302 to the console is blocked, or "Stay connected to the internet" is off in the
  console's power saving settings (then the console leaves the network in rest mode).
- **Pairing locked:** after 10 wrong codes in a row the console locks pairing for 15 minutes (growing); wait, or
  restart the payload.
- **Power off / fan entities missing:** the console cannot do it, or the payload is older than v0.4 (update it).
- **Play time, keep awake, minimise, chip power, payload switches missing:** they need payload v0.4 (see
  `update.ps5_payload`); the Payload Manager must be running for the switches.
- **Play time stays at 0:** only a game in the foreground counts, not apps and not the home screen.
- **Report a problem:** download the diagnostics (Settings → Devices → PS5 → ⋮ → Download diagnostics; the token is
  removed) and add the payload's `/api/v1/info`.

## Removal

1. Turn `switch.ps5_fan_control` and `switch.ps5_keep_awake` off if you used them (the console gets its factory
   threshold and its own power-down timer back). Delete the play time limit automation, if you made one.
2. While the console is on and the payload runs: Settings → Devices & services → PS5 → ⋮ → **Delete**. This frees
   the pairing slot on the console.
3. Marketplace → PS5 (ps5-hass) → ⋮ → **Uninstall** (HACS: ⋮ → **Remove**; manual install: delete
   `custom_components/ps5_hass/`), then restart Home Assistant.

## Development

```sh
python3.14 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest -q
```

Tests run against a stateful fake of the payload whose answers are checked against the payload's JSON schema
(`tests/fixtures/schema.json`).

## License

GPL-3.0-or-later.
