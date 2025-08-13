# automation/browser_config.py
from __future__ import annotations
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium import webdriver

def get_chrome_options(headless: bool = True, download_path: str | None = None):
    opts = ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1366,1050")
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])
    opts.add_argument("--log-level=3")
    opts.add_argument("--disable-logging")
    prefs = {
        "download.default_directory": download_path or "",
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    }
    opts.add_experimental_option("prefs", prefs)
    return opts, None, None

def build_driver(headless: bool = True, download_path: str | None = None) -> webdriver.Chrome:
    opts, _, _ = get_chrome_options(headless=headless, download_path=download_path)
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    return driver
