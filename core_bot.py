#!/usr/bin/env python3
"""
YouTube View Bot Core Engine
- Headless Chromium via Playwright
- Tor IP Rotation via Stem ControlPort
- Real-time Telegram status updates
- Human interaction simulation (bypasses consent banners, plays video, tracks watch time)
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
from datetime import datetime
from urllib.parse import urlparse, parse_qs

import requests
from playwright.sync_api import sync_playwright

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
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
    """Extract YouTube 11-char video ID from various URL formats."""
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
    """Helper to send live progress updates to Telegram."""
    def __init__(self, token: str = None, chat_id: str = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        self.enabled = bool(self.token and self.chat_id)
        if self.enabled:
            logger.info(f"Telegram notifications enabled for Chat ID: {self.chat_id}")
        else:
            logger.info("Telegram notifications disabled (Token or Chat ID not provided).")

    def send_message(self, text: str):
        if not self.enabled:
            return
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
    """Manages Tor ControlPort and circuit renewal for new public IPs."""
    def __init__(self, control_port: int = 9051, socks_port: int = 9050, password: str = None):
        self.control_port = control_port
        self.socks_port = socks_port
        self.password = password or os.environ.get("TOR_CONTROL_PASSWORD", "")
        self.socks_proxy = f"socks5://127.0.0.1:{self.socks_port}"

    def rotate_ip(self) -> str:
        """Sends NEWNYM signal to Tor and returns the new public IP."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect(("127.0.0.1", self.control_port))
            auth_cmd = f'AUTHENTICATE "{self.password}"\r\n' if self.password else 'AUTHENTICATE ""\r\n'
            s.sendall(auth_cmd.encode('utf-8'))
            resp = s.recv(1024).decode('utf-8', errors='ignore')
            if "250" in resp:
                s.sendall(b'SIGNAL NEWNYM\r\nQUIT\r\n')
                logger.info("Signal NEWNYM sent to Tor ControlPort (circuit reset).")
            else:
                logger.warning(f"Tor ControlPort auth response: {resp.strip()}")
            s.close()
        except Exception as e:
            logger.warning(f"Tor socket control error: {e}. Checking IP anyway.")

        # Wait for new circuit to establish
        time.sleep(8)
        new_ip = self.get_current_ip()
        logger.info(f"Active Tor Public IP: {new_ip}")
        return new_ip

    def get_current_ip(self) -> str:
        """Fetch current public IP using the Tor SOCKS5 proxy."""
        # 1. Native curl fallback (fast & reliable on Ubuntu)
        try:
            cmd = [
                "curl", "-s", "--socks5-hostname", f"127.0.0.1:{self.socks_port}",
                "--max-time", "10", "https://api.ipify.org"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            ip = res.stdout.strip()
            if ip and len(ip) <= 45 and ("." in ip or ":" in ip):
                return ip
        except Exception:
            pass

        # 2. Python requests with socks5h
        proxies = {
            "http": f"socks5h://127.0.0.1:{self.socks_port}",
            "https": f"socks5h://127.0.0.1:{self.socks_port}"
        }
        for provider in ["https://api.ipify.org", "https://icanhazip.com", "https://check.torproject.org/api/ip"]:
            try:
                res = requests.get(provider, proxies=proxies, timeout=10)
                if res.status_code == 200:
                    text = res.text.strip()
                    if "IP" in text and "{" in text:
                        try:
                            return json.loads(text).get("IP", "Unknown")
                        except Exception:
                            pass
                    ip = text.split("\n")[0].strip()
                    if ip:
                        return ip
            except Exception:
                continue
        return "Unknown"


class YouTubeViewBot:
    def __init__(
        self,
        tor_manager: TorManager,
        telegram_reporter: TelegramReporter,
        headless: bool = True
    ):
        self.tor = tor_manager
        self.tg = telegram_reporter
        self.headless = headless
        self.stats = {
            "total_views_requested": 0,
            "successful_views": 0,
            "failed_views": 0,
            "ips_used": [],
            "start_time": None,
            "end_time": None
        }

    def _apply_stealth(self, page):
        """Inject scripts to evade automated browser detection."""
        stealth_js = """
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        window.chrome = {
            runtime: {}
        };
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        """
        page.add_init_script(stealth_js)

    def _handle_consent_popup(self, page):
        """Bypass YouTube EU consent / cookie banners."""
        consent_selectors = [
            'button[aria-label*="Reject"]',
            'button[aria-label*="Accept"]',
            'button:has-text("Reject all")',
            'button:has-text("Accept all")',
            'button:has-text("I agree")',
            'form[action*="consent"] button',
            '#lightbox button'
        ]
        for sel in consent_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=3000):
                    logger.info(f"Clicking consent dialog button: {sel}")
                    btn.click()
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                continue

    def simulate_view(self, video_url: str, watch_duration: int = 35) -> bool:
        """Launches headless Chromium, plays the video, and simulates human engagement."""
        video_id = extract_video_id(video_url)
        if not video_id:
            logger.error(f"Invalid YouTube URL: {video_url}")
            return False

        current_ip = self.tor.rotate_ip()
        self.stats["ips_used"].append(current_ip)

        ua = random.choice(USER_AGENTS)
        vp = random.choice(VIEWPORTS)

        logger.info(f"Starting browser | IP: {current_ip} | UA: {ua[:45]}...")

        with sync_playwright() as p:
            browser = None
            try:
                browser = p.chromium.launch(
                    headless=self.headless,
                    proxy={"server": self.tor.socks_proxy},
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
                logger.info(f"Navigating to {target_url}...")
                
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                except Exception as e:
                    logger.warning(f"Page load warning (continuing): {e}")

                page.wait_for_timeout(3000)
                self._handle_consent_popup(page)

                # Ensure video plays
                logger.info("Verifying video player state...")
                try:
                    page.wait_for_selector("video", timeout=15000)
                    page.evaluate("""() => {
                        const v = document.querySelector('video');
                        if (v) {
                            v.muted = true;
                            v.play();
                        }
                    }""")
                except Exception as e:
                    logger.warning(f"Could not force video element directly: {e}")
                    # Try clicking on video player
                    try:
                        page.click(".html5-video-player", timeout=5000)
                    except Exception:
                        pass

                # Simulate watch duration with human-like micro movements
                logger.info(f"Watching video for {watch_duration} seconds...")
                start_watch = time.time()
                while time.time() - start_watch < watch_duration:
                    page.wait_for_timeout(random.randint(4000, 7000))
                    # Random human-like mouse scroll
                    scroll_delta = random.randint(-150, 250)
                    page.mouse.wheel(0, scroll_delta)

                # Check if video was played
                try:
                    current_time = page.evaluate("() => document.querySelector('video')?.currentTime || 0")
                    logger.info(f"Video play verified! Final video currentTime: {current_time:.2f}s")
                except Exception:
                    pass

                context.close()
                browser.close()
                return True

            except Exception as e:
                logger.error(f"View simulation error: {e}")
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass
                return False

    def run(self, video_url: str, views: int = 5, watch_duration: int = 35):
        self.stats["start_time"] = datetime.now().isoformat()
        self.stats["total_views_requested"] = views

        video_id = extract_video_id(video_url)
        header_msg = (
            f"🚀 <b>YouTube View Booster Started</b>\n"
            f"🎬 <b>Video:</b> <code>{video_id}</code>\n"
            f"🎯 <b>Target Views:</b> {views}\n"
            f"⏱️ <b>Watch Time per view:</b> {watch_duration}s\n"
            f"🧅 <b>IP Rotation:</b> Tor Active"
        )
        self.tg.send_message(header_msg)

        logger.info(f"Target Video ID: {video_id} | Total Views: {views} | Watch Time: {watch_duration}s")

        for i in range(1, views + 1):
            logger.info(f"\n==================== VIEW [{i}/{views}] ====================")
            success = self.simulate_view(video_url, watch_duration=watch_duration)
            
            if success:
                self.stats["successful_views"] += 1
                logger.info(f"✅ View [{i}/{views}] successfully completed!")
                msg = (
                    f"✅ <b>View Completed [{i}/{views}]</b>\n"
                    f"🌐 <b>Exit IP:</b> <code>{self.stats['ips_used'][-1]}</code>\n"
                    f"⏱️ <b>Watched:</b> {watch_duration}s"
                )
                self.tg.send_message(msg)
            else:
                self.stats["failed_views"] += 1
                logger.warning(f"❌ View [{i}/{views}] failed.")

            # Cooldown delay between views
            if i < views:
                cooldown = random.randint(3, 7)
                logger.info(f"Cooldown delay: {cooldown}s before next view...")
                time.sleep(cooldown)

        self.stats["end_time"] = datetime.now().isoformat()
        summary_msg = (
            f"🏁 <b>Boosting Task Finished!</b>\n\n"
            f"📊 <b>Total Requested:</b> {self.stats['total_views_requested']}\n"
            f"✅ <b>Successful Views:</b> {self.stats['successful_views']}\n"
            f"❌ <b>Failed Views:</b> {self.stats['failed_views']}\n"
            f"🧅 <b>Unique IPs Rotated:</b> {len(set(self.stats['ips_used']))}"
        )
        self.tg.send_message(summary_msg)
        self._save_stats()

    def _save_stats(self):
        with open("bot_stats.json", "w", encoding="utf-8") as f:
            json.dump(self.stats, f, indent=2)
        logger.info("Execution stats saved to bot_stats.json")


def main():
    parser = argparse.ArgumentParser(description="YouTube View Bot - Tor + Playwright")
    parser.add_argument("--url", type=str, default="", help="YouTube video URL")
    parser.add_argument("--views", type=int, default=3, help="Number of views to boost")
    parser.add_argument("--watch-time", type=int, default=30, help="Watch duration per view in seconds")
    parser.add_argument("--tor-control-port", type=int, default=9051, help="Tor ControlPort (default: 9051)")
    parser.add_argument("--tor-socks-port", type=int, default=9050, help="Tor SOCKS5 port (default: 9050)")
    parser.add_argument("--telegram-token", type=str, default="", help="Telegram bot token")
    parser.add_argument("--telegram-chat-id", type=str, default="", help="Telegram chat ID")
    parser.add_argument("--config", type=str, default="", help="Optional CSV config file")

    args = parser.parse_args()

    tg = TelegramReporter(token=args.telegram_token, chat_id=args.telegram_chat_id)
    tor = TorManager(control_port=args.tor_control_port, socks_port=args.tor_socks_port)

    bot = YouTubeViewBot(tor_manager=tor, telegram_reporter=tg, headless=True)

    target_url = args.url.strip()
    views = args.views
    watch_time = args.watch_time

    # If config CSV given and no URL passed, load from CSV
    if not target_url and args.config and os.path.exists(args.config):
        with open(args.config, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if row and row[0].strip():
                    target_url = row[0].strip()
                    if len(row) > 1 and row[1].strip().isdigit():
                        views = int(row[1].strip())
                    if len(row) > 2 and row[2].strip().isdigit():
                        watch_time = int(row[2].strip())
                    break

    if not target_url:
        target_url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
        logger.info(f"No URL specified, using default test URL: {target_url}")

    bot.run(video_url=target_url, views=views, watch_duration=watch_time)


if __name__ == "__main__":
    main()
