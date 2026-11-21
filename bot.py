
import os
import re
import asyncio
import tempfile
from pathlib import Path

import yt_dlp
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ["BOT_TOKEN"]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! 👋 به ربات HANGIIAT Downloader خوش اومدی.\n\n"
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
            "لطفاً لینک معتبر یک پست یا ریلز عمومی اینستاگرام بفرست."
        )
        return

    message = await update.message.reply_text("⏳ در حال بررسی لینک...")

    try:
        with tempfile.TemporaryDirectory() as folder:
            path = await asyncio.to_thread(download_video, url, folder)

            if not path.exists():
                raise Exception("فایل پیدا نشد")

            if path.stat().st_size > 45 * 1024 * 1024:
                raise Exception("حجم فایل بیشتر از حد مجاز است")

            with path.open("rb") as video:
                await update.message.reply_video(
                    video=video,
                    caption="دانلود با HANGIIAT ❤️"
                )

        await message.delete()

    except Exception:
        await message.edit_text(
            "❌ دریافت این ویدئو ممکن نشد.\n"
            "ممکن است لینک در دسترس نباشد یا محدودیت داشته باشد."
        )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link)
    )
    app.run_polling()

if __name__ == "__main__":
    main()
