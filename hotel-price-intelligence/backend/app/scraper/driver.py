"""
Selenium driver setup với anti-detection cho Booking.com (VN).
Port gần như nguyên văn từ Project/hotel_scraper_project/backend/app/services/booking_scraper.py
- đây là phần đã được tinh chỉnh qua thực tế, không viết lại logic chống bot.
"""
import os
import tempfile

from selenium import webdriver
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions

from app.core.config import settings

VN_GEO = {"latitude": 21.028511, "longitude": 105.854164, "accuracy": 100}
VN_TIMEZONE = "Asia/Ho_Chi_Minh"
VN_LOCALE = "vi-VN"

_proxy_auth_extension_dir = None


def _get_proxy_auth_extension() -> str:
    """Tạo 1 lần/process extension unpacked để tự điền user/pass khi Chrome hỏi xác thực proxy.
    Chrome không cho nhúng credential thẳng vào --proxy-server (khác SOCKS5 URL thông thường),
    đây là cách chuẩn để Selenium vượt qua proxy có auth mà không cần selenium-wire.
    """
    global _proxy_auth_extension_dir
    if _proxy_auth_extension_dir is not None:
        return _proxy_auth_extension_dir

    extension_dir = tempfile.mkdtemp(prefix="proxy_auth_ext_")
    manifest = """{
  "manifest_version": 2,
  "name": "Proxy Auth",
  "version": "1.0.0",
  "permissions": ["proxy", "webRequest", "webRequestBlocking", "<all_urls>"],
  "background": {"scripts": ["background.js"]}
}"""
    background = f"""chrome.webRequest.onAuthRequired.addListener(
  function(details) {{
    return {{authCredentials: {{username: "{settings.PROXY_USERNAME}", password: "{settings.PROXY_PASSWORD}"}}}};
  }},
  {{urls: ["<all_urls>"]}},
  ["blocking"]
);"""
    with open(os.path.join(extension_dir, "manifest.json"), "w", encoding="utf-8") as f:
        f.write(manifest)
    with open(os.path.join(extension_dir, "background.js"), "w", encoding="utf-8") as f:
        f.write(background)

    _proxy_auth_extension_dir = extension_dir
    return extension_dir


def _proxy_needs_auth_extension() -> bool:
    return bool(settings.PROXY_SERVER and settings.PROXY_USERNAME and settings.PROXY_PASSWORD)


def _configure_proxy(options) -> None:
    """Áp dụng proxy (vd. proxy VN) cho Selenium nếu PROXY_SERVER được cấu hình trong .env.
    Xem DEPLOYMENT.md mục 4 — VPS đặt ở nước ngoài khiến Booking hiển thị giá lệch ~11.8% so với
    IP Việt Nam thật; proxy là cách khắc phục mà không cần đổi VPS.
    """
    if not settings.PROXY_SERVER:
        return
    options.add_argument(f'--proxy-server={settings.PROXY_SERVER}')
    if _proxy_needs_auth_extension():
        options.add_argument(f'--load-extension={_get_proxy_auth_extension()}')


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
        # --disable-extensions phải bỏ khi dùng proxy có auth, vì nó vô hiệu hoá luôn
        # extension tự điền credential (--load-extension) do _configure_proxy() thêm bên dưới.
        if not _proxy_needs_auth_extension():
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
        _configure_proxy(options)

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
        _configure_proxy(options)

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
        _configure_proxy(options)

        service = EdgeService(EdgeChromiumDriverManager().install())
        driver = webdriver.Edge(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        _apply_vn_spoofing(driver)
        return driver
    except Exception as edge_error:
        raise Exception(f"Failed to initialize browser driver. Chrome error: {last_error}. Edge error: {edge_error}")
