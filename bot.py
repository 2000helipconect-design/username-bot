import os
import re
import html
import time
import asyncio
from urllib.parse import quote

import aiohttp
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters,
)

# ------------------------------------------------------------------
‎# إعدادات عامة
# ------------------------------------------------------------------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = aiohttp.ClientTimeout(total=8)
MAX_CONCURRENT = 10          # أقصى عدد طلبات متزامنة (يحمي الـ IP من الحظر)
COOLDOWN_SECONDS = 10        # فاصل بين فحص وفحص لنفس المستخدم
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")

UNKNOWN = ("unknown", None)
INVALID = ("invalid", None)

LABELS = {
    ("available", "high"): "متاح ✅",
    ("taken", "high"): "مستخدم ❌",
    ("available", "medium"): "متاح غالباً ✅",
    ("taken", "medium"): "مستخدم غالباً ❌",
    ("unknown", None): "غير مؤكد ⚠️",
    ("invalid", None): "غير صالح للمنصة ⛔",
}

_last_use = {}


# ------------------------------------------------------------------
‎# أدوات الطلبات
# ------------------------------------------------------------------
async def fetch(session, url, want_json=False):
‎    """يرجع (status, body, final_url) أو (None, None, '') عند الفشل."""
    try:
        async with session.get(url, allow_redirects=True) as resp:
            if want_json:
                try:
                    body = await resp.json(content_type=None)
                except Exception:
                    body = None
            else:
                body = await resp.text(errors="ignore")
            return resp.status, body, str(resp.url)
    except Exception:
        return None, None, ""


def by_status(url_tpl, conf="medium"):
‎    """404 = متاح، 200 = مستخدم، أي شيء ثاني = غير مؤكد.
‎    وإذا تم تحويلنا لصفحة ما فيها اليوزر (تسجيل دخول، موافقة كوكيز) = غير مؤكد."""
    async def check(session, u):
        status, _, final = await fetch(session, url_tpl.format(u=quote(u)))
        if status is None or u.lower() not in final.lower():
            return UNKNOWN
        if status == 404:
            return ("available", conf)
        if status == 200:
            return ("taken", conf)
        return UNKNOWN
    return check


# ------------------------------------------------------------------
‎# فحوصات مخصصة (تعتمد على APIs واضحة، أدق من فحص الصفحات)
# ------------------------------------------------------------------
async def check_gitlab(session, u):
    status, data, _ = await fetch(
        session, f"https://gitlab.com/api/v4/users?username={quote(u)}", True)
    if status == 200 and isinstance(data, list):
        return ("taken", "high") if data else ("available", "high")
    return UNKNOWN


async def check_minecraft(session, u):
    status, _, _ = await fetch(
        session, f"https://api.mojang.com/users/profiles/minecraft/{quote(u)}")
    if status in (204, 404):
        return ("available", "high")
    if status == 200:
        return ("taken", "high")
    return UNKNOWN


async def check_keybase(session, u):
    status, data, _ = await fetch(
        session,
        f"https://keybase.io/_/api/1.0/user/lookup.json?usernames={quote(u)}&fields=basics",
        True)
    if status == 200 and isinstance(data, dict) and "them" in data:
        them = data["them"]
        return ("available", "high") if (not them or them[0] is None) else ("taken", "high")
    return UNKNOWN


async def check_roblox(session, u):
    url = ("https://auth.roblox.com/v1/usernames/validate"
           f"?username={quote(u)}&birthday=2000-01-01&context=Signup")
    status, data, _ = await fetch(session, url, True)
    if status == 200 and isinstance(data, dict):
        code = data.get("code")
        if code == 0:
            return ("available", "high")
        if code == 1:
            return ("taken", "high")
        if code in (3, 10):
            return INVALID
    return UNKNOWN


async def check_twitch(session, u):
    status, _, _ = await fetch(
        session, f"https://passport.twitch.tv/usernames/{quote(u)}")
    if status == 204:
        return ("available", "high")
    if status == 200:
        return ("taken", "high")
    return UNKNOWN


async def check_steam(session, u):
    status, text, _ = await fetch(
        session, f"https://steamcommunity.com/id/{quote(u)}")
    if status != 200 or text is None:
        return UNKNOWN
    if "The specified profile could not be found" in text:
        return ("available", "high")
    return ("taken", "high")


async def check_telegram(session, u):
    status, text, _ = await fetch(session, f"https://t.me/{quote(u)}")
    if status != 200 or text is None:
        return UNKNOWN
    if "tgme_page_title" in text:
        return ("taken", "medium")
    return ("available", "medium")


# ------------------------------------------------------------------
‎# قائمة المنصات: (الاسم، الفاحص، نمط اليوزر الصالح أو None)
# ------------------------------------------------------------------
RELIABLE = [
    ("GitHub",   by_status("https://github.com/{u}", "high"),
        re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")),
    ("GitLab",   check_gitlab, None),
    ("Chess.com", by_status("https://api.chess.com/pub/player/{u}", "high"),
        re.compile(r"^[A-Za-z0-9_-]{3,25}$")),
    ("Lichess",  by_status("https://lichess.org/api/user/{u}", "high"),
        re.compile(r"^[A-Za-z0-9_-]{2,20}$")),
    ("Minecraft", check_minecraft, re.compile(r"^[A-Za-z0-9_]{3,16}$")),
    ("Keybase",  check_keybase, re.compile(r"^[A-Za-z0-9_]{2,16}$")),
    ("Roblox",   check_roblox, None),
    ("Twitch",   check_twitch, re.compile(r"^[A-Za-z0-9_]{4,25}$")),
    ("Steam",    check_steam, re.compile(r"^[A-Za-z0-9_-]{3,32}$")),
]

APPROX = [
    ("Telegram", check_telegram, re.compile(r"^[A-Za-z0-9_]{5,32}$")),
    ("Reddit",   by_status("https://www.reddit.com/user/{u}/about.json"),
        re.compile(r"^[A-Za-z0-9_-]{3,20}$")),
    ("YouTube",  by_status("https://www.youtube.com/@{u}"), None),
    ("Tumblr",   by_status("https://{u}.tumblr.com"),
        re.compile(r"^[A-Za-z0-9-]{1,32}$")),
    ("Medium",   by_status("https://medium.com/@{u}"), None),
    ("SoundCloud", by_status("https://soundcloud.com/{u}"), None),
    ("Vimeo",    by_status("https://vimeo.com/{u}"), None),
    ("Behance",  by_status("https://www.behance.net/{u}"), None),
    ("Dribbble", by_status("https://dribbble.com/{u}"), None),
    ("DeviantArt", by_status("https://www.deviantart.com/{u}"), None),
    ("Patreon",  by_status("https://www.patreon.com/{u}"), None),
    ("Ko-fi",    by_status("https://ko-fi.com/{u}"), None),
    ("Buy Me a Coffee", by_status("https://www.buymeacoffee.com/{u}"), None),
    ("Pinterest", by_status("https://www.pinterest.com/{u}/"), None),
    ("Last.fm",  by_status("https://www.last.fm/user/{u}"), None),
    ("Wattpad",  by_status("https://www.wattpad.com/user/{u}"), None),
    ("osu!",     by_status("https://osu.ppy.sh/users/{u}"), None),
]

‎# منصات تحجب الفحص الآلي: ما نخمّن، نعطيك رابط تفتحه بنفسك
MANUAL = [
    ("Instagram", "https://www.instagram.com/{u}/"),
    ("Threads",   "https://www.threads.net/@{u}"),
    ("TikTok",    "https://www.tiktok.com/@{u}"),
    ("X (Twitter)", "https://x.com/{u}"),
    ("Snapchat",  "https://www.snapchat.com/add/{u}"),
    ("LinkedIn",  "https://www.linkedin.com/in/{u}"),
    ("Kick",      "https://kick.com/{u}"),
    ("Fiverr",    "https://www.fiverr.com/{u}"),
    ("PayPal.me", "https://www.paypal.me/{u}"),
]
‎# منصات ما لها رابط عام لليوزر (تفحصها من داخل التطبيق)
IN_APP_ONLY = ["Discord", "Epic Games", "PlayStation", "Xbox", "Riot Games",
               "Rockstar", "Spotify", "Apple Music", "WhatsApp"]

TOTAL = len(RELIABLE) + len(APPROX) + len(MANUAL) + len(IN_APP_ONLY)


# ------------------------------------------------------------------
‎# منطق الفحص
# ------------------------------------------------------------------
async def run_check(session, sem, name, checker, pattern, u):
    if pattern is not None and not pattern.match(u):
        return name, INVALID
    async with sem:
        try:
            return name, await checker(session, u)
        except Exception:
            return name, UNKNOWN


async def check_all(u):
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    async with aiohttp.ClientSession(headers=HEADERS, timeout=TIMEOUT) as session:
        rel = await asyncio.gather(
            *[run_check(session, sem, n, c, p, u) for n, c, p in RELIABLE])
        apx = await asyncio.gather(
            *[run_check(session, sem, n, c, p, u) for n, c, p in APPROX])
    return rel, apx


def build_report(u, rel, apx):
    esc = html.escape
    lines = [f"🔎 نتيجة فحص: <code>@{esc(u)}</code>", ""]

    lines.append("<b>✅ نتائج موثوقة</b>")
    for name, res in rel:
        lines.append(f"• {esc(name)}: {LABELS[res]}")

    lines += ["", "<b>🟡 نتائج تقريبية</b> (تعتمد على شكل الصفحة وقد تخطئ)"]
    for name, res in apx:
        lines.append(f"• {esc(name)}: {LABELS[res]}")

    lines += ["", "<b>🔗 افحصها يدوياً</b> (تحجب الفحص الآلي، فما أخمّن)"]
    for name, tpl in MANUAL:
        url = tpl.format(u=quote(u))
        lines.append(f'• <a href="{esc(url)}">{esc(name)}</a>')

    lines += ["", "<b>📱 فحصها من داخل التطبيق:</b> " + "، ".join(IN_APP_ONLY)]
    lines += ["", "ملاحظة: «متاح» يعني ما لقيت حساب بهذا الاسم الحين، "
‎                  "مو أنه محجوز لك. تأكد عند التسجيل."]
    return "\n".join(lines)


def split_message(text, limit=4000):
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


# ------------------------------------------------------------------
‎# أوامر البوت
# ------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
‎        "أهلاً! أرسل أي اسم مستخدم وأفحص لك توفره.\n\n"
        f"المنصات المغطاة: {TOTAL}\n"
        f"• {len(RELIABLE)} فحص موثوق\n"
        f"• {len(APPROX)} فحص تقريبي\n"
        f"• {len(MANUAL)} أعطيك رابط تفحصه بنفسك\n"
        f"• {len(IN_APP_ONLY)} من داخل التطبيق فقط\n\n"
‎        "الشروط: 3 إلى 30 حرف، إنجليزي أو أرقام أو _ . -"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = (update.message.text or "").strip().lstrip("@")

    if not USERNAME_RE.match(u):
        await update.message.reply_text(
‎            "اليوزر غير صالح. استخدم 3 إلى 30 حرف: إنجليزي، أرقام، _ . -")
        return

    user_id = update.effective_user.id
    now = time.time()
    wait = COOLDOWN_SECONDS - (now - _last_use.get(user_id, 0))
    if wait > 0:
        await update.message.reply_text(f"انتظر {int(wait) + 1} ثانية وجرّب مرة ثانية.")
        return
    _last_use[user_id] = now

    status_msg = await update.message.reply_text(f"🔍 جاري فحص @{u} ...")

    rel, apx = await check_all(u)
    report = build_report(u, rel, apx)
    chunks = split_message(report)

    await status_msg.edit_text(
        chunks[0], parse_mode="HTML", disable_web_page_preview=True)
    for chunk in chunks[1:]:
        await update.message.reply_text(
            chunk, parse_mode="HTML", disable_web_page_preview=True)


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("خطأ: ضع توكن البوت في متغير البيئة TELEGRAM_BOT_TOKEN")
        return

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("البوت يعمل الآن...")
    app.run_polling()


if __name__ == "__main__":
    main()
