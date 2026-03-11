from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time

try:
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    driver = webdriver.Chrome(options=chrome_options)
    driver.get("http://127.0.0.1:5002/admin")
    time.sleep(2)  # Let it load and throw any errors
    logs = driver.get_log('browser')
    if not logs:
        print("No browser logs found.")
    else:
        for log in logs:
            if log['level'] == 'SEVERE':
                print(f"ERROR: {log['message']}")
            else:
                print(f"LOG: {log['message']}")
    driver.quit()
except Exception as e:
    print(f"Failed to run Selenium: {e}")
