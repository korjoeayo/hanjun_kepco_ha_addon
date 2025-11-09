import os
import time
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException

# --- Home Assistant API Configuration ---
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
if not SUPERVISOR_TOKEN:
    raise ValueError("SUPERVISOR_TOKEN environment variable not set. Please ensure the add-on has API access enabled and has been restarted.")

API_URL = "http://supervisor/core/api"
HEADERS = {
    "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
    "content-type": "application/json",
}

def update_ha_sensor(entity_id, state, attributes, unique_id=None):
    """Updates a Home Assistant sensor."""
    url = f"{API_URL}/states/{entity_id}"
    data = {
        "state": state,
        "attributes": attributes,
    }
    if unique_id:
        data["attributes"]["unique_id"] = unique_id
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

service = Service(executable_path='/usr/bin/chromedriver')
driver = webdriver.Chrome(service=service, options=chrome_options)

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

        realtime_usage = float(realtime_usage_str.replace('kWh', '').replace(',', '').strip())
        predicted_usage = float(predicted_usage_str.replace('kWh', '').replace(',', '').strip())
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


    # "실시간 요금" 상세 페이지로 이동
    driver.get("https://pp.kepco.co.kr/pr/pr0201.do?menu_id=O020401")
    
    # div.smart_now 로딩 대기
    wait.until(EC.presence_of_element_located((By.CLASS_NAME, "smart_now")))

    # thead가 있는지 확인하고 발전량 및 상계 후 요금 계산
    has_thead = False
    generation_amount = 0.0
    net_realtime_charge = 0
    try:
        thead = driver.find_element(By.CSS_SELECTOR, "div.smart_now thead")
        if len(thead.find_elements(By.TAG_NAME, 'tr')) > 0:
            has_thead = True
        
        if has_thead:
            try:
                # '전력량요금' 행을 찾아 마지막 칸의 값을 가져옴
                power_rate_row = driver.find_element(By.XPATH, "//th[contains(text(), '전력량요금')]/..")
                last_td = power_rate_row.find_elements(By.TAG_NAME, 'td')[-1]
                net_usage_str = last_td.text.replace('kWh', '').strip()
                net_usage = float(net_usage_str.replace(',', ''))
                
                # 발전량 계산 (실시간 사용량 - 상계 후 사용량)
                generation_amount = realtime_usage - net_usage

                # '실시간 요금' 행을 tfoot에서 찾아 마지막 칸의 값을 가져옴
                charge_row = driver.find_element(By.XPATH, "//tfoot//th[contains(text(), '실시간 요금')]/..")
                last_charge_td = charge_row.find_elements(By.TAG_NAME, 'td')[-1]
                net_charge_str = last_charge_td.text.replace('원', '').replace(',', '').strip()
                net_realtime_charge = int(net_charge_str)
            except (NoSuchElementException, IndexError, ValueError):
                # 관련 요소를 찾지 못하거나 값 변환에 실패하면 값들은 0으로 유지
                pass
    except NoSuchElementException:
        # thead가 없으면 False로 유지
        pass

    print("Data scraped, updating Home Assistant sensors...")

    # Update Home Assistant sensors with cleaned data
    update_ha_sensor(
        "sensor.kepco_realtime_usage",
        realtime_usage,
        {
            "friendly_name": "실시간 사용량",
            "unit_of_measurement": "kWh",
            "icon": "mdi:flash",
            "device_class": "energy"
        },
        "kepco_realtime_usage"
    )
    update_ha_sensor(
        "sensor.kepco_predicted_usage",
        predicted_usage,
        {
            "friendly_name": "예상 사용량",
            "unit_of_measurement": "kWh",
            "icon": "mdi:flash-alert",
            "device_class": "energy"
        },
        "kepco_predicted_usage"
    )
    update_ha_sensor(
        "sensor.kepco_realtime_fee",
        realtime_fee,
        {
            "friendly_name": "실시간 요금",
            "unit_of_measurement": "원",
            "icon": "mdi:cash",
            "device_class": "monetary"
        },
        "kepco_realtime_fee"
    )
    update_ha_sensor(
        "sensor.kepco_predicted_fee",
        predicted_fee,
        {
            "friendly_name": "예상 요금",
            "unit_of_measurement": "원",
            "icon": "mdi:cash-multiple",
            "device_class": "monetary"
        },
        "kepco_predicted_fee"
    )

    if has_thead:
        update_ha_sensor(
            "sensor.kepco_generation_amount",
            round(generation_amount, 3),
            {
                "friendly_name": "발전량",
                "unit_of_measurement": "kWh",
                "icon": "mdi:solar-power",
                "device_class":
                "energy"
            },
            "kepco_generation_amount"
        )
        update_ha_sensor(
            "sensor.kepco_net_realtime_charge",
            net_realtime_charge,
            {
                "friendly_name": "상계 후 요금",
                "unit_of_measurement": "원",
                "icon": "mdi:cash-minus",
                "device_class": "monetary"
            },
            "kepco_net_realtime_charge"
        )

    print("Home Assistant sensors updated.")

except Exception as e:
    print(f"An error occurred during the scrape job: {e}")

finally:
    driver.quit()
    print("Scrape job finished.")