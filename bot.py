
import os
import re
import asyncio
import tempfile
import threading
import logging
import hashlib
from pathlib import Path

import requests
import yt_dlp
import imageio_ffmpeg

from flask import Flask
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
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
FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()

web = Flask(__name__)


@web.get("/")
def home():
    return "HANGIIAT Bot is running!"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! 👋\n"
        "به HANGIIAT Downloader خوش اومدی.\n\n"
        "📸 Instagram\n"
        "📌 Pinterest\n"
        "▶️ YouTube\n\n"
        "دانلود عکس، ویدئو و صدای MP3 ❤️"
    )


def split_text(text, limit=4000):
    parts = []

    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit

        parts.append(text[:cut].strip())
        text = text[cut:].strip()

    if text:
        parts.append(text)

    return parts


# ---------------- INSTAGRAM ----------------

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

    if not data.get("medias"):
        raise Exception("MEDIA_NOT_FOUND")

    return data


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


def file_hash(path):
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


async def handle_instagram(update, url, is_story, is_reel, msg):
    with tempfile.TemporaryDirectory() as folder:
        api_data = await asyncio.to_thread(
            resolve_media, url, is_story
        )

        medias = api_data.get("medias", [])
        sent = 0
        failed = 0
        seen_hashes = set()

        for index, media in enumerate(medias[:10]):
            media_type = str(media.get("type", "")).lower()

            if is_reel and media_type != "video":
                continue

            try:
                path = await asyncio.to_thread(
                    fetch_media, media, folder, index
                )

                digest = await asyncio.to_thread(file_hash, path)

                if digest in seen_hashes:
                    continue

                seen_hashes.add(digest)

                with path.open("rb") as file:
                    ext = path.suffix.lower()

                    if (
                        media_type == "image"
                        and path.stat().st_size <= 10 * 1024 * 1024
                    ):
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

                if is_story or is_reel:
                    break

            except Exception:
                failed += 1
                logging.exception("Instagram media failed")

        if sent == 0:
            raise Exception("No media could be sent")

        if is_reel:
            meta = api_data.get("meta") or {}

            caption_text = (
                meta.get("text")
                or meta.get("title")
                or ""
            ).strip()

            if caption_text:
                for part in split_text(caption_text):
                    await update.message.reply_text(part)

        await msg.delete()

        if len(medias) > 10 and not (is_story or is_reel):
            await update.message.reply_text(
                "⚠️ فقط ۱۰ فایل اول ارسال شد."
            )

        if failed:
            await update.message.reply_text(
                f"⚠️ {failed} فایل قابل ارسال نبود."
            )


# ---------------- EXTERNAL MEDIA ----------------

def extract_formats(url, platform):
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 25,
        "extract_flat": False,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

    formats = info.get("formats") or []
    choices = {}

    for item in formats:
        format_id = item.get("format_id")

        if not format_id:
            continue

        ext = str(item.get("ext") or "").lower()

        if ext not in (
            "mp4", "webm", "m4v", "jpg",
            "jpeg", "png", "webp"
        ):
            continue

        height = item.get("height")
        vcodec = item.get("vcodec")
        filesize = (
            item.get("filesize")
            or item.get("filesize_approx")
            or 0
        )

        # فایل‌های ویدئویی یا تصویری
        is_image = ext in ("jpg", "jpeg", "png", "webp")
        is_video = vcodec not in (None, "none")

        if not is_image and not is_video:
            continue

        if filesize and filesize > MAX_SIZE:
            continue

        label = (
            f"{height}p"
            if height
            else ("تصویر اصلی" if is_image else "کیفیت اصلی")
        )

        quality = int(height or 0)

        # برای هر کیفیت فقط یک فرمت نگه داشته می‌شود
        score = (
            1 if ext == "mp4" else 0,
            1 if item.get("acodec") not in (None, "none") else 0,
            int(filesize or 0),
        )

        old = choices.get(label)

        if old is None or score > old["score"]:
            choices[label] = {
                "format_id": format_id,
                "label": label,
                "height": quality,
                "is_image": is_image,
                "score": score,
            }

    result = sorted(
        choices.values(),
        key=lambda x: x["height"],
        reverse=True
    )

    if not result:
        result = [{
            "format_id": "best",
            "label": "بهترین کیفیت موجود",
            "height": 0,
            "is_image": False,
            "score": (0, 0, 0),
        }]

    return info.get("title", "HANGIIAT"), result


def build_keyboard(choices, platform):
    buttons = []

    for index, choice in enumerate(choices):
        buttons.append(
            InlineKeyboardButton(
                f"🎬 {choice['label']}",
                callback_data=f"quality:{index}"
            )
        )

    if platform == "youtube":
        buttons.append(
            InlineKeyboardButton(
                "🎵 دریافت MP3",
                callback_data="quality:audio"
            )
        )

    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]

    return InlineKeyboardMarkup(rows)


async def prepare_external(update, context, url, platform):
    msg = await update.message.reply_text(
        "⏳ در حال بررسی کیفیت‌های موجود..."
    )

    try:
        title, choices = await asyncio.to_thread(
            extract_formats, url, platform
        )

        context.user_data["pending_download"] = {
            "url": url,
            "platform": platform,
            "choices": choices,
            "title": title,
        }

        keyboard = build_keyboard(choices, platform)

        await msg.edit_text(
            f"📥 {platform.upper()}\n\n"
            f"🎞 {title[:180]}\n\n"
            "کیفیت مورد نظرت رو انتخاب کن:",
            reply_markup=keyboard
        )

    except Exception:
        logging.exception("Format extraction failed")

        await msg.edit_text(
            "❌ نتونستم کیفیت‌های این لینک رو دریافت کنم.\n"
            "ممکنه لینک محدود یا نامعتبر باشه."
        )


def download_external(url, folder, format_id, audio_only=False):
    options = {
        "outtmpl": str(
            Path(folder) / "%(title).60s-%(id)s.%(ext)s"
        ),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "max_filesize": MAX_SIZE,
        "ffmpeg_location": FFMPEG_PATH,
        "socket_timeout": 30,
        "retries": 2,
    }

    if audio_only:
        options.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }],
        })
    else:
        if format_id == "best":
            options["format"] = "best"
        else:
            options["format"] = format_id

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.extract_info(url, download=True)

    files = [
        p for p in Path(folder).iterdir()
        if p.is_file()
        and not p.name.endswith((".part", ".ytdl"))
    ]

    if not files:
        raise Exception("DOWNLOAD_FAILED")

    return files


async def quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    pending = context.user_data.get("pending_download")

    if not pending:
        await query.edit_message_text(
            "❌ درخواست منقضی شده. لطفاً لینک رو دوباره بفرست."
        )
        return

    selection = query.data.split(":", 1)[1]

    if selection == "audio":
        if pending["platform"] != "youtube":
            await query.edit_message_text("❌ این گزینه پشتیبانی نمی‌شود.")
            return

        format_id = "bestaudio"
        audio_only = True

    else:
        try:
            index = int(selection)
            choice = pending["choices"][index]
            format_id = choice["format_id"]
        except (ValueError, IndexError, KeyError):
            await query.edit_message_text(
                "❌ انتخاب کیفیت معتبر نیست."
            )
            return

        audio_only = False

    await query.edit_message_text(
        "⏳ کیفیت انتخاب شد. در حال دانلود..."
    )

    url = pending["url"]
    platform = pending["platform"]

    try:
        with tempfile.TemporaryDirectory() as folder:
            files = await asyncio.to_thread(
                download_external,
                url,
                folder,
                format_id,
                audio_only
            )

            sent = 0

            for path in files[:10]:
                size = path.stat().st_size

                if size == 0 or size > MAX_SIZE:
                    continue

                ext = path.suffix.lower()

                with path.open("rb") as file:
                    if audio_only and ext == ".mp3":
                        await context.bot.send_audio(
                            chat_id=query.message.chat_id,
                            audio=file,
                            title=path.stem[:64],
                            caption="دانلود با HANGIIAT ❤️"
                        )

                    elif ext in (".jpg", ".jpeg", ".png", ".webp"):
                        if size <= 10 * 1024 * 1024:
                            await context.bot.send_photo(
                                chat_id=query.message.chat_id,
                                photo=file,
                                caption="دانلود با HANGIIAT ❤️"
                            )
                        else:
                            await context.bot.send_document(
                                chat_id=query.message.chat_id,
                                document=file,
                                caption="دانلود با HANGIIAT ❤️"
                            )

                    elif ext == ".mp4":
                        await context.bot.send_video(
                            chat_id=query.message.chat_id,
                            video=file,
                            caption="دانلود با HANGIIAT ❤️"
                        )

                    else:
                        await context.bot.send_document(
                            chat_id=query.message.chat_id,
                            document=file,
                            caption="دانلود با HANGIIAT ❤️"
                        )

                sent += 1

            if sent == 0:
                raise Exception("NO_SENDABLE_FILE")

        context.user_data.pop("pending_download", None)

        await query.message.reply_text(
            "✅ دانلود با موفقیت انجام شد."
        )

    except Exception as error:
        logging.exception("External download failed")

        if "NO_SENDABLE_FILE" in str(error):
            reply = "❌ فایل قابل ارسال نیست یا حجمش بیش از حد مجازه."
        else:
            reply = (
                "❌ دانلود انجام نشد.\n"
                "ممکنه فرمت انتخابی یا لینک محدود باشه."
            )

        await query.message.reply_text(reply)


# ---------------- MAIN HANDLER ----------------

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    instagram_post = re.match(
        r"^https?://(?:www\.)?instagram\.com/(?:p|reel)/[^/?#]+",
        url, re.IGNORECASE
    )

    instagram_story = re.match(
        r"^https?://(?:www\.)?instagram\.com/stories/[^/?#]+",
        url, re.IGNORECASE
    )

    is_reel = re.match(
        r"^https?://(?:www\.)?instagram\.com/reel/",
        url, re.IGNORECASE
    )

    pinterest = re.match(
        r"^https?://(?:(?:www|pin)\.)?pinterest\.[a-z.]+/",
        url, re.IGNORECASE
    )

    youtube = re.match(
        r"^https?://(?:(?:www|m|music)\.)?(?:youtube\.com|youtu\.be)/",
        url, re.IGNORECASE
    )

    if not (
        instagram_post
        or instagram_story
        or pinterest
        or youtube
    ):
        await update.message.reply_text(
            "❌ لینک معتبر اینستاگرام، پینترست یا یوتیوب بفرست."
        )
        return

    if (instagram_post or instagram_story) and not SAVEAPI_KEY:
        await update.message.reply_text(
            "❌ کلید SaveAPI در تنظیمات سرور پیدا نشد."
        )
        return

    if instagram_post or instagram_story:
        msg = await update.message.reply_text(
            "⏳ در حال دریافت محتوای اینستاگرام..."
        )

        try:
            await handle_instagram(
                update,
                url,
                bool(instagram_story),
                bool(is_reel),
                msg
            )

        except Exception as error:
            logging.exception("Instagram download failed")

            error_text = str(error)

            if "PRIVATE_CONTENT" in error_text:
                reply = "❌ این پیج خصوصی است."
            elif "MEDIA_NOT_FOUND" in error_text:
                reply = "❌ محتوای قابل دانلود پیدا نشد."
            elif "QUOTA_EXCEEDED" in error_text:
                reply = "❌ اعتبار SaveAPI تمام شده است."
            else:
                reply = "❌ دریافت محتوای اینستاگرام ممکن نشد."

            try:
                await msg.edit_text(reply)
            except Exception:
                await update.message.reply_text(reply)

        return

    if pinterest:
        await prepare_external(
            update, context, url, "pinterest"
        )
        return

    if youtube:
        await prepare_external(
            update, context, url, "youtube"
        )
        return


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        CallbackQueryHandler(
            quality_callback,
            pattern=r"^quality:"
        )
    )

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
