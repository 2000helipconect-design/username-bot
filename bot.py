import os
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# قائمة المنصات الثماني ورابط فحص اليوزر عليها
PLATFORMS = {
    "Telegram": "https://t.me/{}",
    "Instagram": "https://www.instagram.com/{}/",
    "TikTok": "https://www.tiktok.com/@{}",
    "Twitter (X)": "https://twitter.com/{}",
    "GitHub": "https://github.com/{}",
    "Pinterest": "https://pinterest.com/{}",
    "SoundCloud": "https://soundcloud.com/{}",
    "Steam": "https://steamcommunity.com/id/{}"
}

async def check_username(session, url, username):
    target_url = url.format(username)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        async with session.get(target_url, headers=headers, timeout=5) as response:
            # المنصات عادة تعطي 404 إذا كان اليوزر غير متاح/موجود، أو يعتمد على منطق الموقع
            # للتوضيح: 404 تعني أن الصفحة غير موجودة وغالباً اليوزر متاح للتسجيل
            if response.status == 404:
                return "متاح ✅"
            else:
                return "مستخدم ❌"
    except Exception:
        return "غير معروف ⚠️"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً بك في بوت فحص اليوزرات الشامل! 🚀\n\n"
        "أرسل أي اسم مستخدم (Username) وسأقوم بفحص توفره على 8 منصات شهيرة فوراً."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip().lstrip('@')
    if not username:
        return

    msg = await update.message.reply_text(f"🔍 جاري فحص اليوزر: @{username} على المنصات...")

    results = []
    async with aiohttp.ClientSession() as session:
        tasks = []
        platform_names = list(PLATFORMS.keys())
        for name, url in PLATFORMS.items():
            tasks.append(check_username(session, url, username))
        
        statuses = await asyncio.gather(*tasks)

        for name, status in zip(platform_names, statuses):
            results.append(f"- **{name}**: {status}")

    report = f"نتائج الفحص لليوزر: `@{username}`\n\n" + "\n".join(results)
    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=report, parse_mode="Markdown")

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("خطأ: لم يتم تعيين توكن البوت في متغيرات البيئة!")
        return

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    print("البوت يعمل الآن...")
    app.run_polling()

if __name__ == "__main__":
    main()
