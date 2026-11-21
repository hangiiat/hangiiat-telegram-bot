
import os
import re
import asyncio
import tempfile
import threading
import logging
from pathlib import Path

from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import yt_dlp

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

TOKEN = os.environ["BOT_TOKEN"]

web = Flask(__name__)

@web.get("/")
def home():
    return "HANGIIAT Bot is running!"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! 👋\n"
        "به HANGIIAT Downloader خوش اومدی.\n"
        "لینک پست، ریلز یا استوری عمومی اینستاگرام رو بفرست."
    )


def download_media(url, folder):
    options = {
        "format": "best[ext=mp4]/best",
        "outtmpl": str(Path(folder) / "%(id)s.%(ext)s"),
        "max_filesize": 45 * 1024 * 1024,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)

        if info.get("_type") == "playlist":
            raise Exception("Playlist is not supported")

        path = Path(ydl.prepare_filename(info))
        return path


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    post_pattern = (
        r"^https?://(www\.)?instagram\.com/(p|reel)/"
    )

    story_pattern = (
        r"^https?://(www\.)?instagram\.com/stories/[^/]+/\d+"
    )

    is_post = re.match(post_pattern, url)
    is_story = re.match(story_pattern, url)

    if not is_post and not is_story:
        await update.message.reply_text(
            "لطفاً لینک معتبر پست، ریلز یا استوری اینستاگرام بفرست."
        )
        return

    msg = await update.message.reply_text(
        "⏳ در حال دریافت استوری..." if is_story
        else "⏳ در حال دریافت محتوا..."
    )

    try:
        with tempfile.TemporaryDirectory() as folder:
            path = await asyncio.to_thread(
                download_media, url, folder
            )

            if not path.exists():
                raise Exception("File not found")

            if path.stat().st_size > 45 * 1024 * 1024:
                raise Exception("File too large")

            with path.open("rb") as media:
                ext = path.suffix.lower()

                if ext in (".jpg", ".jpeg", ".png", ".webp"):
                    await update.message.reply_photo(
                        photo=media,
                        caption="دانلود با HANGIIAT ❤️"
                    )
                elif ext in (".mp4", ".mov", ".m4v"):
                    await update.message.reply_video(
                        video=media,
                        caption="دانلود با HANGIIAT ❤️"
                    )
                else:
                    await update.message.reply_document(
                        document=media,
                        caption="دانلود با HANGIIAT ❤️"
                    )

        await msg.delete()

    except Exception:
        logging.exception("Media download failed")
        await msg.edit_text(
            "❌ دریافت محتوا ممکن نشد. "
            "ممکن است اینستاگرام دسترسی را محدود کرده باشد."
        )


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
