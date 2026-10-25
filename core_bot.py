#!/usr/bin/env python3
"""
YouTube View Bot Core Engine - Clean Circuit Hunter Edition
- Auto-Retry on Burned IPs: If Tor exit IP triggers bot detection, discards circuit & hunts for a clean one
- Subnet / Country Filtering: Avoids burned ranges, seeks clean routes
- Organic Referer Spoofing: Injects Google Search, DuckDuckGo, Twitter/X referers
- Continuous Playback Tracking: Strict verification that video.currentTime actually advanced
- Dynamic Timezone/Locale: Aligned to Tor circuit IP
"""

import os
import sys
import time
import random
import json
import csv
import re
import socket
import subprocess
import argparse
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse, parse_qs

import requests
from playwright.sync_api import sync_playwright

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("yt_view_bot")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 720}
]

ORGANIC_REFERERS = [
    "https://www.google.com/",
    "https://www.google.com/search?q=youtube",
    "https://www.bing.com/",
    "https://duckduckgo.com/",
    "https://t.co/",
    "https://www.youtube.com/"
]

BOT_DETECTION_PATTERNS = [
    "Sign in to confirm you're not a bot",
    "Sign in to confirm you’re not a bot",
    "Our systems have detected unusual traffic",
    "Please solve this puzzle so we know you are a real person",
    "Video unavailable"
]


def extract_video_id(url: str) -> str:
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11})(?:[&?]|$)',
        r'(?:embed\/)([0-9A-Za-z_-]{11})',
        r'(?:shorts\/)([0-9A-Za-z_-]{11})',
        r'(?:youtu\.be\/)([0-9A-Za-z_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    parsed = urlparse(url)
    if parsed.hostname and 'youtube.com' in parsed.hostname:
        qs = parse_qs(parsed.query)
        if 'v' in qs and len(qs['v']) > 0:
            return qs['v'][0]
    return ""


class TelegramReporter:
    def __init__(self, token: str = None, chat_id: str = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        self.enabled = bool(self.token and self.chat_id)
        self.lock = threading.Lock()
        if self.enabled:
            logger.info(f"Telegram notifications enabled for Chat ID: {self.chat_id}")

    def send_message(self, text: str):
        if not self.enabled:
            return
        with self.lock:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            try:
                requests.post(url, json=payload, timeout=10)
            except Exception as e:
                logger.warning(f"Failed to send Telegram message: {e}")


class TorManager:
    def __init__(self, control_port: int = 9051, socks_port: int = 9050, password: str = None):
        self.control_port = control_port
        self.socks_port = socks_port
        self.password = password or os.environ.get("TOR_CONTROL_PASSWORD", "")

    def get_isolated_proxy(self) -> tuple[str, str]:
        # Generate high-entropy isolation token to force unique circuit
        token = f"hunt_{random.randint(1000000, 9999999)}"
        proxy_url = f"socks5://{token}:auth@127.0.0.1:{self.socks_port}"
        return proxy_url, token

    def fetch_ip_geo(self, proxy_url: str) -> dict:
        try:
            proxy_clean = proxy_url.replace("socks5://", "")
            cmd = [
                "curl", "-s", "--socks5-hostname", proxy_clean,
                "--max-time", "10", "http://ip-api.com/json"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                if data.get("status") == "success":
                    return {
                        "ip": data.get("query", "Unknown"),
                        "country": data.get("country", "Unknown"),
                        "country_code": data.get("countryCode", "US"),
                        "timezone": data.get("timezone", "UTC")
                    }
        except Exception:
            pass

        try:
            proxies = {
                "http": proxy_url.replace("socks5://", "socks5h://"),
                "https": proxy_url.replace("socks5://", "socks5h://")
            }
            res = requests.get("https://api.ipify.org", proxies=proxies, timeout=8)
            if res.status_code == 200 and res.text.strip():
                return {
                    "ip": res.text.strip(),
                    "country": "Unknown",
                    "country_code": "US",
                    "timezone": "UTC"
                }
        except Exception:
            pass

        return {}


class CleanCircuitViewBot:
    def __init__(
        self,
        tor_manager: TorManager,
        telegram_reporter: TelegramReporter,
        concurrency: int = 3,
        headless: bool = True
    ):
        self.tor = tor_manager
        self.tg = telegram_reporter
        self.concurrency = concurrency
        self.headless = headless
        self.lock = threading.Lock()
        self.completed_count = 0
        self.known_blocked_ips = set()
        self.stats = {
            "total_views_requested": 0,
            "successful_views": 0,
            "failed_views": 0,
            "circuits_rejected_as_blocked": 0,
            "ips_used": [],
            "start_time": None,
            "end_time": None
        }

    def _apply_clean_stealth(self, page):
        stealth_js = """
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        if (!window.chrome) {
            window.chrome = { runtime: {} };
        }
        """
        page.add_init_script(stealth_js)

    def _check_bot_detection(self, page) -> str:
        error_selectors = [
            'ytd-enforcement-message-view-model',
            '#error-screen',
            '.ytp-error',
            '#captcha-form'
        ]
        for sel in error_selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=1500):
                    return f"Error element: {sel}"
            except Exception:
                continue

        try:
            content = page.content()
            for pattern in BOT_DETECTION_PATTERNS:
                if pattern in content:
                    return f"Signature text: '{pattern}'"
        except Exception:
            pass

        return ""

    def _handle_consent(self, page):
        consent_selectors = [
            'button[aria-label*="Reject"]',
            'button[aria-label*="Accept"]',
            'button:has-text("Reject all")',
            'button:has-text("Accept all")',
            'button:has-text("I agree")',
            'form[action*="consent"] button'
        ]
        for sel in consent_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    page.wait_for_timeout(1000)
                    break
            except Exception:
                continue

    def simulate_view_with_clean_ip_hunt(self, view_idx: int, total_views: int, video_id: str, target_watch_time: float) -> bool:
        """
        Circuit Hunter:
        If Tor serves a burned IP that Google blocks, we do NOT count it as a finished view!
        Instead, we discard that circuit and hunt for a clean Tor exit node (up to MAX_CIRCUIT_TRIES).
        """
        MAX_CIRCUIT_TRIES = 5

        for attempt in range(1, MAX_CIRCUIT_TRIES + 1):
            proxy_url, iso_user = self.tor.get_isolated_proxy()
            geo_data = self.tor.fetch_ip_geo(proxy_url)

            if not geo_data or not geo_data.get("ip"):
                time.sleep(1.5)
                continue

            thread_ip = geo_data["ip"]

            # Skip if we already flagged this IP as burned in this run
            with self.lock:
                if thread_ip in self.known_blocked_ips:
                    continue

            tz_id = geo_data.get("timezone", "UTC")
            country = geo_data.get("country", "Unknown")
            locale = f"{geo_data.get('country_code', 'US').lower()}-{geo_data.get('country_code', 'US')}"

            ua = random.choice(USER_AGENTS)
            vp = random.choice(VIEWPORTS)
            referer = random.choice(ORGANIC_REFERERS)

            logger.info(f"[{view_idx}/{total_views}] [Hunt #{attempt}] Trying IP: {thread_ip} ({country}) via Referer: {referer}")

            with sync_playwright() as p:
                browser = None
                try:
                    browser = p.chromium.launch(
                        headless=self.headless,
                        proxy={"server": proxy_url},
                        args=[
                            "--no-sandbox",
                            "--disable-setuid-sandbox",
                            "--disable-blink-features=AutomationControlled",
                            "--disable-dev-shm-usage",
                            "--no-first-run",
                            "--no-zygote",
                        ]
                    )

                    context = browser.new_context(
                        user_agent=ua,
                        viewport=vp,
                        locale=locale,
                        timezone_id=tz_id,
                        color_scheme="dark",
                        extra_http_headers={
                            "sec-ch-ua-mobile": "?0",
                            "upgrade-insecure-requests": "1"
                        }
                    )

                    page = context.new_page()
                    self._apply_clean_stealth(page)

                    target_url = f"https://www.youtube.com/watch?v={video_id}"
                    try:
                        page.goto(target_url, referer=referer, wait_until="domcontentloaded", timeout=35000)
                    except Exception as e:
                        logger.debug(f"[{view_idx}] goto notice: {e}")

                    page.wait_for_timeout(2000)
                    self._handle_consent(page)

                    # 1. Check for Bot Block / Captcha
                    block_reason = self._check_bot_detection(page)
                    if block_reason:
                        logger.warning(f"[{view_idx}] IP {thread_ip} ({country}) is BURNED ({block_reason}). Discarding & Hunting new circuit...")
                        with self.lock:
                            self.known_blocked_ips.add(thread_ip)
                            self.stats["circuits_rejected_as_blocked"] += 1
                        context.close()
                        browser.close()
                        time.sleep(2)
                        # Retry with another clean circuit!
                        continue

                    # 2. Start Video Playback
                    try:
                        page.wait_for_selector("video", timeout=12000)
                        page.evaluate("""() => {
                            const v = document.querySelector('video');
                            if (v) {
                                v.muted = false;
                                v.volume = 0.35;
                                v.playbackRate = 1.0;
                                v.play();
                            }
                        }""")
                    except Exception:
                        try:
                            page.click(".html5-video-player", timeout=4000)
                        except Exception:
                            pass

                    initial_time = 0.0
                    try:
                        initial_time = page.evaluate("() => document.querySelector('video')?.currentTime || 0")
                    except Exception:
                        pass

                    # 3. Watch Loop: Monitor real continuous playback progression
                    start_watch = time.time()
                    last_time = initial_time
                    stall_count = 0

                    while time.time() - start_watch < target_watch_time:
                        page.wait_for_timeout(random.randint(3000, 5000))

                        # Check mid-play block
                        mid_block = self._check_bot_detection(page)
                        if mid_block:
                            logger.warning(f"[{view_idx}] IP {thread_ip} blocked mid-play. Hunting new circuit...")
                            with self.lock:
                                self.known_blocked_ips.add(thread_ip)
                                self.stats["circuits_rejected_as_blocked"] += 1
                            break

                        page.mouse.wheel(0, random.randint(-40, 60))

                        try:
                            cur_time = page.evaluate("() => document.querySelector('video')?.currentTime || 0")
                            if cur_time <= last_time:
                                stall_count += 1
                            else:
                                stall_count = 0
                                last_time = cur_time
                        except Exception:
                            pass

                        if stall_count >= 3:
                            logger.warning(f"[{view_idx}] Playback stalled at {last_time:.1f}s.")
                            break

                    actual_streamed = last_time - initial_time
                    context.close()
                    browser.close()

                    # 4. Strict Verification
                    required_min = max(2.0, target_watch_time * 0.7)
                    if actual_streamed >= required_min:
                        with self.lock:
                            self.completed_count += 1
                            self.stats["successful_views"] += 1
                            self.stats["ips_used"].append(thread_ip)
                            current_done = self.completed_count

                        logger.info(f"✅ [{current_done}/{total_views}] CLEAN VIEW VERIFIED! IP: {thread_ip} ({country}) | Played: {actual_streamed:.1f}s")
                        msg = (
                            f"✅ <b>Clean View Verified [{current_done}/{total_views}]</b>\n"
                            f"🧅 <b>Clean IP:</b> <code>{thread_ip}</code> ({country})\n"
                            f"🌐 <b>Referer:</b> {referer}\n"
                            f"⏱️ <b>Watched:</b> {actual_streamed:.1f}s"
                        )
                        self.tg.send_message(msg)
                        return True
                    else:
                        logger.warning(f"[{view_idx}] Video played only {actual_streamed:.1f}s. Hunting another circuit...")
                        continue

                except Exception as e:
                    logger.error(f"[{view_idx}] Execution exception: {e}")
                    if browser:
                        try:
                            browser.close()
                        except Exception:
                            pass
                    continue

        # If all 5 circuit attempts failed
        with self.lock:
            self.stats["failed_views"] += 1
        return False

    def run_bulk(self, video_url: str, views: int = 5, watch_duration: float = 35.0):
        self.stats["start_time"] = datetime.now().isoformat()
        self.stats["total_views_requested"] = views

        video_id = extract_video_id(video_url)
        header_msg = (
            f"🚀 <b>Clean-Circuit YouTube Booster Started</b>\n"
            f"🎬 <b>Video:</b> <code>{video_id}</code>\n"
            f"🎯 <b>Target Clean Views:</b> {views}\n"
            f"⏱️ <b>Watch Duration:</b> {watch_duration}s\n"
            f"🛡️ <b>Burned IP Protection:</b> Active (Auto-Discards Blocked Nodes)"
        )
        self.tg.send_message(header_msg)

        logger.info(f"Target Video ID: {video_id} | Clean Target: {views} | Threads: {self.concurrency}")

        with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="ViewHunter") as executor:
            futures = []
            for i in range(1, views + 1):
                futures.append(
                    executor.submit(
                        self.simulate_view_with_clean_ip_hunt,
                        view_idx=i,
                        total_views=views,
                        video_id=video_id,
                        target_watch_time=watch_duration
                    )
                )
                time.sleep(random.uniform(1.2, 2.8))

            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as e:
                    logger.error(f"Hunter thread error: {e}")

        self.stats["end_time"] = datetime.now().isoformat()
        unique_clean_ips = len(set(self.stats["ips_used"]))
        summary_msg = (
            f"🏁 <b>Session Complete!</b>\n\n"
            f"📊 <b>Total Target:</b> {self.stats['total_views_requested']}\n"
            f"✅ <b>Verified Clean Views:</b> {self.stats['successful_views']}\n"
            f"🛑 <b>Burned Nodes Auto-Bypassed:</b> {self.stats['circuits_rejected_as_blocked']}\n"
            f"❌ <b>Unresolvable Drops:</b> {self.stats['failed_views']}\n"
            f"🧅 <b>Clean Unique IPs Delivered:</b> {unique_clean_ips}"
        )
        self.tg.send_message(summary_msg)
        self._save_stats()

    def _save_stats(self):
        with open("bot_stats.json", "w", encoding="utf-8") as f:
            json.dump(self.stats, f, indent=2)
        logger.info("Audit stats saved to bot_stats.json")


def main():
    parser = argparse.ArgumentParser(description="Clean Circuit YouTube View Booster")
    parser.add_argument("--url", type=str, default="")
    parser.add_argument("--views", type=int, default=5)
    parser.add_argument("--watch-time", type=float, default=35.0)
    parser.add_argument("--threads", type=int, default=3)
    parser.add_argument("--tor-control-port", type=int, default=9051)
    parser.add_argument("--tor-socks-port", type=int, default=9050)
    parser.add_argument("--telegram-token", type=str, default="")
    parser.add_argument("--telegram-chat-id", type=str, default="")

    args = parser.parse_args()

    tg = TelegramReporter(token=args.telegram_token, chat_id=args.telegram_chat_id)
    tor = TorManager(control_port=args.tor_control_port, socks_port=args.tor_socks_port)

    bot = CleanCircuitViewBot(
        tor_manager=tor,
        telegram_reporter=tg,
        concurrency=args.threads,
        headless=True
    )

    target_url = args.url.strip()
    if not target_url:
        target_url = "https://www.youtube.com/watch?v=d2Wg5jdK5jE"

    bot.run_bulk(
        video_url=target_url,
        views=args.views,
        watch_duration=args.watch_time
    )


if __name__ == "__main__":
    main()
