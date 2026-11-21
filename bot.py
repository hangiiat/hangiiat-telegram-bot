
import os
import re
import asyncio
import tempfile
import threading
import logging
from pathlib import Path

import requests
from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

TOKEN = os.environ["BOT_TOKEN"]
SAVEAPI_KEY = os.environ.get("SAVEAPI_KEY", "").strip()

API_BASE = "https://api.saveapi.org/v1"
MAX_SIZE = 45 * 1024 * 1024

web = Flask(__name__)


@web.get("/")
def home():
    return "HANGIIAT Bot is running!"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! 👋\n"
        "به HANGIIAT Downloader خوش اومدی.\n\n"
        "لینک پست، ریلز یا استوری عمومی اینستاگرام رو بفرست.\n"
        "عکس و ویدئو پشتیبانی می‌شن. ❤️"
    )


def resolve_media(url, is_story):
    endpoint = (
        f"{API_BASE}/instagram"
        if is_story
        else f"{API_BASE}/download"
    )

    response = requests.get(
        endpoint,
        headers={"Authorization": f"Bearer {SAVEAPI_KEY}"},
        params={"url": url},
        timeout=45
    )

    try:
        data = response.json()
    except ValueError:
        raise Exception("Invalid API response")

    if not response.ok or not data.get("success"):
        error = data.get("error", {})
        code = error.get("code", "UNKNOWN")
        message = error.get("message", "Download failed")
        raise Exception(f"{code}: {message}")

    medias = data.get("medias", [])

    if not medias:
        raise Exception("MEDIA_NOT_FOUND")

    return medias


def fetch_media(media, folder, index):
    media_url = media.get("url")

    if not media_url:
        raise Exception("Media URL missing")

    response = requests.get(
        media_url,
        stream=True,
        timeout=(15, 60)
    )
    response.raise_for_status()

    content_length = int(response.headers.get("Content-Length", 0))

    if content_length > MAX_SIZE:
        raise Exception("File too large")

    ext = str(media.get("ext") or "").lower()
    ext = re.sub(r"[^a-z0-9]", "", ext)

    if not ext:
        ext = "jpg" if media.get("type") == "image" else "mp4"

    path = Path(folder) / f"media_{index}.{ext}"
    total = 0

    with path.open("wb") as file:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue

            total += len(chunk)

            if total > MAX_SIZE:
                raise Exception("File too large")

            file.write(chunk)

    if total == 0:
        raise Exception("Empty file")

    return path


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    post_pattern = (
        r"^https?://(?:www\.)?instagram\.com/"
        r"(?:p|reel)/[^/?#]+/?(?:[?#].*)?$"
    )

    story_pattern = (
        r"^https?://(?:www\.)?instagram\.com/"
        r"stories/[^/?#]+(?:/\d+)?/?(?:[?#].*)?$"
    )

    is_post = re.match(post_pattern, url, re.IGNORECASE)
    is_story = re.match(story_pattern, url, re.IGNORECASE)

    if not is_post and not is_story:
        await update.message.reply_text(
            "لطفاً لینک معتبر پست، ریلز یا استوری عمومی اینستاگرام بفرست."
        )
        return

    if not SAVEAPI_KEY:
        await update.message.reply_text(
            "❌ کلید SaveAPI در تنظیمات سرور پیدا نشد."
        )
        return

    msg = await update.message.reply_text(
        "⏳ در حال دریافت استوری..." if is_story
        else "⏳ در حال دریافت محتوا..."
    )

    try:
        with tempfile.TemporaryDirectory() as folder:

            medias = await asyncio.to_thread(
                resolve_media, url, bool(is_story)
            )

            sent = 0
            failed = 0

            for index, media in enumerate(medias[:10]):
                try:
                    path = await asyncio.to_thread(
                        fetch_media, media, folder, index
                    )

                    with path.open("rb") as file:
                        media_type = str(
                            media.get("type", "")
                        ).lower()

                        ext = path.suffix.lower()

                        if media_type == "image" and path.stat().st_size <= 10 * 1024 * 1024:
                            await update.message.reply_photo(
                                photo=file,
                                caption="دانلود با HANGIIAT ❤️"
                            )

                        elif media_type == "video" and ext == ".mp4":
                            await update.message.reply_video(
                                video=file,
                                caption="دانلود با HANGIIAT ❤️"
                            )

                        else:
                            await update.message.reply_document(
                                document=file,
                                caption="دانلود با HANGIIAT ❤️"
                            )

                    sent += 1

                except Exception:
                    failed += 1
                    logging.exception("One media failed")

            if sent == 0:
                raise Exception("No media could be sent")

            await msg.delete()

            if len(medias) > 10:
                await update.message.reply_text(
                    "⚠️ فقط ۱۰ فایل اول ارسال شد."
                )

            if failed:
                await update.message.reply_text(
                    f"⚠️ {failed} فایل قابل ارسال نبود."
                )

    except Exception as error:
        logging.exception("Media download failed")

        error_text = str(error)

        if "PRIVATE_CONTENT" in error_text:
            reply = "❌ این پیج خصوصی است و قابل دانلود نیست."
        elif "MEDIA_NOT_FOUND" in error_text:
            reply = "❌ استوری فعال یا محتوای قابل دانلود پیدا نشد."
        elif "INVALID_API_KEY" in error_text or "MISSING_API_KEY" in error_text:
            reply = "❌ کلید SaveAPI معتبر نیست. تنظیمات سرور را بررسی کن."
        elif "QUOTA_EXCEEDED" in error_text:
            reply = "❌ اعتبار رایگان SaveAPI تمام شده است."
        else:
            reply = "❌ دریافت محتوا ممکن نشد. کمی بعد دوباره امتحان کن."

        await msg.edit_text(reply)


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_link
        )
    )

    threading.Thread(
        target=lambda: web.run(
            host="0.0.0.0",
            port=int(os.environ.get("PORT", 10000))
        ),
        daemon=True
    ).start()

    app.run_polling()


if __name__ == "__main__":
    main()
