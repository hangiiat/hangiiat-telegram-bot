
import os
import re
import asyncio
import tempfile
import threading
import logging
import hashlib
import mimetypes
from pathlib import Path
from urllib.parse import urlparse
from html.parser import HTMLParser

import requests
import yt_dlp
import imageio_ffmpeg

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
    level=logging.INFO,
)

TOKEN = os.environ["BOT_TOKEN"]
SAVEAPI_KEY = os.environ.get("SAVEAPI_KEY", "").strip()
API_BASE = "https://api.saveapi.org/v1"
MAX_SIZE = 45 * 1024 * 1024
FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36"
    )
}

web = Flask(__name__)


@web.get("/")
def home():
    return "HANGIIAT Bot is running!"


# ---------------- START ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام 👋 به HANGIIAT Downloader خوش اومدی.\n\n"
        "📸 دانلود از اینستاگرام\n"
        "📌 دانلود عکس و ویدئو از پینترست\n\n"
        "لینک موردنظرت رو بفرست ❤️"
    )


# ---------------- HTML METADATA ----------------

class MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.data = {}

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta":
            return

        attrs = dict(attrs)
        key = (
            attrs.get("property")
            or attrs.get("name")
            or ""
        ).lower()

        value = attrs.get("content")

        if key and value:
            self.data[key] = value


def get_page_metadata(url):
    response = requests.get(
        url,
        headers=HEADERS,
        allow_redirects=True,
        timeout=30,
    )
    response.raise_for_status()

    parser = MetaParser()
    parser.feed(response.text)

    return response.url, parser.data


# ---------------- PINTEREST URL ----------------

def pinterest_host(host):
    host = (host or "").lower().split(":")[0]

    if host == "pin.it":
        return True

    if host.startswith("www."):
        host = host[4:]

    return host == "pinterest.com" or host.endswith(
        (".pinterest.com", ".pinterest.co.uk", ".pinterest.ca")
    )


def is_pinterest_url(url):
    return pinterest_host(urlparse(url).hostname)


def resolve_pinterest_url(url):
    if (urlparse(url).hostname or "").lower() == "pin.it":
        response = requests.get(
            url,
            headers=HEADERS,
            allow_redirects=True,
            timeout=30,
        )
        response.raise_for_status()
        url = response.url

    if not is_pinterest_url(url):
        raise Exception("INVALID_PINTEREST_URL")

    return url


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
        timeout=45,
    )

    try:
        data = response.json()
    except ValueError:
        raise Exception("INVALID_API_RESPONSE")

    if not response.ok or not data.get("success"):
        error = data.get("error") or {}
        raise Exception(
            error.get("message", "INSTAGRAM_DOWNLOAD_FAILED")
        )

    if not data.get("medias"):
        raise Exception("MEDIA_NOT_FOUND")

    return data


def fetch_media(media, folder, index):
    media_url = media.get("url")

    if not media_url:
        raise Exception("MEDIA_URL_MISSING")

    response = requests.get(
        media_url,
        headers=HEADERS,
        stream=True,
        timeout=(15, 60),
    )
    response.raise_for_status()

    content_length = int(
        response.headers.get("Content-Length", 0)
    )

    if content_length > MAX_SIZE:
        raise Exception("FILE_TOO_LARGE")

    ext = str(media.get("ext") or "").lower()
    ext = re.sub(r"[^a-z0-9]", "", ext)

    if not ext:
        ext = (
            "jpg"
            if media.get("type") == "image"
            else "mp4"
        )

    path = Path(folder) / f"media_{index}.{ext}"
    total = 0

    with path.open("wb") as file:
        for chunk in response.iter_content(65536):
            if not chunk:
                continue

            total += len(chunk)

            if total > MAX_SIZE:
                raise Exception("FILE_TOO_LARGE")

            file.write(chunk)

    if total == 0:
        raise Exception("EMPTY_FILE")

    return path


def file_hash(path):
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


async def handle_instagram(
    update,
    url,
    is_story,
    is_reel,
    msg,
):
    with tempfile.TemporaryDirectory() as folder:
        data = await asyncio.to_thread(
            resolve_media,
            url,
            is_story,
        )

        medias = data.get("medias", [])
        sent = 0
        failed = 0
        seen = set()

        for index, media in enumerate(medias[:10]):
            media_type = str(
                media.get("type", "")
            ).lower()

            if is_reel and media_type != "video":
                continue

            try:
                path = await asyncio.to_thread(
                    fetch_media,
                    media,
                    folder,
                    index,
                )

                digest = await asyncio.to_thread(
                    file_hash,
                    path,
                )

                if digest in seen:
                    continue

                seen.add(digest)

                with path.open("rb") as file:
                    ext = path.suffix.lower()

                    if (
                        media_type == "image"
                        and path.stat().st_size <= 10 * 1024 * 1024
                    ):
                        await update.message.reply_photo(
                            photo=file,
                            caption="دانلود با HANGIIAT ❤️",
                        )

                    elif media_type == "video" and ext == ".mp4":
                        await update.message.reply_video(
                            video=file,
                            caption="دانلود با HANGIIAT ❤️",
                        )

                    else:
                        await update.message.reply_document(
                            document=file,
                            caption="دانلود با HANGIIAT ❤️",
                        )

                sent += 1

                if is_story or is_reel:
                    break

            except Exception:
                failed += 1
                logging.exception("Instagram media failed")

        if sent == 0:
            raise Exception("NO_INSTAGRAM_MEDIA_SENT")

        try:
            await msg.delete()
        except Exception:
            pass

        if failed:
            await update.message.reply_text(
                f"⚠️ دریافت {failed} فایل ناموفق بود."
            )

        if len(medias) > 10 and not (is_story or is_reel):
            await update.message.reply_text(
                "⚠️ فقط ۱۰ فایل اول ارسال شد."
            )

        if is_reel:
            meta = data.get("meta") or {}
            caption = (
                meta.get("text")
                or meta.get("title")
                or ""
            ).strip()

            if caption:
                for part in split_text(caption):
                    await update.message.reply_text(part)


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


# ---------------- PINTEREST EXTRACTION ----------------

def yt_options():
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 35,
        "retries": 3,
        "fragment_retries": 5,
        "extractor_retries": 3,
        "ffmpeg_location": FFMPEG_PATH,
        "http_headers": HEADERS,
    }


def get_height(item):
    height = item.get("height")

    if height:
        try:
            return int(height)
        except (TypeError, ValueError):
            pass

    format_id = str(item.get("format_id") or "")
    match = re.search(
        r"(?<!\d)(\d{3,4})p?(?!\d)",
        format_id,
        re.I,
    )

    return int(match.group(1)) if match else 0


def extract_pinterest_formats(url):
    url = resolve_pinterest_url(url)
    extractor_error = None

    # ابتدا استخراج مستقیم با yt-dlp
    try:
        with yt_dlp.YoutubeDL(yt_options()) as ydl:
            info = ydl.extract_info(
                url,
                download=False,
            )

        formats = info.get("formats") or []
        choices = {}

        for item in formats:
            format_id = item.get("format_id")

            if not format_id:
                continue

            if item.get("vcodec") == "none":
                continue

            height = get_height(item)
            label = (
                f"{height}p"
                if height
                else "کیفیت اصلی"
            )

            size = (
                item.get("filesize")
                or item.get("filesize_approx")
                or 0
            )

            if size and size > MAX_SIZE:
                continue

            candidate = {
                "format": str(format_id),
                "label": label,
                "height": height,
                "is_image": False,
                "size": size,
            }

            old = choices.get(label)

            if old is None:
                choices[label] = candidate
            elif int(size or 0) > int(old.get("size") or 0):
                choices[label] = candidate

        if choices:
            result = sorted(
                choices.values(),
                key=lambda x: x["height"],
                reverse=True,
            )

            return (
                info.get("title") or "Pinterest",
                result,
                url,
            )

        direct_url = info.get("url")
        direct_ext = str(
            info.get("ext") or ""
        ).lower()

        if direct_url and direct_ext in (
            "mp4",
            "webm",
            "m4v",
        ):
            return (
                info.get("title") or "Pinterest",
                [{
                    "format": "direct:" + direct_url,
                    "label": "ویدئوی اصلی",
                    "height": 0,
                    "is_image": False,
                }],
                url,
            )

    except Exception as error:
        extractor_error = error
        logging.warning(
            "Pinterest yt-dlp failed: %s",
            error,
        )

    # بررسی اطلاعات صفحه در صورت شکست استخراج
    page_url, meta = get_page_metadata(url)

    video_url = (
        meta.get("og:video:secure_url")
        or meta.get("og:video")
        or meta.get("twitter:player:stream")
        or meta.get("twitter:player:stream:content")
    )

    image_url = (
        meta.get("og:image")
        or meta.get("twitter:image")
        or meta.get("twitter:image:src")
    )

    title = (
        meta.get("og:title")
        or meta.get("twitter:title")
        or "Pinterest"
    )

    og_type = (
        meta.get("og:type") or ""
    ).lower()

    video_signal = bool(video_url) or "video" in og_type

    if video_url:
        video_path = urlparse(video_url).path.lower()

        if video_path.endswith(
            (".mp4", ".m4v", ".webm")
        ):
            return (
                title,
                [{
                    "format": "direct:" + video_url,
                    "label": "ویدئوی اصلی",
                    "height": 0,
                    "is_image": False,
                }],
                page_url,
            )

        return (
            title,
            [{
                "format": "source:" + video_url,
                "label": "ویدئو - کیفیت اصلی",
                "height": 0,
                "is_image": False,
            }],
            page_url,
        )

    if video_signal:
        raise Exception(
            "PINTEREST_VIDEO_EXTRACTION_FAILED"
        ) from extractor_error

    if image_url:
        return (
            title,
            [{
                "format": "direct:" + image_url,
                "label": "تصویر اصلی",
                "height": 0,
                "is_image": True,
            }],
            page_url,
        )

    raise Exception("PINTEREST_MEDIA_NOT_FOUND")


# ---------------- PINTEREST KEYBOARD ----------------

def build_keyboard(choices):
    buttons = []

    for index, choice in enumerate(choices):
        buttons.append(
            InlineKeyboardButton(
                f"📥 {choice['label']}",
                callback_data=f"quality:{index}",
            )
        )

    rows = [
        buttons[i:i + 2]
        for i in range(0, len(buttons), 2)
    ]

    return InlineKeyboardMarkup(rows)


async def prepare_pinterest(update, context, url):
    msg = await update.message.reply_text(
        "⏳ در حال بررسی لینک پینترست..."
    )

    try:
        title, choices, final_url = await asyncio.to_thread(
            extract_pinterest_formats,
            url,
        )

        context.user_data["pending_download"] = {
            "url": final_url,
            "choices": choices,
            "title": title,
        }

        await msg.edit_text(
            f"📌 PINTEREST\n\n"
            f"🎞 {title[:180]}\n\n"
            "کیفیت موردنظرت رو انتخاب کن:",
            reply_markup=build_keyboard(choices),
        )

    except Exception as error:
        logging.exception(
            "Pinterest extraction error"
        )

        if "PINTEREST_VIDEO_EXTRACTION_FAILED" in str(error):
            text = (
                "❌ این پین ویدئویی است، اما فایل ویدئو دریافت نشد.\n"
                "کاور را به‌جای فیلم ارسال نمی‌کنم."
            )
        else:
            text = (
                "❌ دریافت محتوای پینترست ممکن نشد.\n"
                "ممکن است پین خصوصی یا لینک نامعتبر باشد."
            )

        await msg.edit_text(text)


# ---------------- DOWNLOAD ----------------

def download_direct(url, folder, expected_image=False):
    response = requests.get(
        url,
        headers=HEADERS,
        stream=True,
        timeout=(15, 60),
    )

    response.raise_for_status()

    content_type = (
        response.headers.get("Content-Type", "")
        .split(";")[0]
        .lower()
    )

    url_ext = Path(
        urlparse(url).path
    ).suffix.lower()

    if expected_image and content_type:
        if not content_type.startswith("image/"):
            raise Exception("URL_DID_NOT_RETURN_IMAGE")

    if content_type.startswith("image/"):
        ext = mimetypes.guess_extension(content_type) or ".jpg"

    elif content_type.startswith("video/"):
        ext = mimetypes.guess_extension(content_type) or ".mp4"

    elif url_ext in (
        ".jpg", ".jpeg", ".png", ".webp",
        ".mp4", ".m4v", ".webm",
    ):
        ext = url_ext

    else:
        ext = ".bin"

    path = Path(folder) / ("pinterest_file" + ext)

    total = 0
    content_length = int(
        response.headers.get("Content-Length", 0)
    )

    if content_length > MAX_SIZE:
        raise Exception("FILE_TOO_LARGE")

    with path.open("wb") as file:
        for chunk in response.iter_content(65536):
            if not chunk:
                continue

            total += len(chunk)

            if total > MAX_SIZE:
                raise Exception("FILE_TOO_LARGE")

            file.write(chunk)

    if total == 0:
        raise Exception("EMPTY_DOWNLOAD")

    return [path]


def download_external(url, folder, selector):
    if selector.startswith("direct:"):
        direct_url = selector[len("direct:"):]

        return download_direct(
            direct_url,
            folder,
        )

    if selector.startswith("source:"):
        source_url = selector[len("source:"):]
        target_url = source_url
        format_value = "best"
    else:
        target_url = url
        format_value = selector

    options = yt_options()

    options.update({
        "outtmpl": str(
            Path(folder) / "%(title).60s-%(id)s.%(ext)s"
        ),
        "restrictfilenames": True,
        "max_filesize": MAX_SIZE,
        "merge_output_format": "mp4",
        "format": format_value,
    })

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.extract_info(
            target_url,
            download=True,
        )

    files = [
        path
        for path in Path(folder).iterdir()
        if path.is_file()
        and not path.name.endswith(
            (".part", ".ytdl")
        )
    ]

    if not files:
        raise Exception("DOWNLOAD_FAILED")

    for path in files:
        if path.stat().st_size > MAX_SIZE:
            raise Exception("FILE_TOO_LARGE")

    return sorted(
        files,
        key=lambda path: path.stat().st_size,
        reverse=True,
    )


# ---------------- QUALITY CALLBACK ----------------

async def quality_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    pending = context.user_data.get(
        "pending_download"
    )

    if not pending:
        await query.edit_message_text(
            "❌ درخواست منقضی شده. لینک را دوباره بفرست."
        )
        return

    try:
        index = int(
            query.data.split(":", 1)[1]
        )

        choice = pending["choices"][index]

    except (ValueError, IndexError, KeyError):
        await query.edit_message_text(
            "❌ انتخاب کیفیت معتبر نیست."
        )
        return

    await query.edit_message_text(
        "⏳ در حال دانلود فایل..."
    )

    try:
        with tempfile.TemporaryDirectory() as folder:
            files = await asyncio.to_thread(
                download_external,
                pending["url"],
                folder,
                choice["format"],
            )

            sent = 0

            for path in files[:5]:
                size = path.stat().st_size

                if size == 0 or size > MAX_SIZE:
                    continue

                ext = path.suffix.lower()

                with path.open("rb") as file:
                    if ext in (
                        ".jpg",
                        ".jpeg",
                        ".png",
                        ".webp",
                        ".gif",
                    ):
                        if size <= 10 * 1024 * 1024:
                            await context.bot.send_photo(
                                chat_id=query.message.chat_id,
                                photo=file,
                                caption="دانلود با HANGIIAT ❤️",
                            )
                        else:
                            await context.bot.send_document(
                                chat_id=query.message.chat_id,
                                document=file,
                                caption="دانلود با HANGIIAT ❤️",
                            )

                    elif ext == ".mp4":
                        await context.bot.send_video(
                            chat_id=query.message.chat_id,
                            video=file,
                            caption="دانلود با HANGIIAT ❤️",
                        )

                    else:
                        await context.bot.send_document(
                            chat_id=query.message.chat_id,
                            document=file,
                            caption="دانلود با HANGIIAT ❤️",
                        )

                sent += 1

            if sent == 0:
                raise Exception("NO_SENDABLE_FILE")

        context.user_data.pop(
            "pending_download",
            None,
        )

        await query.message.reply_text(
            "✅ دانلود با موفقیت انجام شد."
        )

    except Exception:
        logging.exception(
            "Pinterest download failed"
        )

        await query.message.reply_text(
            "❌ دانلود انجام نشد.\n"
            "ممکن است پینترست دسترسی سرور را محدود کرده باشد "
            "یا حجم فایل بیش از حد مجاز باشد."
        )


# ---------------- MAIN LINK HANDLER ----------------

async def handle_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    url = update.message.text.strip()

    instagram_post = re.match(
        r"^https?://(?:www\.)?instagram\.com/(?:p|reel)/[^/?#]+",
        url,
        re.I,
    )

    instagram_story = re.match(
        r"^https?://(?:www\.)?instagram\.com/stories/[^/?#]+",
        url,
        re.I,
    )

    is_reel = re.match(
        r"^https?://(?:www\.)?instagram\.com/reel/",
        url,
        re.I,
    )

    pinterest = is_pinterest_url(url)

    if not (
        instagram_post
        or instagram_story
        or pinterest
    ):
        await update.message.reply_text(
            "❌ لینک معتبر اینستاگرام یا پینترست بفرست."
        )
        return

    if instagram_post or instagram_story:
        if not SAVEAPI_KEY:
            await update.message.reply_text(
                "❌ کلید SaveAPI در تنظیمات سرور پیدا نشد."
            )
            return

        msg = await update.message.reply_text(
            "⏳ در حال دریافت محتوای اینستاگرام..."
        )

        try:
            await handle_instagram(
                update,
                url,
                bool(instagram_story),
                bool(is_reel),
                msg,
            )

        except Exception:
            logging.exception(
                "Instagram download failed"
            )

            try:
                await msg.edit_text(
                    "❌ دریافت محتوای اینستاگرام ممکن نشد."
                )
            except Exception:
                await update.message.reply_text(
                    "❌ دریافت محتوای اینستاگرام ممکن نشد."
                )

        return

    if pinterest:
        await prepare_pinterest(
            update,
            context,
            url,
        )


# ---------------- MAIN ----------------

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(
            quality_callback,
            pattern=r"^quality:",
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_link,
        )
    )

    threading.Thread(
        target=lambda: web.run(
            host="0.0.0.0",
            port=int(os.environ.get("PORT", 10000)),
        ),
        daemon=True,
    ).start()

    logging.info("HANGIIAT bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
