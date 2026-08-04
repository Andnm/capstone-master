"""
Selenium driver setup với anti-detection cho Booking.com (VN).
Port gần như nguyên văn từ Project/hotel_scraper_project/backend/app/services/booking_scraper.py
- đây là phần đã được tinh chỉnh qua thực tế, không viết lại logic chống bot.
"""
import os
import random

from selenium import webdriver
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions

VN_GEO = {"latitude": 21.028511, "longitude": 105.854164, "accuracy": 100}
VN_TIMEZONE = "Asia/Ho_Chi_Minh"
VN_LOCALE = "vi-VN"

_USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]


def get_random_user_agent() -> str:
    return random.choice(_USER_AGENTS)


def _apply_vn_spoofing(driver):
    try:
        driver.execute_cdp_cmd("Emulation.setGeolocationOverride", VN_GEO)
        driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": VN_TIMEZONE})
        driver.execute_cdp_cmd("Emulation.setLocaleOverride", {"locale": VN_LOCALE})
    except Exception:
        pass


def get_driver(is_headless: bool = True):
    """Trả về 1 Selenium driver đã cấu hình chống bot + giả lập VN.
    Docker/Linux (VPS) -> Chrome. Windows local -> Chrome, fallback Edge.
    """
    is_docker = os.path.exists('/.dockerenv') or os.path.exists('/usr/bin/google-chrome')

    if is_docker:
        from webdriver_manager.chrome import ChromeDriverManager

        options = ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--lang=vi-VN')
        options.add_argument(f'user-agent={get_random_user_agent()}')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--disable-extensions')
        options.add_argument('--no-first-run')
        options.add_argument('--disable-web-security')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_experimental_option("prefs", {
            "profile.default_content_setting_values.geolocation": 1,
            "profile.managed_default_content_settings.geolocation": 1,
            "intl.accept_languages": "vi-VN,vi,en",
            "profile.default_content_settings.popups": 0,
        })

        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        _apply_vn_spoofing(driver)
        return driver

    # Local Windows dev: Chrome trước, fallback Edge
    last_error = None
    try:
        from webdriver_manager.chrome import ChromeDriverManager

        options = ChromeOptions()
        if is_headless:
            options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--lang=vi-VN')
        options.add_argument(f'user-agent={get_random_user_agent()}')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--disable-web-security')
        options.add_experimental_option("prefs", {
            "profile.default_content_setting_values.geolocation": 1,
            "profile.managed_default_content_settings.geolocation": 1,
            "intl.accept_languages": "vi-VN,vi,en",
            "profile.default_content_settings.popups": 0,
        })

        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        _apply_vn_spoofing(driver)
        return driver
    except Exception as chrome_error:
        last_error = chrome_error

    try:
        from webdriver_manager.microsoft import EdgeChromiumDriverManager

        options = EdgeOptions()
        options.use_chromium = True
        if is_headless:
            options.add_argument('--headless=new')
        options.add_argument('--disable-gpu')
        options.add_argument('--lang=vi-VN')
        options.add_argument(f'user-agent={get_random_user_agent()}')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--disable-web-security')
        options.add_argument('--allow-running-insecure-content')
        options.add_experimental_option("prefs", {
            "profile.default_content_setting_values.geolocation": 1,
            "profile.managed_default_content_settings.geolocation": 1,
            "intl.accept_languages": "vi-VN,vi,en-US,en",
        })

        service = EdgeService(EdgeChromiumDriverManager().install())
        driver = webdriver.Edge(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        _apply_vn_spoofing(driver)
        return driver
    except Exception as edge_error:
        raise Exception(f"Failed to initialize browser driver. Chrome error: {last_error}. Edge error: {edge_error}")
