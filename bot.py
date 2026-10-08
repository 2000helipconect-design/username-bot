import os
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# التحقق الدقيق لكل منصة
async def check_username(session, platform, username):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    
    try:
        if platform == "Telegram":
            url = f"https://t.me/{username}"
            async with session.get(url, headers=headers, timeout=5) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    # تليجرام يعرض صفحة القناة/المستخدم إذا كان موجوداً
                    if "tgme_page_title" in text or "tgme_channel_info" in text:
                        return "مستخدم ❌"
                return "متاح ✅"

        elif platform == "Instagram":
            url = f"https://www.instagram.com/{username}/"
            async with session.get(url, headers=headers, timeout=5) as resp:
                if resp.status == 404:
                    return "متاح ✅"
                elif resp.status == 200:
                    text = await resp.text()
                    if "Sorry, this page isn't available." in text or "تم إلغاء تنشيط الصفحة" in text:
                        return "متاح ✅"
                    return "مستخدم ❌"
                return "مستخدم ❌"

        elif platform == "TikTok":
            url = f"https://www.tiktok.com/@{username}"
            async with session.get(url, headers=headers, timeout=5) as resp:
                if resp.status == 404:
                    return "متاح ✅"
                elif resp.status == 200:
                    text = await resp.text()
                    if "Couldn't find this account" in text or "التعرف على حسابات أخرى" in text:
                        return "متاح ✅"
                    return "مستخدم ❌"
                return "مستخدم ❌"

        elif platform == "Twitter (X)":
            url = f"https://twitter.com/{username}"
            async with session.get(url, headers=headers, timeout=5) as resp:
                if resp.status == 404:
                    return "متاح ✅"
                return "مستخدم ❌"

        elif platform == "GitHub":
            url = f"https://github.com/{username}"
            async with session.get(url, headers=headers, timeout=5) as resp:
                if resp.status == 404:
                    return "متاح ✅"
                return "مستخدم ❌"

        elif platform == "Pinterest":
            url = f"https://www.pinterest.com/{username}/"
            async with session.get(url, headers=headers, timeout=5) as resp:
                if resp.status == 404:
                    return "متاح ✅"
                return "مستخدم ❌"

        elif platform == "SoundCloud":
            url = f"https://soundcloud.com/{username}"
            async with session.get(url, headers=headers, timeout=5) as resp:
                if resp.status == 404:
                    return "متاح ✅"
                return "مستخدم ❌"

        elif platform == "Steam":
            url = f"https://steamcommunity.com/id/{username}"
            async with session.get(url, headers=headers, timeout=5) as resp:
                if resp.status == 404 or resp.status == 500:
                    return "متاح ✅"
                return "مستخدم ❌"

    except Exception:
        return "غير معروف ⚠️"
    
    return "مستخدم ❌"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً بك في بوت فحص اليوزرات المطور والدقيق! 🚀\n\n"
        "أرسل أي اسم مستخدم (Username) وسأقوم بفحص توفره على المنصات بدقة عالية."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip().lstrip('@')
    if not username:
        return

    msg = await update.message.reply_text(f"🔍 جاري الفحص بدقة لليوزر: @{username}...")

    platforms = ["Telegram", "Instagram", "TikTok", "Twitter (X)", "GitHub", "Pinterest", "SoundCloud", "Steam"]
    
    results = []
    async with aiohttp.ClientSession() as session:
        tasks = [check_username(session, p, username) for p in platforms]
        statuses = await asyncio.gather(*tasks)

        for name, status in zip(platforms, statuses):
            results.append(f"- **{name}**: {status}")

    report = f"📊 نتائج الفحص الدقيق لليوزر: `@{username}`\n\n" + "\n".join(results)
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
