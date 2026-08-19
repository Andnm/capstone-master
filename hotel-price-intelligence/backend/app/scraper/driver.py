"""
Selenium driver setup với anti-detection cho Booking.com (VN).
Port gần như nguyên văn từ Project/hotel_scraper_project/backend/app/services/booking_scraper.py
- đây là phần đã được tinh chỉnh qua thực tế, không viết lại logic chống bot.
"""
import asyncio
import base64
import os
import threading

from selenium import webdriver
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions

from app.core.config import settings

VN_GEO = {"latitude": 21.028511, "longitude": 105.854164, "accuracy": 100}
VN_TIMEZONE = "Asia/Ho_Chi_Minh"
VN_LOCALE = "vi-VN"

_LOCAL_PROXY_PORT = 18080
_local_proxy_started = False


def _ensure_local_auth_proxy() -> int:
    """Chạy 1 local HTTP CONNECT proxy KHÔNG cần auth (127.0.0.1), forward sang proxy VN thật
    (PROXY_SERVER) kèm header Proxy-Authorization. Chrome trỏ vào cổng local này thay vì trỏ thẳng
    vào proxy có auth.

    Lý do không dùng Chrome extension để tự điền credential (cách đã thử trước): đã verify bằng
    driver.get() trực tiếp — dù không raise exception, page_source trả về rỗng
    (``<html><head></head><body></body></html>``) chỉ sau 0.5s cho cả ipinfo.io lẫn Booking, tức
    Chrome fail xác thực proxy gần như ngay lập tức chứ không phải chậm. Extension MV3 với
    ``webRequestAuthProvider``/``asyncBlocking`` không đáng tin cậy khi nạp qua ``--load-extension``
    ở chế độ headless của bản Chrome đang cài. Cách local-proxy này không phụ thuộc Chrome
    extension API nào — chỉ CONNECT tunneling (đủ dùng vì Booking.com toàn HTTPS).
    """
    global _local_proxy_started
    if _local_proxy_started:
        return _LOCAL_PROXY_PORT

    upstream_host, upstream_port_str = settings.PROXY_SERVER.split(':')
    upstream_port = int(upstream_port_str)
    auth_header = base64.b64encode(
        f"{settings.PROXY_USERNAME}:{settings.PROXY_PASSWORD}".encode()
    ).decode()

    async def _pipe(src, dst):
        try:
            while True:
                data = await src.read(65536)
                if not data:
                    break
                dst.write(data)
                await dst.drain()
        except Exception:
            pass
        finally:
            dst.close()

    async def _handle_client(reader, writer):
        try:
            request_line = await reader.readline()
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b""):
                    break
            if not request_line.upper().startswith(b"CONNECT"):
                writer.close()
                return
            target = request_line.split()[1].decode()
            up_reader, up_writer = await asyncio.open_connection(upstream_host, upstream_port)
            up_writer.write(
                f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
                f"Proxy-Authorization: Basic {auth_header}\r\n"
                f"Proxy-Connection: Keep-Alive\r\n\r\n".encode()
            )
            await up_writer.drain()
            response = await up_reader.readuntil(b"\r\n\r\n")
            writer.write(response)
            await writer.drain()
            if b" 200" not in response.split(b"\r\n", 1)[0]:
                writer.close()
                up_writer.close()
                return
            await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
        except Exception:
            try:
                writer.close()
            except Exception:
                pass

    ready = threading.Event()

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _serve():
            server = await asyncio.start_server(_handle_client, "127.0.0.1", _LOCAL_PROXY_PORT)
            ready.set()
            async with server:
                await server.serve_forever()

        loop.run_until_complete(_serve())

    threading.Thread(target=_run, daemon=True, name="local-auth-proxy").start()
    ready.wait(timeout=5)
    _local_proxy_started = True
    return _LOCAL_PROXY_PORT


def _configure_proxy(options) -> None:
    """Áp dụng proxy (vd. proxy VN) cho Selenium nếu PROXY_SERVER được cấu hình trong .env.
    Xem DEPLOYMENT.md mục 4 — VPS đặt ở nước ngoài khiến Booking hiển thị giá lệch ~11.8% so với
    IP Việt Nam thật; proxy là cách khắc phục mà không cần đổi VPS.
    """
    if not settings.PROXY_SERVER:
        return
    if settings.PROXY_USERNAME and settings.PROXY_PASSWORD:
        port = _ensure_local_auth_proxy()
        options.add_argument(f'--proxy-server=127.0.0.1:{port}')
    else:
        options.add_argument(f'--proxy-server={settings.PROXY_SERVER}')


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
