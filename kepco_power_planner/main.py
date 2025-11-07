import os
import time
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- Home Assistant API Configuration ---
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
API_URL = "http://supervisor/core/api"
HEADERS = {
    "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
    "content-type": "application/json",
}

def update_ha_sensor(entity_id, state, attributes):
    """Updates a Home Assistant sensor."""
    url = f"{API_URL}/states/{entity_id}"
    data = {
        "state": state,
        "attributes": attributes,
    }
    try:
        response = requests.post(url, headers=HEADERS, json=data)
        response.raise_for_status()
        print(f"Successfully updated {entity_id}")
    except requests.exceptions.RequestException as e:
        print(f"Error updating {entity_id}: {e}")

# --- Selenium Scraping Logic ---
RSA_USER_ID = os.environ.get("RSA_USER_ID")
RSA_USER_PWD = os.environ.get("RSA_USER_PWD")

# More robust Chrome options
chrome_options = Options()
chrome_options.add_argument("--headless=new")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--disable-software-rasterizer")
chrome_options.add_argument("--remote-debugging-port=9222")
chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

driver = webdriver.Chrome(executable_path='/usr/bin/chromedriver', options=chrome_options)

try:
    print("Starting KEPCO scrape job...")
    driver.get("https://pp.kepco.co.kr/")

    wait = WebDriverWait(driver, 20)

    # Login
    wait.until(EC.presence_of_element_located((By.ID, "RSA_USER_ID"))).send_keys(RSA_USER_ID)
    driver.find_element(By.ID, "RSA_USER_PWD").send_keys(RSA_USER_PWD)
    login_button = wait.until(EC.presence_of_element_located((By.ID, "intro_btn_indi")))
    driver.execute_script("arguments[0].click();", login_button)

    print("Logged in, waiting for data...")

    # Wait for data elements to be populated
    wait.until(lambda d: d.find_element(By.ID, "F_AP_QT").text.strip() != "")
    wait.until(lambda d: d.find_element(By.ID, "PREDICT_TOT").text.strip() != "")
    wait.until(lambda d: d.find_element(By.ID, "TOTAL_CHARGE").text.strip() != "")
    wait.until(lambda d: d.find_element(By.ID, "PREDICT_TOTAL_CHARGE").text.strip() != "")

    # Data validation and retry logic
    max_retries = 5
    for i in range(max_retries):
        # Data cleaning
        realtime_usage_str = driver.find_element(By.ID, "F_AP_QT").text
        predicted_usage_str = driver.find_element(By.ID, "PREDICT_TOT").text
        realtime_fee_str = driver.find_element(By.ID, "TOTAL_CHARGE").text
        predicted_fee_str = driver.find_element(By.ID, "PREDICT_TOTAL_CHARGE").text

        # Check for empty strings before processing
        if not all([realtime_usage_str, predicted_usage_str, realtime_fee_str, predicted_fee_str]):
            if i < max_retries - 1:
                time.sleep(2) # wait a bit before retry
                continue
            else:
                raise ValueError("Failed to scrape data, fields were empty.")

        realtime_usage = float(realtime_usage_str.replace('kWh', '').strip())
        predicted_usage = float(predicted_usage_str.replace('kWh', '').strip())
        realtime_fee = int(realtime_fee_str.replace('원', '').replace(',', '').strip())
        predicted_fee = int(predicted_fee_str.replace('원', '').replace(',', '').strip())

        # Data validation
        usage_same = (realtime_usage == predicted_usage)
        charge_same = (realtime_fee == predicted_fee)

        if usage_same == charge_same:
            print("Data appears consistent.")
            break
        
        print(f"Data inconsistency found (attempt {i+1}/{max_retries}). Refreshing...")
        if i < max_retries - 1:
            driver.refresh()
            # Wait for elements to be populated again after refresh
            wait.until(lambda d: d.find_element(By.ID, "F_AP_QT").text.strip() != "")
            wait.until(lambda d: d.find_element(By.ID, "PREDICT_TOT").text.strip() != "")
            wait.until(lambda d: d.find_element(By.ID, "TOTAL_CHARGE").text.strip() != "")
            wait.until(lambda d: d.find_element(By.ID, "PREDICT_TOTAL_CHARGE").text.strip() != "")
    else:
        print("Warning: Data might be inconsistent after 5 retries.")


    print("Data scraped, updating Home Assistant sensors...")

    # Update Home Assistant sensors with cleaned data
    update_ha_sensor("sensor.kepco_realtime_usage", realtime_usage, {"friendly_name": "실시간 사용량", "unit_of_measurement": "kWh", "icon": "mdi:flash"})
    update_ha_sensor("sensor.kepco_predicted_usage", predicted_usage, {"friendly_name": "예상 사용량", "unit_of_measurement": "kWh", "icon": "mdi:flash-alert"})
    update_ha_sensor("sensor.kepco_realtime_fee", realtime_fee, {"friendly_name": "실시간 요금", "unit_of_measurement": "원", "icon": "mdi:cash"})
    update_ha_sensor("sensor.kepco_predicted_fee", predicted_fee, {"friendly_name": "예상 요금", "unit_of_measurement": "원", "icon": "mdi:cash-multiple"})

    print("Home Assistant sensors updated.")

except Exception as e:
    print(f"An error occurred during the scrape job: {e}")

finally:
    driver.quit()
    print("Scrape job finished.")