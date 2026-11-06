# PS5 (ps5-hass) for Home Assistant

Brings a jailbroken PS5 into Home Assistant: what is running (with cover), all installed games and apps as
sources, start/switch/close apps, temperatures and free storage, app started/stopped events and, if you allow it,
rest mode. No YAML, no MQTT broker, no PSN account, no cloud.

It talks to the [ps5-hass payload](https://github.com/Phynix88/ps5-hass-payload) running on the console and to
Sony's own status query (to tell "rest mode" from "off").

## Requirements

- A PS5 with a jailbreak and the **ps5-hass payload v0.1.0 or later** loaded (see the payload's README; it can be
  installed through the Payload Manager and put into the autoload).
- Home Assistant 2026.10 or later, in the same local network.

## Installation

**HACS (once the repository is public):** HACS → ⋮ → Custom repositories → add
`https://github.com/Phynix88/ps5-hass` as *Integration* → install **PS5 (ps5-hass)** → restart Home Assistant.

**Manually:** copy `custom_components/ps5_hass/` to `<config>/custom_components/ps5_hass/` and restart.

## Setup

1. Load the payload on the PS5. Home Assistant shows **"PS5 found"** (mDNS). Otherwise add the integration
   **PS5 (ps5-hass)** and enter the console's IP address (port 8181).
2. The console shows `ps5-hass pairing code: 123456` on the TV. Enter the 6 digits within two minutes.
   "Show a new code" asks for another one (at most every 30 s).

If the console forgets the pairing (for example after its `config.json` was deleted), Home Assistant asks you to
pair again. Removing the integration gives the pairing slot back to the console.

## What you get

| Entity | Notes |
|---|---|
| `media_player.ps5` | `playing` (app running), `idle` (home screen), `on` (console on, payload not loaded), `standby` (rest mode), `off`. Source list = installed titles, games first; selecting a source starts it (a running app is closed first). Stop closes the running app. Turn off = rest mode, only when allowed. Cover of the running app as picture. |
| `sensor.ps5_cpu_temperature`, `sensor.ps5_soc_temperature` | °C |
| `sensor.ps5_storage_free` | GiB free on the internal SSD |
| `sensor.ps5_running_app` | name, attributes `title_id`, `kind` |
| `sensor.ps5_uptime`, `sensor.ps5_fan_raw` | diagnostic, disabled by default (the fan value is the raw console value) |
| `image.ps5_cover` | cover of the running app |
| `event.ps5_app` | `started` / `stopped` with `title_id`, `name`, `kind` |
| `button.ps5_close_app` | closes the app that Home Assistant shows as running |
| `button.ps5_rest_mode` | only when `power_actions` is enabled on the console |
| `button.ps5_start_<app>` | one per favourite (options) |

Entity IDs follow your Home Assistant language (for example `sensor.ps5_cpu_temperatur` in German).

### Options

- **Source list:** all apps and games, or games only.
- **Favourite apps:** each gets its own start button.

### Rest mode from Home Assistant

The payload refuses rest mode unless you allow it on the console: set `"power_actions": true` in
`/data/ps5-hass/config.json` and restart the payload. Home Assistant picks the change up the next time the
payload answers.

## How it works

- Every 10 s Home Assistant asks the payload for the state. When it does not answer, the last state is kept for
  30 s (going to rest mode), then Sony's status query decides between `on`, `standby` and `off`.
- The app list is fetched every 10 minutes, and at once when the console reports a change.
- Covers go through Home Assistant's image proxy; only PNG/JPEG up to 1 MB are passed on.

## Limits

- Waking the console from rest mode is not possible (it needs Remote Play pairing with a PSN login).
- No controller input, no screenshots, no power off (rest mode only).
- PS4 titles are listed with their title id as name.

## Troubleshooting

- **Not found automatically:** add it by IP address; check that the payload runs (`http://<ps5>:8181/api/v1/info`).
- **Pairing locked:** after 10 wrong codes in a row the console locks pairing for 15 minutes (growing).
- **Report a problem:** download the diagnostics (Settings → Devices → PS5 → ⋮ → Download diagnostics; the token is
  removed) and add the payload's `/api/v1/info`.

## Development

```sh
python3.14 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest -q
```

Tests run against a stateful fake of the payload whose answers are checked against the payload's JSON schema
(`tests/fixtures/schema.json`).

## License

GPL-3.0-or-later.
