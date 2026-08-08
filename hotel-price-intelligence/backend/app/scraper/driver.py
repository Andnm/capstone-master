"""
Selenium driver setup với anti-detection cho Booking.com (VN).
Port gần như nguyên văn từ Project/hotel_scraper_project/backend/app/services/booking_scraper.py
- đây là phần đã được tinh chỉnh qua thực tế, không viết lại logic chống bot.
"""
import os

from selenium import webdriver
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions

VN_GEO = {"latitude": 21.028511, "longitude": 105.854164, "accuracy": 100}
VN_TIMEZONE = "Asia/Ho_Chi_Minh"
VN_LOCALE = "vi-VN"

def _apply_vn_spoofing(driver):
    try:
        # Headless Chrome tự khai báo ``HeadlessChrome`` trong UA. Booking hiện canonicalize
        # URL hotel và bỏ query checkin/checkout với UA này. Giữ nguyên đúng version browser
        # đang chạy, chỉ bỏ marker headless để không quay lại danh sách UA hard-code dễ lệch.
        native_user_agent = driver.execute_script("return navigator.userAgent") or ""
        browser_user_agent = native_user_agent.replace("HeadlessChrome/", "Chrome/")
        if browser_user_agent != native_user_agent:
            driver.execute_cdp_cmd("Network.setUserAgentOverride", {
                "userAgent": browser_user_agent,
                "acceptLanguage": "vi-VN,vi;q=0.9,en;q=0.8",
                "platform": "Windows",
            })
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
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--lang=vi-VN')
        # Giữ User-Agent native để luôn khớp đúng browser/version thực tế.
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
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--lang=vi-VN')
        # Giữ User-Agent native để luôn khớp đúng browser/version thực tế.
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
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--disable-gpu')
        options.add_argument('--lang=vi-VN')
        # Giữ User-Agent native để luôn khớp đúng browser/version thực tế.
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
