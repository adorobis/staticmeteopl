import appdaemon.plugins.hass.hassapi as hass
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from PIL import Image, ImageStat
from datetime import datetime, timedelta
import inspect
import io
import os
import threading
import time

CONFIG_PATH = "/conf/meteo_config.txt"

# Every supported config key with its default; the type of the default is used to parse the value
DEFAULTS = {
    "URL": "https://app.meteo.pl/meteogram/50.12,19.96",
    "FILENAME": "meteogram_bibice.png",
    "OUTPUT_DIR": "/media",
    "CROP_LEFT": 310,
    "CROP_TOP": 140,
    "CROP_WIDTH": 403,
    "CROP_HEIGHT": 208,
    "REFRESH_INTERVAL": 900,
    "PAGE_LOAD_TIMEOUT": 60,
    # History of every captured image (accepted and rejected) for later investigation
    "HISTORY_DIR": "meteo_history",  # relative to OUTPUT_DIR unless absolute
    "HISTORY_HOURS": 24,
    # Validation thresholds - an image failing any of them never replaces the previous one
    "MIN_FILE_SIZE": 3000,  # bytes of the encoded PNG
    "MIN_STDDEV": 8.0,      # grayscale standard deviation; a blank/flat image is close to 0
    "MIN_COLORS": 16,       # distinct colours; a blank image has only a handful
    # Event fired after every run; a template binary_sensor in Home Assistant turns it into an entity
    "STATUS_EVENT": "meteogram_status",
}


def make_logger(logger_func):
    """Wraps logger_func so log(msg, level=...) works both with AppDaemon's self.log and plain print."""
    try:
        params = inspect.signature(logger_func).parameters.values()
        accepts_level = any(p.name == "level" or p.kind == p.VAR_KEYWORD for p in params)
    except (TypeError, ValueError):
        accepts_level = False

    def log(msg, level="INFO"):
        if accepts_level:
            logger_func(msg, level=level)
        else:
            logger_func(msg if level == "INFO" else f"{level}: {msg}")
    return log


def load_config(logger_func):
    """Reads KEY=VALUE lines from the config file on top of DEFAULTS. Bad lines are logged and skipped."""
    logger_func = make_logger(logger_func)
    cfg = dict(DEFAULTS)
    if not os.path.exists(CONFIG_PATH):
        return cfg
    try:
        with open(CONFIG_PATH, "r") as f:
            for lineno, raw in enumerate(f, 1):
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = (part.strip() for part in line.split("=", 1))
                key = key.upper()
                if key not in DEFAULTS:
                    logger_func(f"[CONFIG] Unknown key '{key}' on line {lineno}, ignored.", level="WARNING")
                    continue
                try:
                    cfg[key] = type(DEFAULTS[key])(value)
                except ValueError:
                    logger_func(f"[CONFIG] Invalid value '{value}' for {key} on line {lineno}, using {cfg[key]}.", level="WARNING")
    except OSError as e:
        logger_func(f"[CONFIG] Could not read {CONFIG_PATH}: {e}. Using defaults.", level="WARNING")
    return cfg


def validate_image(img, png_size, cfg):
    """Returns (ok, reason). Rejects images that are too small, blank or nearly single-coloured."""
    if img.width != cfg["CROP_WIDTH"] or img.height != cfg["CROP_HEIGHT"]:
        return False, f"unexpected dimensions {img.width}x{img.height}"
    if png_size < cfg["MIN_FILE_SIZE"]:
        return False, f"file too small ({png_size} B < {cfg['MIN_FILE_SIZE']} B)"
    stddev = ImageStat.Stat(img.convert("L")).stddev[0]
    if stddev < cfg["MIN_STDDEV"]:
        return False, f"image looks blank (stddev {stddev:.1f} < {cfg['MIN_STDDEV']})"
    # getcolors() returns None when there are more colours than maxcolors - that is what we want
    if img.convert("RGB").getcolors(maxcolors=cfg["MIN_COLORS"]) is not None:
        return False, f"too few distinct colours (< {cfg['MIN_COLORS']})"
    return True, f"{png_size} B, stddev {stddev:.1f}"


def write_atomic(path, data):
    """Writes via a temp file + rename so the dashboard never reads a half-written image."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(data)
    os.replace(tmp_path, path)


def prune_history(history_dir, hours, logger_func):
    cutoff = time.time() - hours * 3600
    removed = 0
    for name in os.listdir(history_dir):
        path = os.path.join(history_dir, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError as e:
            logger_func(f"[HISTORY] Could not remove {path}: {e}", level="WARNING")
    remaining = len(os.listdir(history_dir))
    logger_func(f"[HISTORY] Pruned {removed} file(s) older than {hours}h, {remaining} file(s) kept.")


def fetch_meteogram(logger_func):
    """Downloads the meteogram webpage, dismisses overlays, and saves a cropped image.

    Returns (success, message). The previous image is only replaced when the new one passes validation.
    """
    logger_func = make_logger(logger_func)
    cfg = load_config(logger_func)
    target_url = cfg["URL"]
    output_filename = cfg["FILENAME"]
    output_dir = cfg["OUTPUT_DIR"]
    crop_left, crop_top = cfg["CROP_LEFT"], cfg["CROP_TOP"]
    crop_width, crop_height = cfg["CROP_WIDTH"], cfg["CROP_HEIGHT"]
    crop_right = crop_left + crop_width
    crop_bottom = crop_top + crop_height

    history_dir = os.path.join(output_dir, cfg["HISTORY_DIR"])
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(history_dir, exist_ok=True)
    final_output = os.path.join(output_dir, output_filename)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = os.path.splitext(output_filename)[0]
    started = time.monotonic()

    logger_func(f"=== Meteogram run {stamp} started ===")
    logger_func(f"[STEP 1] Config: {CONFIG_PATH} ({'found' if os.path.exists(CONFIG_PATH) else 'not found, using defaults'})")
    logger_func(f"[STEP 1] Target URL: {target_url} | Output file: {output_filename}")
    logger_func(f"[STEP 1] Bounding Box: {crop_left},{crop_top} to {crop_right},{crop_bottom}")
    logger_func(f"[STEP 1] History dir: {history_dir} (keeping {cfg['HISTORY_HOURS']}h)")

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1400,1200")
    # Keeps screenshot pixels 1:1 with CSS pixels so the crop box stays valid
    chrome_options.add_argument("--force-device-scale-factor=1")

    service = Service(executable_path="/usr/bin/chromedriver")
    driver = None

    try:
        logger_func("[STEP 2] Starting Chrome...")
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(cfg["PAGE_LOAD_TIMEOUT"])
        driver.get(target_url)
        logger_func(f"[STEP 2] Webpage loaded successfully. Title: '{driver.title}'")
        time.sleep(5)

        # --- MODAL 1: COOKIE CONSENT (FROZEN WORKING METHOD) ---
        logger_func("[STEP 3] Dismissing Cookie Consent overlay...")
        try:
            consent_button = driver.find_element(By.CSS_SELECTOR, "button[class*='consent'], button[class*='primary'], .fc-cta-consent")
            consent_button.click()
            logger_func("[SUCCESS] Cookie Consent dismissed.")
            time.sleep(2)
        except Exception as e_consent:
            logger_func(f"[INFO] Cookie Consent button not found or already skipped: {e_consent}")

        # --- MODAL 2: 'WHAT'S NEW' OVERLAY (FROZEN WORKING METHOD) ---
        logger_func("[STEP 4] Dismissing 'What's new' overlay via sequential top-bar clicks...")
        try:
            url_before = driver.current_url
            actions = ActionChains(driver)
            body_element = driver.find_element(By.TAG_NAME, "body")
            test_coordinates_x = [100, 300, 500, 700, 900, 1100, 1300]
            for test_x in test_coordinates_x:
                try:
                    actions.move_to_element_with_offset(body_element, test_x, 20).click().perform()
                    driver.execute_script(f"document.elementFromPoint({test_x}, 20).click();")
                    logger_func(f"[STEP 4] Offset ({test_x},20): clicked.")
                except Exception as e_point:
                    logger_func(f"[STEP 4] Offset ({test_x},20): skipped ({type(e_point).__name__}).")
                    continue
            if driver.current_url != url_before:
                logger_func(f"[STEP 4] Page navigated to {driver.current_url} - a click probably hit a link.", level="WARNING")
            logger_func("[SUCCESS] Overlay background interactions completed.")
            time.sleep(2)
        except Exception as e_action:
            logger_func(f"[INFO] Top-bar interaction sequence encountered an issue: {e_action}")

        # --- STEP 5: CAPTURE, CROP AND VALIDATE GRAPH ---
        full_png = driver.get_screenshot_as_png()
        driver.quit()
        driver = None

        full_img = Image.open(io.BytesIO(full_png))
        logger_func(f"[STEP 5] Screenshot captured: {full_img.width}x{full_img.height}, {len(full_png)} B")
        if crop_right > full_img.width or crop_bottom > full_img.height:
            # Pillow would silently pad the missing area with black, so treat it as a failure
            ok, reason = False, f"crop box exceeds screenshot size {full_img.width}x{full_img.height}"
            cropped_png = b""
        else:
            cropped_img = full_img.crop((crop_left, crop_top, crop_right, crop_bottom))
            buf = io.BytesIO()
            cropped_img.save(buf, format="PNG")
            cropped_png = buf.getvalue()
            ok, reason = validate_image(cropped_img, len(cropped_png), cfg)
        logger_func(f"[STEP 5] Validation {'passed' if ok else 'FAILED'}: {reason}")

        if ok:
            history_file = os.path.join(history_dir, f"{base_name}_{stamp}.png")
            write_atomic(history_file, cropped_png)
            logger_func(f"[STEP 6] History file saved: {history_file}")
            write_atomic(final_output, cropped_png)
            logger_func(f"[DASHBOARD SUCCESS] Tailored {crop_width}x{crop_height} image created at: {final_output} ({reason})")
            return True, reason

        # Keep the rejected crop and the full page so the cause can be investigated later
        if cropped_png:
            rejected_file = os.path.join(history_dir, f"{base_name}_{stamp}_REJECTED.png")
            write_atomic(rejected_file, cropped_png)
            logger_func(f"[STEP 6] Rejected crop saved: {rejected_file}")
        full_file = os.path.join(history_dir, f"{base_name}_{stamp}_REJECTED_full.png")
        write_atomic(full_file, full_png)
        logger_func(f"[STEP 6] Rejected full screenshot saved: {full_file}")
        logger_func(f"[VALIDATION FAILED] {reason}. Keeping previous image at {final_output}.", level="WARNING")
        return False, reason

    except Exception as e:
        logger_func(f"[CRITICAL ERROR] Execution failed: {e}", level="ERROR")
        # Selenium errors carry a long stack trace; the first line is enough for the status entity
        first_line = (str(e).strip().splitlines() or [type(e).__name__])[0]
        return False, f"error: {first_line[:250]}"

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        try:
            prune_history(history_dir, cfg["HISTORY_HOURS"], logger_func)
        except OSError as e:
            logger_func(f"[HISTORY] Pruning failed: {e}", level="WARNING")
        logger_func(f"=== Meteogram run {stamp} finished in {time.monotonic() - started:.1f}s ===")


class MeteoDownloader(hass.Hass):
    """AppDaemon application class for scheduling and initialization."""
    def initialize(self):
        self.log("=== METEO APPLICATION INITIALIZED ===")
        self._lock = threading.Lock()

        refresh_interval = load_config(self.log)["REFRESH_INTERVAL"]
        self.log(f"Scheduling dynamic refresh interval to: {refresh_interval} seconds.")
        # First download a few seconds after start in a worker thread instead of blocking initialize().
        # A separate run_in is used because "now" as run_every start is not handled the same in all AppDaemon versions.
        self.run_in(self.execute_download, 5)
        self.run_every(self.execute_download, f"now+{refresh_interval}", refresh_interval)
        # Manual refresh: fire this event from HA (Developer Tools -> Events, a script or a dashboard button)
        self.listen_event(self.on_refresh_event, "METEOGRAM_REFRESH")

    def on_refresh_event(self, event_name, data, kwargs):
        self.log(f"Manual refresh requested via {event_name} event.")
        self.execute_download({})

    def execute_download(self, kwargs):
        # Skip if the previous run is still going (e.g. the site hangs) rather than stacking up browsers
        if not self._lock.acquire(blocking=False):
            self.log("Previous download still running, skipping this cycle.", level="WARNING")
            return
        try:
            success, message = fetch_meteogram(self.log)
            self.publish_status(success, message)
        finally:
            self._lock.release()

    def publish_status(self, success, message):
        """Fires the status event. Entities created by AppDaemon cannot have a unique_id, so Home Assistant
        builds the binary_sensor itself from this event with a trigger-based template (see meteogram_status.yaml)."""
        event = load_config(self.log)["STATUS_EVENT"]
        try:
            self.fire_event(
                event,
                success=success,
                message=message,
                last_attempt=datetime.now().astimezone().isoformat(timespec="seconds"),
            )
            self.log(f"[STATUS] Event '{event}' fired: success={success}, message='{message}'")
        except Exception as e:
            self.log(f"Could not fire status event '{event}': {e}", level="WARNING")
