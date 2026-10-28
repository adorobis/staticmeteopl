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
    config_path = "/conf/meteo_config.txt"
    target_url = "https://app.meteo.pl/meteogram/50.12,19.96"
    output_filename = "meteogram_bibice.png"
    output_dir = "/media"
    
    crop_left, crop_top, crop_width, crop_height = 305, 122, 403, 208

    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("URL="): target_url = line.replace("URL=", "").strip()
                elif line.startswith("FILENAME="): output_filename = line.replace("FILENAME=", "").strip()
                elif line.startswith("CROP_LEFT="): crop_left = int(line.replace("CROP_LEFT=", "").strip())
                elif line.startswith("CROP_TOP="): crop_top = int(line.replace("CROP_TOP=", "").strip())
                elif line.startswith("CROP_WIDTH="): crop_width = int(line.replace("CROP_WIDTH=", "").strip())
                elif line.startswith("CROP_HEIGHT="): crop_height = int(line.replace("CROP_HEIGHT=", "").strip())
                elif line.startswith("OUTPUT_DIR="): output_dir = line.replace("OUTPUT_DIR=", "").strip()

    logger_func(f"[STEP 1] Target URL: {target_url} | Output dir: {output_dir}")
    
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
        time.sleep(5)

        # Dismiss Cookie Consent (Frozen Method)
        try:
            driver.find_element(By.CSS_SELECTOR, "button[class*='consent'], .fc-cta-consent").click()
            time.sleep(2)
        except: pass

        # Dismiss 'What's new' modal (Frozen Method)
        try:
            actions = ActionChains(driver)
            body_element = driver.find_element(By.TAG_NAME, "body")
            test_coordinates_x = [100, 300, 500, 700, 900, 1100, 1300]
            for test_x in test_coordinates_x:
                try:
                    actions.move_to_element_with_offset(body_element, test_x, 20).click().perform()
                    driver.execute_script(f"document.elementFromPoint({test_x}, 20).click();")
                except: continue
            time.sleep(2)
        except: pass

        os.makedirs(output_dir, exist_ok=True)
            
        temp_screenshot = os.path.join(output_dir, "temp_full_page.png")
        img_output_path = os.path.join(output_dir, "chart.png")
        html_output_path = os.path.join(output_dir, output_filename)
        
        driver.get_screenshot_as_file(temp_screenshot)
        driver.quit()
        driver = None

        # Crop image
        img = Image.open(temp_screenshot)
        cropped_img = img.crop((crop_left, crop_top, crop_left + crop_width, crop_top + crop_height))
        cropped_img.save(img_output_path)
        
        if os.path.exists(temp_screenshot):
            os.remove(temp_screenshot)

        # Create HTML wrapper
        with open(html_output_path, "w") as html_file:
            html_file.write(f"<html><body style='margin:0;padding:0;overflow:hidden;'><img src='chart.png' style='width:{crop_width}px;height:{crop_height}px;'></body></html>")
            
        logger_func(f"[DASHBOARD SUCCESS] Production files updated at: {html_output_path}")
        return True
        
    except Exception as e:
        logger_func(f"[CRITICAL ERROR] {e}")
        if driver: driver.quit()
        return False

class MeteoDownloader(hass.Hass):
    """AppDaemon application class for scheduling and initialization."""
    def initialize(self):
        self.log("=== METEO APPLICATION INITIALIZED ===")
        fetch_meteogram(self.log)
        
        # Read the dynamic refresh interval from configuration
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
