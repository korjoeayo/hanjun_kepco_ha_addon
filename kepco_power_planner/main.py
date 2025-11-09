import os
import time
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException

# --- Home Assistant API Configuration ---
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
if not SUPERVISOR_TOKEN:
    raise ValueError("SUPERVISOR_TOKEN environment variable not set.")

API_URL = "http://supervisor/core/api"
HEADERS = {
    "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
    "content-type": "application/json",
}

def update_ha_sensor(entity_id, state, attributes):
    """Updates a Home Assistant sensor."""
    url = f"{API_URL}/states/{entity_id}"
    data = {"state": state, "attributes": attributes}
    try:
        response = requests.post(url, headers=HEADERS, json=data)
        response.raise_for_status()
        print(f"Successfully updated {entity_id}")
    except requests.exceptions.RequestException as e:
        print(f"Error updating {entity_id}: {e}")

# --- Selenium Scraping Logic ---
RSA_USER_ID = os.environ.get("RSA_USER_ID")
RSA_USER_PWD = os.environ.get("RSA_USER_PWD")

def create_sensor_set(cust_no, sensor_data):
    """Creates a set of sensors for a given customer number."""
    sensors = {
        "realtime_usage": {"name": "실시간 사용량", "unit": "kWh", "icon": "mdi:flash", "device_class": "energy"},
        "predicted_usage": {"name": "예상 사용량", "unit": "kWh", "icon": "mdi:flash-alert", "device_class": "energy"},
        "realtime_fee": {"name": "실시간 요금", "unit": "원", "icon": "mdi:cash", "device_class": "monetary"},
        "predicted_fee": {"name": "예상 요금", "unit": "원", "icon": "mdi:cash-multiple", "device_class": "monetary"},
        "generation_amount": {"name": "발전량", "unit": "kWh", "icon": "mdi:solar-power", "device_class": "energy"},
        "net_realtime_charge": {"name": "상계 후 요금", "unit": "원", "icon": "mdi:cash-minus", "device_class": "monetary"},
    }

    for sensor_type, data in sensor_data.items():
        if data is not None and sensor_type in sensors:
            config = sensors[sensor_type]
            entity_id = f"sensor.kepco_{cust_no}_{sensor_type}"
            unique_id = f"kepco_power_planner_{cust_no}_{sensor_type}"
            friendly_name = f"{config['name']} ({cust_no})"
            
            attributes = {
                "friendly_name": friendly_name,
                "unit_of_measurement": config["unit"],
                "icon": config["icon"],
                "device_class": config["device_class"],
                "customer_number": cust_no,
            }
            update_ha_sensor(entity_id, data, attributes) # Removed unique_id argument

def scrape_data_for_cust_no(driver, cust_no):
    """Scrapes all relevant data for a specific customer number."""
    wait = WebDriverWait(driver, 20)
    sensor_data = {}

    print(f"Scraping data for customer number: {cust_no}")

    # --- Scrape Main Page Data ---
    try:
        # Wait for data elements to be populated and not empty
        wait.until(lambda d: d.find_element(By.ID, "F_AP_QT").text.strip() != "")
        
        # Add a small delay to ensure JS has updated the values
        time.sleep(2)

        realtime_usage_str = driver.find_element(By.ID, "F_AP_QT").text
        predicted_usage_str = driver.find_element(By.ID, "PREDICT_TOT").text
        realtime_fee_str = driver.find_element(By.ID, "TOTAL_CHARGE").text
        predicted_fee_str = driver.find_element(By.ID, "PREDICT_TOTAL_CHARGE").text

        sensor_data["realtime_usage"] = float(realtime_usage_str.replace('kWh', '').replace(',', '').strip())
        sensor_data["predicted_usage"] = float(predicted_usage_str.replace('kWh', '').replace(',', '').strip())
        sensor_data["realtime_fee"] = int(realtime_fee_str.replace('원', '').replace(',', '').strip())
        sensor_data["predicted_fee"] = int(predicted_fee_str.replace('원', '').replace(',', '').strip())
    except (NoSuchElementException, TimeoutException, ValueError) as e:
        print(f"Could not scrape main page data for {cust_no}: {e}")
        return None # Abort for this customer if main data fails

    # --- Scrape Detailed Page for Generation Data ---
    try:
        driver.get("https://pp.kepco.co.kr/pr/pr0201.do?menu_id=O020401")
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "smart_now")))
        
        thead = driver.find_element(By.CSS_SELECTOR, "div.smart_now thead")
        if len(thead.find_elements(By.TAG_NAME, 'tr')) > 0:
            power_rate_row = driver.find_element(By.XPATH, "//th[contains(text(), '전력량요금')]/..")
            last_td = power_rate_row.find_elements(By.TAG_NAME, 'td')[-1]
            net_usage_str = last_td.text.replace('kWh', '').strip()
            net_usage = float(net_usage_str.replace(',', ''))
            
            generation_amount = sensor_data["realtime_usage"] - net_usage
            sensor_data["generation_amount"] = round(generation_amount, 3)

            charge_row = driver.find_element(By.XPATH, "//tfoot//th[contains(text(), '실시간 요금')]/..")
            last_charge_td = charge_row.find_elements(By.TAG_NAME, 'td')[-1]
            net_charge_str = last_charge_td.text.replace('원', '').replace(',', '').strip()
            sensor_data["net_realtime_charge"] = int(net_charge_str)
    except (NoSuchElementException, IndexError, ValueError, TimeoutException) as e:
        print(f"Could not find generation data for {cust_no}, skipping. Error: {e}")
    finally:
        # Go back to the main page for the next iteration
        driver.get("https://pp.kepco.co.kr/main.do")
        wait.until(EC.presence_of_element_located((By.ID, "country_id")))

    return sensor_data


# --- Main Execution ---
chrome_options = Options()
chrome_options.add_argument("--headless=new")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--disable-software-rasterizer")
chrome_options.add_argument("--remote-debugging-port=9222")
chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

service = Service(executable_path='/usr/bin/chromedriver')
driver = webdriver.Chrome(service=service, options=chrome_options)

try:
    print("Starting KEPCO scrape job...")
    driver.get("https://pp.kepco.co.kr/")
    wait = WebDriverWait(driver, 20)

    # Login
    wait.until(EC.presence_of_element_located((By.ID, "RSA_USER_ID"))).send_keys(RSA_USER_ID)
    driver.find_element(By.ID, "RSA_USER_PWD").send_keys(RSA_USER_PWD)
    login_button = wait.until(EC.element_to_be_clickable((By.ID, "intro_btn_indi")))
    driver.execute_script("arguments[0].click();", login_button)
    print("Logged in.")

    # Wait for main page to load after login
    wait.until(EC.presence_of_element_located((By.ID, "country_id")))
    
    # Get all customer numbers
    cust_no_select = driver.find_element(By.ID, "country_id")
    cust_no_options = cust_no_select.find_elements(By.TAG_NAME, "option")
    customer_numbers = [opt.get_attribute("value") for opt in cust_no_options]
    print(f"Found customer numbers: {customer_numbers}")

    # Get the dynamic ID for the selectbox
    sb_value = cust_no_select.get_attribute("sb")
    sb_holder_id = f"sbHolder_{sb_value}"
    sb_options_id = f"sbOptions_{sb_value}"

    # Iterate through each customer number
    for i, cust_no in enumerate(customer_numbers):
        print("-" * 20)
        # For the first customer number, the data is already loaded.
        # For subsequent numbers, we need to select it from the dropdown.
        if i > 0:
            print(f"Switching to customer number: {cust_no}")
            # This site uses a custom selectbox, so we interact with its elements
            sb_holder = driver.find_element(By.ID, sb_holder_id)
            sb_holder.click()
            # Wait for dropdown options to be visible
            wait.until(EC.visibility_of_element_located((By.ID, sb_options_id)))
            # Click the option corresponding to the customer number
            driver.find_element(By.XPATH, f"//a[@rel='{cust_no}']").click()
        
        # Scrape and update sensors
        scraped_data = scrape_data_for_cust_no(driver, cust_no)
        if scraped_data:
            create_sensor_set(cust_no, scraped_data)
            print(f"Successfully updated sensors for {cust_no}")

except Exception as e:
    print(f"An unexpected error occurred: {e}")

finally:
    driver.quit()
    print("Scrape job finished.")