import appdaemon.plugins.hass.hassapi as hass
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from PIL import Image
import time
import os

def fetch_meteogram(logger_func):
    """Downloads the meteogram webpage, dismisses overlays, and saves a cropped image."""
    config_path = "/conf/meteo_config.txt"
    target_url = "https://app.meteo.pl/meteogram/50.12,19.96"
    output_filename = "meteogram_bibice.png"
    output_dir = "/media"
    
    # Default fallback crop boundaries
    crop_left = 310
    crop_top = 140
    crop_width = 403
    crop_height = 208

    # Dynamic configuration loading from file
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("URL="):
                    target_url = line.replace("URL=", "").strip()
                elif line.startswith("FILENAME="):
                    output_filename = line.replace("FILENAME=", "").strip()
                elif line.startswith("CROP_LEFT="):
                    crop_left = int(line.replace("CROP_LEFT=", "").strip())
                elif line.startswith("CROP_TOP="):
                    crop_top = int(line.replace("CROP_TOP=", "").strip())
                elif line.startswith("CROP_WIDTH="):
                    crop_width = int(line.replace("CROP_WIDTH=", "").strip())
                elif line.startswith("CROP_HEIGHT="):
                    crop_height = int(line.replace("CROP_HEIGHT=", "").strip())
                elif line.startswith("OUTPUT_DIR="): 
                    output_dir = line.replace("OUTPUT_DIR=", "").strip()

    logger_func(f"[STEP 1] Target URL: {target_url} | Output file: {output_filename}")
    logger_func(f"[STEP 1] Bounding Box: {crop_left},{crop_top} to {crop_left+crop_width},{crop_top+crop_height}")
    
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1400,1200")
    
    service = Service(executable_path="/usr/bin/chromedriver")
    driver = None
    
    try:
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.get(target_url)
        logger_func("[STEP 2] Webpage loaded successfully.")
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
            actions = ActionChains(driver)
            body_element = driver.find_element(By.TAG_NAME, "body")
            test_coordinates_x = [100, 300, 500, 700, 900, 1100, 1300]
            for test_x in test_coordinates_x:
                try:
                    actions.move_to_element_with_offset(body_element, test_x, 20).click().perform()
                    driver.execute_script(f"document.elementFromPoint({test_x}, 20).click();")
                except:
                    continue
            logger_func("[SUCCESS] Overlay background interactions completed.")
            time.sleep(2)
        except Exception as e_action:
            logger_func(f"[INFO] Top-bar interaction sequence encountered an issue: {e_action}")

        # --- STEP 5: CAPTURE AND CROP GRAPH ---

        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
            
        temp_screenshot = os.path.join(output_dir, "temp_full_page.png")
        final_output = os.path.join(output_dir, output_filename)
        
        driver.get_screenshot_as_file(temp_screenshot)
        driver.quit()
        driver = None

        # Pixel cropping calculations via Pillow: (left, upper, right, lower)
        img = Image.open(temp_screenshot)
        crop_right = crop_left + crop_width
        crop_bottom = crop_top + crop_height
        
        cropped_img = img.crop((crop_left, crop_top, crop_right, crop_bottom))
        cropped_img.save(final_output)
        
        if os.path.exists(temp_screenshot):
            os.remove(temp_screenshot)
            
        logger_func(f"[DASHBOARD SUCCESS] Tailored {crop_width}x{crop_height} image created at: {final_output}")
        return True
        
    except Exception as e:
        logger_func(f"[CRITICAL ERROR] Execution failed: {e}")
        if driver:
            driver.quit()
        return False

class MeteoDownloader(hass.Hass):
    """AppDaemon application class for scheduling and initialization."""
    def initialize(self):
        self.log("=== METEO APPLICATION INITIALIZED ===")
        fetch_meteogram(self.log)
        
        config_path = "/conf/meteo_config.txt"
        refresh_interval = 900
        
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                for line in f:
                    if line.strip().startswith("REFRESH_INTERVAL="):
                        try:
                            refresh_interval = int(line.replace("REFRESH_INTERVAL=", "").strip())
                        except:
                            pass
                            
        self.log(f"Scheduling dynamic refresh interval to: {refresh_interval} seconds.")
        self.run_every(self.execute_download, f"now+{refresh_interval}", refresh_interval)

    def execute_download(self, kwargs):
        fetch_meteogram(self.log)
