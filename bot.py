
import os
import re
import asyncio
import tempfile
import threading
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

TOKEN = os.environ["BOT_TOKEN"]

web = Flask(__name__)

@web.get("/")
def home():
    return "HANGIIAT Bot is running!"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! 👋\n"
        "به HANGIIAT Downloader خوش اومدی.\n"
        "لینک پست یا ریلز عمومی اینستاگرام رو بفرست."
    )

def download_video(url, folder):
    options = {
        "format": "best[ext=mp4]/best",
        "outtmpl": str(Path(folder) / "video.%(ext)s"),
        "max_filesize": 45 * 1024 * 1024,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info))

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not re.match(
        r"^https?://(www\.)?instagram\.com/(p|reel)/",
        url
    ):
        await update.message.reply_text(
            "لطفاً لینک معتبر یک پست یا ریلز عمومی بفرست."
        )
        return

    msg = await update.message.reply_text("⏳ در حال بررسی لینک...")

    try:
        with tempfile.TemporaryDirectory() as folder:
            path = await asyncio.to_thread(download_video, url, folder)

            if not path.exists():
                raise Exception("فایل پیدا نشد")

            if path.stat().st_size > 45 * 1024 * 1024:
                raise Exception("حجم فایل زیاد است")

            with path.open("rb") as video:
                await update.message.reply_video(
                    video=video,
                    caption="دانلود با HANGIIAT ❤️"
                )

        await msg.delete()

    except Exception:
        await msg.edit_text(
            "❌ دریافت این محتوا ممکن نشد. "
            "ممکن است لینک در دسترس نباشد یا محدودیت داشته باشد."
        )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link)
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
