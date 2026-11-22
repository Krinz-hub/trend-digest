# Trend digest bot

Sends AI / dev / GitHub / agent news to Telegram, with a summary, a Short hook and a Short/Long suggestion for each item. Runs free on GitHub Actions (3x a day).

## Setup (about 10 minutes)

1. **Telegram bot**: message @BotFather, send `/newbot`, copy the token.
2. **Chat ID**: send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `message.chat.id`.
3. **Gemini key (free)**: create one at https://aistudio.google.com/apikey
4. **GitHub**: create a new repo, push this folder to it.
5. In the repo go to Settings > Secrets and variables > Actions and add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `GEMINI_API_KEY`
6. Actions tab > "Trend digest" > Run workflow to test. After that it runs on its own.

## Tweaks

- Schedule: edit the `cron` line in `.github/workflows/digest.yml` (times are UTC).
- Items per digest: set `TOP_N` (default 6).
- Topics: edit `KEYWORDS` and `RSS_FEEDS` in `main.py`.
- X/Twitter is not included (no reliable free access).
