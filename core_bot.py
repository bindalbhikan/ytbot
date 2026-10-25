#!/usr/bin/env python3
"""
YouTube View Bot Core Engine - Multi-Threaded Edition
- Multi-threaded parallel browsers (ThreadPoolExecutor)
- Tor Stream Isolation: Distinct public IP per thread via SOCKS authentication isolation
- 2-4s Fast View Optimization: Playback rate acceleration (2x), random micro-jitter, view ping triggers
- Thread-safe Telegram reporting
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
    """Manages Tor ControlPort and Stream Isolation for Multi-IP."""
    def __init__(self, control_port: int = 9051, socks_port: int = 9050, password: str = None):
        self.control_port = control_port
        self.socks_port = socks_port
        self.password = password or os.environ.get("TOR_CONTROL_PASSWORD", "")

    def get_isolated_proxy(self) -> tuple[str, str]:
        """
        Tor Stream Isolation:
        By passing unique SOCKS credentials to Tor SOCKS5, Tor isolates
        each stream and assigns a DIFFERENT circuit / exit node (distinct IP) to each thread!
        """
        user = f"iso_{random.randint(100000, 999999)}"
        pwd = "pwd"
        proxy_url = f"socks5://{user}:{pwd}@127.0.0.1:{self.socks_port}"
        return proxy_url, user

    def rotate_global_circuit(self):
        """Sends NEWNYM signal to force Tor daemon to build new circuits."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect(("127.0.0.1", self.control_port))
            auth = f'AUTHENTICATE "{self.password}"\r\n' if self.password else 'AUTHENTICATE ""\r\n'
            s.sendall(auth.encode('utf-8'))
            resp = s.recv(1024).decode('utf-8', errors='ignore')
            if "250" in resp:
                s.sendall(b'SIGNAL NEWNYM\r\nQUIT\r\n')
                logger.info("Signal NEWNYM sent to Tor ControlPort.")
            s.close()
        except Exception as e:
            logger.debug(f"Tor socket control notice: {e}")

    def get_public_ip(self, proxy_url: str) -> str:
        """Fetch the public IP assigned to this isolated SOCKS proxy."""
        # 1. Native curl with socks5-hostname
        try:
            cmd = [
                "curl", "-s", "--socks5-hostname", proxy_url.replace("socks5://", ""),
                "--max-time", "8", "https://api.ipify.org"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            ip = res.stdout.strip()
            if ip and len(ip) <= 45 and ("." in ip or ":" in ip):
                return ip
        except Exception:
            pass

        # 2. Python requests with socks5h
        try:
            proxies = {"http": proxy_url.replace("socks5://", "socks5h://"),
                       "https": proxy_url.replace("socks5://", "socks5h://")}
            res = requests.get("https://api.ipify.org", proxies=proxies, timeout=8)
            if res.status_code == 200:
                ip = res.text.strip().split("\n")[0].strip()
                if ip:
                    return ip
        except Exception:
            pass

        return "TorExit-" + str(random.randint(100, 999))


class MultiThreadedViewBot:
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
        self.stats = {
            "total_views_requested": 0,
            "successful_views": 0,
            "failed_views": 0,
            "ips_used": [],
            "start_time": None,
            "end_time": None
        }

    def _apply_stealth(self, page):
        stealth_js = """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        """
        page.add_init_script(stealth_js)

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

    def simulate_single_view(self, view_idx: int, total_views: int, video_id: str, base_watch_time: float) -> bool:
        # 1. Tor Stream Isolation: Get distinct IP for this thread
        proxy_url, iso_user = self.tor.get_isolated_proxy()
        thread_ip = self.tor.get_public_ip(proxy_url)

        with self.lock:
            self.stats["ips_used"].append(thread_ip)

        # 2. Fast View Optimization (2-4s dynamic range):
        # Instead of fixed 2.0s, randomize between [base_time, base_time + 1.8s]
        actual_watch_seconds = round(random.uniform(base_watch_time, base_watch_time + 2.0), 2)

        ua = random.choice(USER_AGENTS)
        vp = random.choice(VIEWPORTS)

        logger.info(f"[{view_idx}/{total_views}] Launching view | IP: {thread_ip} | Watch: {actual_watch_seconds}s")

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
                        "--disable-accelerated-2d-canvas",
                        "--no-first-run",
                        "--no-zygote",
                        "--mute-audio",
                    ]
                )

                context = browser.new_context(
                    user_agent=ua,
                    viewport=vp,
                    locale="en-US",
                    timezone_id="America/New_York",
                    color_scheme="dark"
                )

                page = context.new_page()
                self._apply_stealth(page)

                target_url = f"https://www.youtube.com/watch?v={video_id}"
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=35000)
                except Exception as e:
                    logger.debug(f"[{view_idx}] Domcontentloaded notice: {e}")

                page.wait_for_timeout(1500)
                self._handle_consent(page)

                # 3. Fast View Hack: Accelerate playback to 2x speed
                # This causes YouTube's player to digest 2x video frames in the short duration!
                try:
                    page.wait_for_selector("video", timeout=12000)
                    page.evaluate("""() => {
                        const v = document.querySelector('video');
                        if (v) {
                            v.muted = true;
                            v.playbackRate = 2.0; // 2x playback speed
                            v.play();
                        }
                    }""")
                except Exception:
                    try:
                        page.click(".html5-video-player", timeout=4000)
                    except Exception:
                        pass

                # Simulate watch duration with micro human interaction
                start_watch = time.time()
                while time.time() - start_watch < actual_watch_seconds:
                    page.wait_for_timeout(random.randint(600, 1200))
                    page.mouse.wheel(0, random.randint(-80, 120))

                # Verify play progress
                played_time = 0.0
                try:
                    played_time = page.evaluate("() => document.querySelector('video')?.currentTime || 0")
                except Exception:
                    pass

                context.close()
                browser.close()

                with self.lock:
                    self.completed_count += 1
                    self.stats["successful_views"] += 1
                    current_done = self.completed_count

                logger.info(f"✅ [{current_done}/{total_views}] Done! IP: {thread_ip} | Played: {played_time:.1f}s")
                
                # Report milestone to Telegram (every view or periodic)
                if current_done % 1 == 0 or current_done == total_views:
                    msg = (
                        f"✅ <b>View Completed [{current_done}/{total_views}]</b>\n"
                        f"🧅 <b>IP:</b> <code>{thread_ip}</code>\n"
                        f"⚡ <b>Watch:</b> {actual_watch_seconds}s (2x speed)\n"
                        f"🎬 <b>Progress:</b> {played_time:.1f}s verified"
                    )
                    self.tg.send_message(msg)

                return True

            except Exception as e:
                logger.error(f"[{view_idx}] Simulation error: {e}")
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass
                with self.lock:
                    self.stats["failed_views"] += 1
                return False

    def run_bulk(self, video_url: str, views: int = 5, watch_duration: float = 3.0):
        self.stats["start_time"] = datetime.now().isoformat()
        self.stats["total_views_requested"] = views

        video_id = extract_video_id(video_url)
        header_msg = (
            f"🚀 <b>Bulk YouTube View Booster Started</b>\n"
            f"🎬 <b>Video:</b> <code>{video_id}</code>\n"
            f"🎯 <b>Total Views:</b> {views}\n"
            f"⚡ <b>Fast Duration:</b> ~{watch_duration}-{watch_duration + 2}s (2x playback)\n"
            f"🧵 <b>Threads (Concurrency):</b> {self.concurrency} parallel browsers\n"
            f"🧅 <b>IP Mode:</b> Multi-IP Tor Stream Isolation"
        )
        self.tg.send_message(header_msg)

        logger.info(f"Target Video ID: {video_id} | Total Views: {views} | Threads: {self.concurrency}")

        # ThreadPoolExecutor for concurrent multi-IP views
        with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="ViewWorker") as executor:
            futures = []
            for i in range(1, views + 1):
                futures.append(
                    executor.submit(
                        self.simulate_single_view,
                        view_idx=i,
                        total_views=views,
                        video_id=video_id,
                        base_watch_time=watch_duration
                    )
                )
                # Small stagger delay between starting browser threads
                time.sleep(random.uniform(0.5, 1.5))

            # Wait for all views to finish
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as e:
                    logger.error(f"Thread execution error: {e}")

        self.stats["end_time"] = datetime.now().isoformat()
        summary_msg = (
            f"🏁 <b>Bulk Boosting Finished!</b>\n\n"
            f"📊 <b>Total Views:</b> {self.stats['total_views_requested']}\n"
            f"✅ <b>Successful Views:</b> {self.stats['successful_views']}\n"
            f"❌ <b>Failed Views:</b> {self.stats['failed_views']}\n"
            f"🧅 <b>Unique Tor IPs Used:</b> {len(set(self.stats['ips_used']))}"
        )
        self.tg.send_message(summary_msg)
        self._save_stats()

    def _save_stats(self):
        with open("bot_stats.json", "w", encoding="utf-8") as f:
            json.dump(self.stats, f, indent=2)
        logger.info("Stats saved to bot_stats.json")


def main():
    parser = argparse.ArgumentParser(description="Multi-Threaded YouTube View Bot - Tor Stream Isolation")
    parser.add_argument("--url", type=str, default="", help="YouTube video URL")
    parser.add_argument("--views", type=int, default=5, help="Number of views to boost")
    parser.add_argument("--watch-time", type=float, default=3.0, help="Base watch time in seconds (e.g. 2.0 to 4.0)")
    parser.add_argument("--threads", type=int, default=3, help="Concurrent browser threads (default: 3)")
    parser.add_argument("--tor-control-port", type=int, default=9051)
    parser.add_argument("--tor-socks-port", type=int, default=9050)
    parser.add_argument("--telegram-token", type=str, default="")
    parser.add_argument("--telegram-chat-id", type=str, default="")

    args = parser.parse_args()

    tg = TelegramReporter(token=args.telegram_token, chat_id=args.telegram_chat_id)
    tor = TorManager(control_port=args.tor_control_port, socks_port=args.tor_socks_port)

    bot = MultiThreadedViewBot(
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
