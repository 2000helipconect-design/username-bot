import os
import re
import html
import time
import base64
import socket
import asyncio
import ipaddress
from urllib.parse import urlsplit, urljoin

import aiohttp
from telegram import BotCommand, Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
}
TIMEOUT = aiohttp.ClientTimeout(total=8)
MAX_REDIRECTS = 5
COOLDOWN_SECONDS = 5

SAFE_BROWSING_KEY = os.getenv("SAFE_BROWSING_API_KEY")
VIRUSTOTAL_KEY = os.getenv("VIRUSTOTAL_API_KEY")

SHORTENERS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "buff.ly",
    "cutt.ly", "rb.gy", "shorturl.at", "tiny.cc", "rebrand.ly", "s.id",
    "t.ly", "v.gd", "lnkd.in", "wa.me",
}

RISKY_TLDS = {
    "zip", "mov", "xyz", "top", "tk", "ml", "ga", "cf", "gq", "click",
    "country", "work", "support", "icu", "cyou", "rest", "monster", "buzz",
    "cam", "loan", "men", "date", "download", "review",
}

KEYWORDS = [
    "login", "signin", "verify", "verification", "update", "secure",
    "account", "wallet", "gift", "free", "bonus", "claim", "airdrop",
    "password", "suspended", "confirm", "prize", "winner", "giveaway",
    "recover", "unlock", "limited", "urgent",
]

BRANDS = [
    "paypal", "apple", "icloud", "google", "microsoft", "amazon", "facebook",
    "instagram", "netflix", "binance", "whatsapp", "telegram", "tiktok",
    "snapchat", "steam", "discord", "youtube", "twitter", "coinbase",
    "metamask", "dhl", "fedex", "aramex", "stcpay", "alrajhi", "absher",
]

SECOND_LEVEL = {"co", "com", "org", "net", "gov", "edu", "ac"}

URL_WITH_SCHEME = re.compile(r"https?://[^\s<>\"']+", re.I)
URL_NO_SCHEME = re.compile(
    r"(?<![@\w.-])(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?::\d+)?(?:/[^\s<>\"']*)?")

_last_use = {}


def extract_url(text):
    m = URL_WITH_SCHEME.search(text)
    if m:
        return m.group(0).rstrip(".,;:!?)]}»«")
    m = URL_NO_SCHEME.search(text)
    if m:
        return "https://" + m.group(0).rstrip(".,;:!?)]}»«")
    return None


def registered_domain(host):
    parts = host.lower().split(".")
    if len(parts) <= 2:
        return host.lower()
    if len(parts[-1]) == 2 and parts[-2] in SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_ip(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


async def host_is_public(host):
    if is_ip(host):
        return ipaddress.ip_address(host).is_global
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except Exception:
        return None
    addrs = {i[4][0] for i in infos}
    if not addrs:
        return None
    return all(ipaddress.ip_address(a).is_global for a in addrs)


async def follow_redirects(session, url):
    chain = [url]
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(current)
        host = parts.hostname
        if parts.scheme not in ("http", "https") or not host:
            return chain, "blocked", None
        if parts.port not in (None, 80, 443):
            return chain, "blocked", None
        public = await host_is_public(host)
        if public is None:
            return chain, "unreachable", None
        if not public:
            return chain, "internal", None
        try:
            async with session.get(current, allow_redirects=False) as resp:
                status = resp.status
                location = resp.headers.get("Location")
        except Exception:
            return chain, "unreachable", None
        if status in (301, 302, 303, 307, 308) and location:
            current = urljoin(current, location)
            chain.append(current)
            continue
        return chain, "ok", status
    return chain, "too_many", None


def analyze_url(url, label=""):
    signals = []
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    full = url.lower()
    reg = registered_domain(host)
    tld = host.rsplit(".", 1)[-1] if "." in host else ""

    if parts.scheme == "http":
        signals.append((1, "بدون تشفير HTTPS"))
    if is_ip(host):
        signals.append((3, "الرابط عنوان IP مباشر بدل اسم موقع"))
    if "xn--" in host:
        signals.append((3, "اسم الموقع بحروف مموّهة (قد يقلّد موقعاً معروفاً)"))
    if "@" in parts.netloc:
        signals.append((3, "فيه علامة @ داخل الرابط، وهذا أسلوب تضليل شائع"))
    if tld in RISKY_TLDS:
        signals.append((2, f"امتداد الموقع (.{tld}) كثير الاستخدام في الاحتيال"))
    if host.count(".") >= 4:
        signals.append((1, "عدد كبير من النطاقات الفرعية"))
    if len(url) > 100:
        signals.append((1, "الرابط طويل بشكل غير عادي"))
    if not reg.startswith("xn--") and reg.split(".")[0].count("-") >= 3:
        signals.append((1, "اسم الموقع فيه شرطات كثيرة"))

    hits = [k for k in KEYWORDS if k in full]
    if hits:
        pts = 2 if len(hits) >= 2 else 1
        signals.append((pts, "كلمات تُستخدم كثيراً في الاحتيال: " + "، ".join(hits[:4])))

    for brand in BRANDS:
        if brand in host and reg.split(".")[0] != brand:
            signals.append((3, f"يحتوي اسم «{brand}» لكنه ليس الموقع الرسمي ({reg})"))
            break

    return signals


async def safe_browsing_check(session, urls):
    if not SAFE_BROWSING_KEY:
        return None
    body = {
        "client": {"clientId": "link-checker-bot", "clientVersion": "1.0"},
        "threatInfo": {
            "threatTypes": [
                "MALWARE", "SOCIAL_ENGINEERING",
                "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION",
            ],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": u} for u in urls],
        },
    }
    try:
        async with session.post(
            "https://safebrowsing.googleapis.com/v4/threatMatches:find",
            params={"key": SAFE_BROWSING_KEY}, json=body,
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            return bool(data.get("matches"))
    except Exception:
        return None


async def virustotal_check(session, url):
    if not VIRUSTOTAL_KEY:
        return None
    url_id = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    try:
        async with session.get(
            f"https://www.virustotal.com/api/v3/urls/{url_id}",
            headers={"x-apikey": VIRUSTOTAL_KEY},
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            stats = data["data"]["attributes"]["last_analysis_stats"]
            return stats.get("malicious", 0), stats.get("suspicious", 0)
    except Exception:
        return None


async def check_link(url):
    async with aiohttp.ClientSession(headers=HEADERS, timeout=TIMEOUT) as session:
        chain, state, status = await follow_redirects(session, url)
        final = chain[-1]
        sb, vt = await asyncio.gather(
            safe_browsing_check(session, list(dict.fromkeys(chain))),
            virustotal_check(session, final),
        )

    signals = analyze_url(url)
    start_host = urlsplit(url).hostname or ""
    final_host = urlsplit(final).hostname or ""

    if len(chain) > 1:
        if registered_domain(final_host) != registered_domain(start_host):
            if start_host in SHORTENERS or registered_domain(start_host) in SHORTENERS:
                signals.append((0, f"رابط مختصر، وجهته الفعلية: {final_host}"))
            else:
                signals.append((1, f"يحوّلك لموقع مختلف: {final_host}"))
        for extra, text in analyze_url(final):
            if (extra, text) not in signals:
                signals.append((extra, "الوجهة النهائية: " + text))
    if len(chain) >= 4:
        signals.append((1, "تحويلات كثيرة قبل الوصول للصفحة"))

    notes = []
    if state == "internal":
        signals.append((3, "الرابط يشير لعنوان داخلي أو خاص، ولم أفتحه"))
    elif state == "unreachable":
        notes.append("ما قدرت أوصل للموقع (قد يكون معطلاً أو محذوفاً)")
    elif state == "too_many":
        signals.append((2, "تحويلات متكررة بلا نهاية واضحة"))
    elif state == "blocked":
        notes.append("الرابط يستخدم منفذاً أو بروتوكولاً غير عادي ولم أفتحه")
    elif status and status >= 400:
        notes.append(f"الموقع رد بحالة {status}")

    score = sum(p for p, _ in signals)
    flagged = False

    if sb is True:
        flagged = True
        signals.append((10, "مُدرج في قائمة Google Safe Browsing كموقع خطير"))
    if vt is not None:
        mal, sus = vt
        if mal >= 1:
            flagged = True
            signals.append((4 if mal < 5 else 8,
                            f"{mal} محرك أمان على VirusTotal صنّفه خبيثاً"))
        elif sus >= 1:
            signals.append((2, f"{sus} محرك أمان على VirusTotal وصفه بالمشبوه"))

    score = sum(p for p, _ in signals)
    external = []
    if sb is None and SAFE_BROWSING_KEY:
        external.append("تعذّر الاتصال بـ Safe Browsing")
    if vt is None and VIRUSTOTAL_KEY:
        external.append("VirusTotal ما عنده معلومات عن هذا الرابط أو تعذّر الاتصال")

    return chain, signals, notes, external, score, flagged


def build_report(url, chain, signals, notes, external, score, flagged):
    esc = html.escape
    if flagged or score >= 6:
        verdict = "🔴 خطير، لا تفتحه"
    elif score >= 3:
        verdict = "🟠 مشبوه، لا تدخل بياناتك فيه"
    else:
        verdict = "🟢 ما لقيت مؤشرات خطر واضحة"

    shown = url if len(url) <= 150 else url[:150] + "..."
    lines = [f"<b>{verdict}</b>", "", f"الرابط: <code>{esc(shown)}</code>"]

    if len(chain) > 1:
        final = chain[-1]
        shown_final = final if len(final) <= 150 else final[:150] + "..."
        lines.append(f"الوجهة النهائية: <code>{esc(shown_final)}</code>")

    risky = [t for p, t in signals if p > 0]
    infos = [t for p, t in signals if p == 0]

    if risky:
        lines += ["", "<b>المؤشرات:</b>"] + [f"• {esc(t)}" for t in risky]
    if infos:
        lines += [""] + [f"ℹ️ {esc(t)}" for t in infos]
    if notes:
        lines += [""] + [f"ℹ️ {esc(t)}" for t in notes]
    if external:
        lines += [""] + [f"ℹ️ {esc(t)}" for t in external]

    if not SAFE_BROWSING_KEY and not VIRUSTOTAL_KEY:
        lines += ["", "ℹ️ الفحص هنا يعتمد على شكل الرابط وتحويلاته فقط، "
                      "بدون قواعد بيانات تهديدات."]

    lines += ["", "تنبيه: ما فيه فحص يضمن الأمان 100%. إذا الرابط وصلك من "
                  "شخص ما تعرفه أو يطلب بياناتك أو مالاً، لا تفتحه حتى لو طلع 🟢."]
    return "\n".join(lines)


HELP_TEXT = (
    "طريقة الاستخدام:\n"
    "أرسل أي رابط وأفحصه لك قبل ما تفتحه.\n"
    "مثال: https://example.com/login\n\n"
    "وش أفحص:\n"
    "• شكل الرابط وكلمات الاحتيال الشائعة\n"
    "• الروابط المختصرة وين توصّل فعلاً\n"
    "• تقليد المواقع المعروفة\n"
    "• قوائم التهديدات (Google وVirusTotal) إذا فعّلها صاحب البوت\n\n"
    "معنى النتائج:\n"
    "🟢 ما لقيت مؤشرات خطر (مو ضمان أمان)\n"
    "🟠 مشبوه\n"
    "🔴 خطير\n\n"
    "أنا ما أفتح محتوى الصفحة، أقرأ فقط وجهة التحويل.\n\n"
    "الأوامر:\n"
    "/start - البداية\n"
    "/help - الشرح"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً! أنا بوت فحص الروابط 🛡️\n"
        "أرسل لي أي رابط مشبوه وأقول لك إذا فيه مؤشرات احتيال.\n\n" + HELP_TEXT
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    url = extract_url(text)

    if not url:
        await update.message.reply_text(
            "ما لقيت رابط في رسالتك 🙁\n"
            "أرسل الرابط كامل، مثال: https://example.com\n"
            "أو اكتب /help للشرح."
        )
        return

    user_id = update.effective_user.id
    now = time.time()
    wait = COOLDOWN_SECONDS - (now - _last_use.get(user_id, 0))
    if wait > 0:
        await update.message.reply_text(f"⏳ انتظر {int(wait) + 1} ثواني وجرّب مرة ثانية.")
        return
    _last_use[user_id] = now

    status_msg = await update.message.reply_text("🔍 جاري فحص الرابط ...")

    try:
        result = await check_link(url)
        report = build_report(url, *result)
    except Exception:
        await status_msg.edit_text("صار خطأ أثناء الفحص 😕\nجرّب مرة ثانية بعد شوي.")
        return

    await status_msg.edit_text(
        report[:4000], parse_mode="HTML", disable_web_page_preview=True)


async def handle_other(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أرسل الرابط كنص فقط.\nأو اكتب /help للشرح."
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("خطأ:", context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "صار خطأ غير متوقع 😕 جرّب مرة ثانية.")
        except Exception:
            pass


async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start", "البداية"),
        BotCommand("help", "طريقة الاستخدام ومعنى النتائج"),
    ])


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("خطأ: ضع توكن البوت في متغير البيئة TELEGRAM_BOT_TOKEN")
        return

    app = ApplicationBuilder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, handle_other))
    app.add_error_handler(on_error)

    print("بوت فحص الروابط يعمل الآن...")
    app.run_polling()


if __name__ == "__main__":
    main()
