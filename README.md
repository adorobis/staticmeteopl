# Meteogram Screenshot for Home Assistant (AppDaemon)

An [AppDaemon](https://appdaemon.readthedocs.io/) app that takes a screenshot of the dynamic meteogram on [app.meteo.pl](https://app.meteo.pl), crops it and saves it as a PNG for a Home Assistant dashboard.

The site is a Flutter web app that can't be embedded or scraped directly, so the app drives headless Chrome with Selenium. It closes the overlays, takes a screenshot and keeps only the chart.

## Features

- **Headless Chrome screenshot**, cropped to a configurable box.
- **Overlay handling**: closes the cookie consent popup and the "What's new" popup.
- **Image validation**: a new image only replaces the previous one if it passes checks for size, blankness and number of colours. A failed run never leaves an empty image on the dashboard.
- **24-hour image history**: every capture is kept, and rejected captures also keep a full-page screenshot, so you can find when and why rendering broke.
- **Atomic writes**: the dashboard never loads a half-written file.
- **Status entity in Home Assistant**: `binary_sensor.meteogram_problem`, with a `unique_id`, for automations and alerts.
- **Manual refresh** from Home Assistant through an event, script or dashboard button.
- **Safe operation**: overlapping runs are prevented, there is a page load timeout and the browser is always closed.
- **Step-by-step logging** in the AppDaemon log, or on the console when run by hand.
- **Plain text config file**, re-read on every run.

## Requirements

| Component | Type | Purpose |
|---|---|---|
| AppDaemon 4.x | | Runs the app and connects it to Home Assistant. |
| `chromium` | System package | Headless browser that renders the page. |
| `chromium-chromedriver` | System package | Lets Selenium control Chromium. Installs `/usr/bin/chromedriver`. |
| `selenium==4.18.1` | Python package | Browser automation. |
| `Pillow==10.2.0` | Python package | Cropping and validating the image. |

You also need a folder the dashboard can read, `/media` by default.

Install `chromium` and `chromium-chromedriver` from the same package repository so their versions match. A mismatch causes a `session not created` error.

### Where to configure the packages

How you add the packages depends on how you run AppDaemon. Restart AppDaemon after changing them, because packages are installed at startup.

#### AppDaemon Docker container (`acrelle/appdaemon`)

The official image is based on Alpine. At startup it searches the config folder (mounted as `/conf`) and all its subfolders for these two files and installs what they list. No environment variable is needed. Because the search includes subfolders, cloning this repository into `/conf/apps/` is enough: its `system_packages.txt` and `requirements.txt` are picked up automatically.

If you use a different image, check its documentation. Some community images only install extra packages when an environment variable is set.

The two files look like this:

`/conf/system_packages.txt`, installed with `apk`:

```text
chromium
chromium-chromedriver
```

`/conf/requirements.txt`, installed with `pip`:

```text
selenium==4.18.1
Pillow==10.2.0
```

#### Home Assistant AppDaemon add-on

Set the packages in the add-on's **Configuration** tab (Settings → Add-ons → AppDaemon → Configuration):

```yaml
system_packages:
  - chromium
  - chromium-chromedriver
python_packages:
  - selenium==4.18.1
  - Pillow==10.2.0
```

#### Check the installation

```bash
docker exec appdaemon sh -c "chromium --version; chromedriver --version; python3 -c 'import selenium, PIL; print(selenium.__version__, PIL.__version__)'"
```

The Chromium and ChromeDriver major versions must be the same. On some installations the browser command is `chromium-browser`.

## Installation

1. Install the required packages (see [Where to configure the packages](#where-to-configure-the-packages)).
1. Copy `pobierz_meteo.py` to your AppDaemon apps folder, for example `/conf/apps/`.
2. Register the app in `apps.yaml`:

   ```yaml
   pobierz_meteo:
     module: pobierz_meteo
     class: MeteoDownloader
   ```

3. Copy `meteo_config.txt` to `/conf/meteo_config.txt` and adjust it (see [Configuration](#configuration)).
4. Add the status binary sensor to Home Assistant (see [Home Assistant integration](#home-assistant-integration)).
5. Restart AppDaemon. The first download starts about 5 seconds after the app starts.

## Configuration

Settings are read from `/conf/meteo_config.txt` as `KEY=VALUE` lines. Blank lines and lines starting with `#` are ignored. Every key is optional: missing keys use their defaults, and invalid values are logged and replaced by the default.

| Key | Default | Description |
|---|---|---|
| `URL` | `https://app.meteo.pl/meteogram/50.12,19.96` | Page to capture. |
| `FILENAME` | `meteogram_bibice.png` | Output file name. |
| `OUTPUT_DIR` | `/media` | Output folder. |
| `CROP_LEFT` | `310` | Left edge of the crop box, in pixels. |
| `CROP_TOP` | `140` | Top edge of the crop box. |
| `CROP_WIDTH` | `403` | Crop box width. |
| `CROP_HEIGHT` | `208` | Crop box height. |
| `REFRESH_INTERVAL` | `900` | Seconds between runs. Read only when the app starts. |
| `PAGE_LOAD_TIMEOUT` | `60` | Seconds to wait for the page to load. |
| `HISTORY_DIR` | `meteo_history` | History folder, relative to `OUTPUT_DIR` unless absolute. |
| `HISTORY_HOURS` | `24` | How long history files are kept. |
| `MIN_FILE_SIZE` | `3000` | Minimum size of the cropped PNG, in bytes. |
| `MIN_STDDEV` | `8` | Minimum grayscale standard deviation. A blank image is close to 0. |
| `MIN_COLORS` | `16` | Minimum number of distinct colours. |
| `STATUS_EVENT` | `meteogram_status` | Event fired after every run. |

Example:

```ini
# --- Source and output ---
URL=https://app.meteo.pl/meteogram/50.12,19.96
FILENAME=meteogram_bibice.png
OUTPUT_DIR=/media

# --- Crop box (pixels in the 1400x1200 browser window) ---
CROP_LEFT=500
CROP_TOP=117
CROP_WIDTH=615
CROP_HEIGHT=260

# --- Timing (seconds) ---
REFRESH_INTERVAL=3600
PAGE_LOAD_TIMEOUT=60

# --- History ---
HISTORY_DIR=meteo_history
HISTORY_HOURS=24

# --- Validation ---
MIN_FILE_SIZE=3000
MIN_STDDEV=8
MIN_COLORS=16

# --- Home Assistant status event ---
STATUS_EVENT=meteogram_status
```

### Finding the crop box

The browser window is 1400×1200, but the visible page area is a bit smaller (about 1400×1061) because the browser's own bars take up some height. To find the coordinates, look at a `*_REJECTED_full.png` from the history folder, or capture one full screenshot. The app rejects a crop box that doesn't fit inside the screenshot.

## How it works

Each run:

1. Loads the config and logs the target, crop box and history folder.
2. Starts headless Chrome (1400×1200, device scale factor 1) and opens the page.
3. Clicks the cookie consent button, if there is one.
4. Closes the "What's new" popup with a series of clicks on its background.
5. Takes a screenshot, crops it and validates the crop.
6. On success, saves the image to the history folder and replaces the dashboard image.
   On failure, saves the rejected crop and the full screenshot to the history folder and keeps the previous image.
7. Deletes history files older than `HISTORY_HOURS`.
8. Fires the `meteogram_status` event with the result.

### Image validation

A capture is rejected if any of these is true:

- Its dimensions don't match `CROP_WIDTH` × `CROP_HEIGHT`, or the crop box goes outside the screenshot.
- The PNG is smaller than `MIN_FILE_SIZE`.
- Its grayscale standard deviation is below `MIN_STDDEV`, meaning it is blank or flat.
- It has fewer than `MIN_COLORS` distinct colours.

These checks catch blank and broken renders. They can't detect a valid-looking image of the wrong thing, such as a new popup covering the chart. That's what the history folder is for.

### History

```
/media/meteo_history/
├── meteogram_bibice_20261030_174246.png               # accepted capture
├── meteogram_bibice_20261030_184250_REJECTED.png      # rejected crop
└── meteogram_bibice_20261030_184250_REJECTED_full.png # full page of the rejected run
```

If the dashboard image starts looking wrong, find the first bad file in the history folder to see when the problem began. The `_REJECTED_full.png` files show what the browser actually rendered.

## Home Assistant integration

### Status binary sensor

Entities created by AppDaemon can't have a `unique_id`. Instead, the app fires an event after every run, and Home Assistant builds the entity from it with a trigger-based template binary sensor. Home Assistant restores its state after a restart, and you can customise it in the UI.

Add this to `configuration.yaml`, merging it with an existing `template:` section if you have one, or use it as a package:

```yaml
template:
  - triggers:
      - trigger: event
        event_type: meteogram_status
    binary_sensor:
      - name: "Meteogram problem"
        unique_id: meteogram_problem
        device_class: problem
        icon: mdi:weather-partly-cloudy
        state: "{{ not trigger.event.data.success }}"
        attributes:
          message: "{{ trigger.event.data.message }}"
          last_attempt: "{{ trigger.event.data.last_attempt }}"
          last_success: >-
            {{ trigger.event.data.last_attempt if trigger.event.data.success
               else this.attributes.get('last_success') }}
          consecutive_failures: >-
            {{ 0 if trigger.event.data.success
               else (this.attributes.get('consecutive_failures', 0) | int(0)) + 1 }}
```

> On Home Assistant versions older than 2024.10, write `trigger:` and `- platform: event` instead.

| State / attribute | Meaning |
|---|---|
| `on` | The last run failed. |
| `off` | The last run succeeded. |
| `message` | Validation result or error message. |
| `last_attempt` | Time of the last run. |
| `last_success` | Time of the last successful run. |
| `consecutive_failures` | Failed runs in a row. Counted in Home Assistant, so restarting AppDaemon doesn't reset it. |

The sensor shows `unknown` until the first event arrives.

Example automation:

```yaml
automation:
  - alias: "Meteogram keeps failing"
    triggers:
      - trigger: numeric_state
        entity_id: binary_sensor.meteogram_problem
        attribute: consecutive_failures
        above: 2
    actions:
      - action: notify.notify
        data:
          message: >-
            Meteogram failed {{ state_attr('binary_sensor.meteogram_problem', 'consecutive_failures') }}
            times: {{ state_attr('binary_sensor.meteogram_problem', 'message') }}
```

### Manual refresh

The app listens for the `METEOGRAM_REFRESH` event.

Script in `scripts.yaml`:

```yaml
meteogram_refresh:
  alias: "Refresh meteogram"
  icon: mdi:refresh
  sequence:
    - event: METEOGRAM_REFRESH
```

If you create the script in the UI (Settings → Automations & Scenes → Scripts → Add → Edit in YAML), leave out the `meteogram_refresh:` line, otherwise you get `Message malformed: not a valid option`. Set its entity ID to `script.meteogram_refresh` when saving, or change the button below to match.

```yaml
alias: Refresh meteogram
icon: mdi:refresh
sequence:
  - event: METEOGRAM_REFRESH
```

Dashboard button:

```yaml
type: button
name: Refresh meteogram
icon: mdi:refresh
tap_action:
  action: perform-action
  perform_action: script.meteogram_refresh
```

A run takes about 30 seconds. A refresh requested while a run is in progress is skipped, and the log shows a warning.

You can also fire the event from **Developer Tools → Events**, or through the REST API:

```bash
curl -X POST -H "Authorization: Bearer YOUR_TOKEN" http://HA_HOST:8123/api/events/METEOGRAM_REFRESH
```

## Logging

Every step is logged to the AppDaemon log:

```
INFO pobierz_meteo: === Meteogram run 20261030_174246 started ===
INFO pobierz_meteo: [STEP 1] Config: /conf/meteo_config.txt (found)
INFO pobierz_meteo: [STEP 2] Webpage loaded successfully. Title: 'Meteo ICM'
INFO pobierz_meteo: [STEP 4] Offset (100,20): clicked.
INFO pobierz_meteo: [STEP 5] Screenshot captured: 1400x1061, 422687 B
INFO pobierz_meteo: [STEP 5] Validation passed: 82259 B, stddev 61.5
INFO pobierz_meteo: [STEP 6] History file saved: /media/meteo_history/meteogram_bibice_20261030_174246.png
INFO pobierz_meteo: [DASHBOARD SUCCESS] Tailored 615x260 image created at: /media/meteogram_bibice.png
INFO pobierz_meteo: [HISTORY] Pruned 0 file(s) older than 24h, 7 file(s) kept.
INFO pobierz_meteo: [STATUS] Event 'meteogram_status' fired: success=True, message='82259 B, stddev 61.5'
INFO pobierz_meteo: === Meteogram run 20261030_174246 finished in 31.0s ===
```

## Testing from the command line

You can run the screenshot step without AppDaemon's scheduler. Output goes to the console. This doesn't fire the status event.

```bash
docker exec -it appdaemon python3 -c "import sys; sys.path.append('/conf/apps'); import pobierz_meteo; pobierz_meteo.fetch_meteogram(print)"
```

To run the full app, including the status event, reload it inside AppDaemon:

```bash
docker exec appdaemon touch /conf/apps/pobierz_meteo.py
```

## Troubleshooting

| Symptom | What to check |
|---|---|
| No `=== Meteogram run ... started ===` after the app starts | Make sure the app is registered in `apps.yaml` and the latest file is in `/conf/apps/`. |
| `session not created: This version of ChromeDriver only supports Chrome version X` | Install `chromium` and `chromium-chromedriver` from the same repository so the versions match. |
| `ModuleNotFoundError: No module named 'selenium'` (or `PIL`) | Add the Python packages (see [Requirements](#requirements)) and restart AppDaemon. |
| `Message malformed: not a valid option` when saving the script | In the UI script editor, leave out the top `meteogram_refresh:` line. |
| `crop box exceeds screenshot size` | Make the crop box smaller. The visible page area is about 1400×1061. |
| `image looks blank` / `too few distinct colours` | Look at the `_REJECTED_full.png` in the history folder: the page probably didn't render or a popup covers the chart. |
| Valid images rejected | Lower `MIN_FILE_SIZE`, `MIN_STDDEV` or `MIN_COLORS`. |
| `Page navigated to ... a click probably hit a link` | The overlay clicks landed on a page element. Check whether the site layout changed. |
| Status sensor stays `unknown` | Check for the `[STATUS] Event ... fired` log line, and listen to `meteogram_status` in Developer Tools → Events. |
| Dashboard shows an old image | The browser caches the image. Reload the dashboard. |

## Repository layout

```
.
├── pobierz_meteo.py        # AppDaemon app
├── meteo_config.txt        # example configuration
├── meteogram_status.yaml   # Home Assistant template binary_sensor
├── requirements.txt        # Python packages
├── system_packages.txt     # Alpine packages (Docker image)
└── README.md
```

## Disclaimer

This project isn't affiliated with ICM / meteo.pl. Forecast data and images belong to their owners, so check the site's terms of use before publishing captured images. The overlay handling depends on the site's current layout and may need adjusting if the site changes.
