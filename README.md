# YouTube View Booster (GitHub Actions + Tor + Telegram Bot)

A fully automated, 100% cloud-based YouTube view booster designed to run entirely on **GitHub Actions** (Ubuntu runners), controlled via **Telegram Bot**, with **real headless Chromium browser automation (Playwright)** and **Tor IP rotation**.

---

## 🏗️ Architecture

```text
       📱 Telegram User (@ytbhikan_bot)
                     │
         [/boost <url> <views> <time>]
                     │
                     ▼
    🌐 Cloudflare Worker (Serverless Webhook)
                     │
       [GitHub API: workflow_dispatch]
                     │
                     ▼
       ⚙️ GitHub Actions (Ubuntu-latest)
       ┌─────────────────────────────────────┐
       │ 1. Tor Service Started              │
       │ 2. Tor ControlPort (SIGNAL NEWNYM)  │
       │    Rotates Public IP every view     │
       │ 3. Headless Chromium (Playwright)   │
       │    - Proxied via Tor SOCKS5         │
       │    - Bypasses Consent Dialogs       │
       │    - Plays Real Video Streams       │
       │    - Human Scroll Simulation        │
       │ 4. Verifies playback duration       │
       └──────────────────┬──────────────────┘
                          │
          [Live View Completion Updates]
                          │
                          ▼
            📱 Real-time Telegram Alerts!
```

---

## 📁 Repository Structure

```text
├── .github/
│   └── workflows/
│       └── view_bot.yml           # GitHub Actions workflow (Tor + Playwright)
├── cloudflare-worker/
│   ├── worker.js                  # 24/7 Serverless Telegram webhook
│   └── wrangler.toml              # Cloudflare Worker deployment config
├── telegram-bot/
│   └── bot.py                     # Optional standalone Python poller bot
├── data/
│   └── videos_config.csv          # Optional batch video list
├── core_bot.py                    # Main Playwright + Tor engine
├── requirements.txt               # Dependencies (playwright, stem, requests)
├── .gitignore
└── README.md
```

---

## 🚀 Setup & Deployment Guide

### Step 1: Push Code to Your GitHub Repository

1. Initialize git and push this codebase to `https://github.com/bindalbhikan/ytbot`:

```bash
git init
git add .
git commit -m "Initial commit: Playwright + Tor YouTube booster with Telegram trigger"
git branch -M main
git remote add origin https://github.com/bindalbhikan/ytbot.git
git push -u origin main
```

---

### Step 2: Configure GitHub Repository Secrets

Go to your repository on GitHub:
**`Settings`** ➔ **`Secrets and variables`** ➔ **`Actions`** ➔ **`New repository secret`**

Add the following secrets:

| Secret Name | Value | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `YOUR_BOT_TOKEN_FROM_BOTFATHER` | Your Telegram Bot API token |
| `TELEGRAM_CHAT_ID` | `Your Telegram Numeric Chat ID` | Can get it by messaging `@userinfobot` or `@myidbot` |

---

### Step 3: Create GitHub Personal Access Token (PAT)

The Telegram bot needs a GitHub PAT to trigger GitHub Actions workflows:

1. Go to: **GitHub Settings ➔ Developer Settings ➔ Personal access tokens ➔ Tokens (classic)**:
   [https://github.com/settings/tokens](https://github.com/settings/tokens)
2. Click **Generate new token (classic)**.
3. Note: `YT Bot Dispatcher`
4. Select Scopes:
   - ✅ **`repo`** (Full control of private repositories)
   - ✅ **`workflow`** (Update and trigger GitHub Action workflows)
5. Generate and copy the token (e.g., `ghp_xxxxxxxxxxxx`).

---

### Step 4: Deploy Cloudflare Worker (Serverless 24/7 Bot)

You can run your Telegram bot 24/7 for free using Cloudflare Workers:

1. Open [Cloudflare Dashboard](https://dash.cloudflare.com/) ➔ **Workers & Pages** ➔ **Create Application**.
2. Create a Worker named `ytbot-telegram-gateway`.
3. Click **Quick Edit**, paste the contents of [`cloudflare-worker/worker.js`](cloudflare-worker/worker.js), and click **Save and Deploy**.
4. Go to **Settings ➔ Variables and Secrets**, add:
   - `TELEGRAM_BOT_TOKEN` = `<Your Telegram Bot Token>`
   - `GITHUB_PAT` = `<Your GitHub PAT from Step 3>`
   - `GITHUB_REPO` = `bindalbhikan/ytbot`
5. Copy your Worker URL (e.g. `https://ytbot-telegram-gateway.yourname.workers.dev`).
6. Register the webhook with Telegram by opening this URL in your browser:
   ```text
   https://api.telegram.org/bot<YOUR_TELEGRAM_BOT_TOKEN>/setWebhook?url=https://ytbot-telegram-gateway.yourname.workers.dev
   ```
   *Expected response:* `{"ok":true,"result":true,"description":"Webhook was set"}`

---

## 🎮 Usage via Telegram

Open your bot: [**@ytbhikan_bot**](https://t.me/ytbhikan_bot)

### Commands:

1. **Start Bot:**
   ```text
   /start
   ```

2. **Boost Video:**
   ```text
   /boost <YouTube_URL> [views] [watch_seconds]
   ```

   **Examples:**
   ```text
   /boost https://www.youtube.com/watch?v=dQw4w9WgXcQ 5 35
   /boost https://youtu.be/jNQXAC9IVRw 10 40
   ```

3. **Status Check:**
   ```text
   /status
   ```

When triggered, the bot will notify you:
- When the job starts on GitHub Actions runner.
- After every view finishes with the rotated Tor public IP.
- When the complete boost run is finished with total stats.

---

## 🛠️ Manual Triggering from GitHub Actions UI

You can also run the bot directly from GitHub:
1. Go to your repo: `https://github.com/bindalbhikan/ytbot`
2. Click the **Actions** tab.
3. Select **YouTube View Booster** in the sidebar.
4. Click **Run workflow**, enter the Video URL, number of views, and watch time, and click **Run workflow**!
