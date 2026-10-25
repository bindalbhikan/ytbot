#!/usr/bin/env python3
"""
Standalone Python Telegram Bot Poller for YouTube View Booster
Listens for /boost commands and triggers GitHub Actions workflow.
"""

import os
import sys
import time
import requests
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger("telegram_bot_poller")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
GITHUB_PAT = os.environ.get("GITHUB_PAT", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "bindalbhikan/ytbot").strip()

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_message(chat_id: int, text: str):
    try:
        requests.post(
            f"{API_URL}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=10
        )
    except Exception as e:
        logger.error(f"Error sending message: {e}")


def dispatch_github_workflow(video_url: str, views: int, watch_time: int, chat_id: int) -> bool:
    if not GITHUB_PAT:
        send_message(
            chat_id,
            "⚠️ <b>Error:</b> <code>GITHUB_PAT</code> environment variable is missing on the bot server.\n"
            "Please export your GitHub Personal Access Token."
        )
        return False

    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/view_bot.yml/dispatches"
    headers = {
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Telegram-Bot-Dispatch"
    }
    payload = {
        "ref": "main",
        "inputs": {
            "video_url": video_url,
            "views": str(views),
            "watch_time": str(watch_time),
            "telegram_chat_id": str(chat_id)
        }
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code == 204:
            return True
        else:
            logger.error(f"GitHub dispatch failed ({resp.status_code}): {resp.text}")
            send_message(
                chat_id,
                f"❌ <b>GitHub Actions Dispatch Error ({resp.status_code}):</b>\n<code>{resp.text[:250]}</code>"
            )
            return False
    except Exception as e:
        logger.error(f"Exception during GitHub dispatch: {e}")
        send_message(chat_id, f"❌ <b>Connection Error:</b> {e}")
        return False


def handle_update(update: dict):
    message = update.get("message")
    if not message or "text" not in message:
        return

    chat_id = message["chat"]["id"]
    text = message["text"].strip()

    if text.startswith("/start") or text.startswith("/help"):
        welcome = (
            "👋 <b>Welcome to YouTube View Booster Bot (Multi-Threaded)!</b>\n\n"
            "I trigger automated runs on <b>GitHub Actions</b> with parallel headless Chromium + Tor Stream Isolation (Multi-IP).\n\n"
            "<b>Commands:</b>\n"
            "🔹 <code>/boost &lt;video_url&gt; [views] [watch_seconds] [threads]</code>\n"
            "🔹 <code>/status</code> - Show status\n\n"
            "<b>Examples:</b>\n"
            "⚡ Fast View (3-5s): <code>/boost https://youtu.be/d2Wg5jdK5jE 50 3 3</code>\n"
            "🎯 Regular View (30s): <code>/boost https://youtu.be/d2Wg5jdK5jE 20 30 3</code>\n\n"
            "<i>Defaults: views = 10, watch time = 3s, threads = 3 parallel browsers.</i>"
        )
        send_message(chat_id, welcome)
        return

    if text.startswith("/boost"):
        parts = text.split()
        if len(parts) < 2:
            send_message(
                chat_id,
                "⚠️ <b>Usage:</b>\n<code>/boost &lt;YouTube_URL&gt; [views] [watch_time]</code>\n\n"
                "<b>Example:</b>\n<code>/boost https://youtu.be/dQw4w9WgXcQ 10 35</code>"
            )
            return

        video_url = parts[1]
        views = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 5
        watch_time = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 35

        send_message(
            chat_id,
            f"⏳ <b>Dispatching job to GitHub Actions...</b>\n\n"
            f"🎬 <b>Target:</b> {video_url}\n"
            f"🎯 <b>Views:</b> {views}\n"
            f"⏱️ <b>Watch Time:</b> {watch_time}s"
        )

        ok = dispatch_github_workflow(video_url, views, watch_time, chat_id)
        if ok:
            send_message(
                chat_id,
                f"🚀 <b>Workflow Started on GitHub Actions!</b>\n"
                f"Repository: <code>{GITHUB_REPO}</code>\n"
                f"Live updates will appear here."
            )
        return

    if text.startswith("/status"):
        send_message(
            chat_id,
            f"ℹ️ <b>Bot Status:</b> Active\n<b>Target Repo:</b> {GITHUB_REPO}\n"
            f"Workflow Dashboard: https://github.com/{GITHUB_REPO}/actions"
        )


def main():
    logger.info("Starting Telegram Bot Poller...")
    offset = None
    while True:
        try:
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset

            resp = requests.get(f"{API_URL}/getUpdates", params=params, timeout=40)
            if resp.status_code == 200:
                data = resp.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    handle_update(update)
            else:
                time.sleep(2)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            break
        except Exception as e:
            logger.error(f"Polling loop error: {e}")
            time.sleep(3)


if __name__ == "__main__":
    main()
