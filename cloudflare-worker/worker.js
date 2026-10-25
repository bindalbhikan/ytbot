/**
 * Cloudflare Worker: Serverless Telegram Bot Gateway for YouTube View Booster
 * Receives commands from Telegram (@ytbhikan_bot) and dispatches GitHub Actions workflows!
 *
 * Environment variables required in Cloudflare Worker:
 * - TELEGRAM_BOT_TOKEN: Telegram bot API token
 * - GITHUB_PAT: GitHub Personal Access Token with 'repo' or 'actions:write' scope
 * - GITHUB_REPO: "bindalbhikan/ytbot"
 */

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("Telegram Webhook active. Method should be POST.", { status: 200 });
    }

    try {
      const update = await request.json();
      if (!update.message || !update.message.text) {
        return new Response("OK", { status: 200 });
      }

      const chatId = update.message.chat.id;
      const text = update.message.text.trim();
      const botToken = env.TELEGRAM_BOT_TOKEN;
      const githubPat = env.GITHUB_PAT;
      const githubRepo = env.GITHUB_REPO || "bindalbhikan/ytbot";

      // Command: /start or /help
      if (text.startsWith("/start") || text.startsWith("/help")) {
        const welcomeText = 
          `👋 <b>Welcome to YouTube View Booster Bot!</b>\n\n` +
          `I trigger cloud-based automated views via <b>GitHub Actions</b> using headless Chromium and Tor IP rotation.\n\n` +
          `<b>Available Commands:</b>\n` +
          `🔹 <code>/boost &lt;video_url&gt; [views] [watch_seconds]</code>\n` +
          `🔹 <code>/status</code> - Show workflow info\n\n` +
          `<b>Example:</b>\n` +
          `<code>/boost https://www.youtube.com/watch?v=dQw4w9WgXcQ 5 35</code>\n\n` +
          `<i>Note: Default views = 5, default watch time = 35s.</i>`;

        await sendTelegramMessage(botToken, chatId, welcomeText);
        return new Response("OK", { status: 200 });
      }

      // Command: /boost
      if (text.startsWith("/boost")) {
        const parts = text.split(/\s+/);
        if (parts.length < 2) {
          await sendTelegramMessage(
            botToken,
            chatId,
            "⚠️ <b>Usage:</b>\n<code>/boost &lt;YouTube_URL&gt; [views] [watch_time_seconds]</code>\n\n<b>Example:</b>\n<code>/boost https://youtu.be/dQw4w9WgXcQ 10 35</code>"
          );
          return new Response("OK", { status: 200 });
        }

        const videoUrl = parts[1];
        const views = parts[2] && !isNaN(parts[2]) ? parts[2] : "5";
        const watchTime = parts[3] && !isNaN(parts[3]) ? parts[3] : "35";

        if (!videoUrl.includes("youtu")) {
          await sendTelegramMessage(botToken, chatId, "❌ <b>Error:</b> Invalid YouTube URL provided.");
          return new Response("OK", { status: 200 });
        }

        if (!githubPat) {
          await sendTelegramMessage(
            botToken,
            chatId,
            "⚠️ <b>Worker Configuration Missing:</b> GITHUB_PAT is not configured in Cloudflare Worker environment variables."
          );
          return new Response("OK", { status: 200 });
        }

        // Notify user dispatching
        await sendTelegramMessage(
          botToken,
          chatId,
          `⏳ <b>Dispatching to GitHub Actions...</b>\n\n🎬 <b>URL:</b> ${videoUrl}\n🎯 <b>Views:</b> ${views}\n⏱️ <b>Watch Time:</b> ${watchTime}s`
        );

        // Dispatch workflow via GitHub REST API
        const ghUrl = `https://api.github.com/repos/${githubRepo}/actions/workflows/view_bot.yml/dispatches`;
        const ghResponse = await fetch(ghUrl, {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${githubPat}`,
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Cloudflare-Worker-TelegramBot"
          },
          body: JSON.stringify({
            ref: "main",
            inputs: {
              video_url: videoUrl,
              views: views.toString(),
              watch_time: watchTime.toString(),
              telegram_chat_id: chatId.toString()
            }
          })
        });

        if (ghResponse.status === 204) {
          await sendTelegramMessage(
            botToken,
            chatId,
            `🚀 <b>GitHub Actions Workflow Started!</b>\n\n` +
            `Runner: <b>Ubuntu (Headless Chrome + Tor)</b>\n` +
            `Repository: <code>${githubRepo}</code>\n\n` +
            `<i>You will receive live view completion updates in this chat!</i>`
          );
        } else {
          const errText = await ghResponse.text();
          await sendTelegramMessage(
            botToken,
            chatId,
            `❌ <b>GitHub Dispatch Error (${ghResponse.status}):</b>\n<code>${errText.slice(0, 300)}</code>`
          );
        }

        return new Response("OK", { status: 200 });
      }

      // Command: /status
      if (text.startsWith("/status")) {
        await sendTelegramMessage(
          botToken,
          chatId,
          `ℹ️ <b>Bot Status:</b> Online\n<b>Target Repo:</b> <code>${githubRepo}</code>\n<b>Check Runs:</b> https://github.com/${githubRepo}/actions`
        );
        return new Response("OK", { status: 200 });
      }

      return new Response("OK", { status: 200 });
    } catch (err) {
      return new Response(`Error: ${err.message}`, { status: 500 });
    }
  }
};

async function sendTelegramMessage(token, chatId, text) {
  const url = `https://api.telegram.org/bot${token}/sendMessage`;
  await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text: text,
      parse_mode: "HTML",
      disable_web_page_preview: true
    })
  });
}
