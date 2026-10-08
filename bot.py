import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
import aiohttp

# قراءة التوكن بأمان من إعدادات السيرفر (مخفي تماماً)
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# قائمة المنصات وروابطها للبحث
PLATFORMS = {
    "Telegram": "https://t.me/{}",
    "Instagram": "https://www.instagram.com/{}",
    "X (Twitter)": "https://x.com/{}",
    "TikTok": "https://www.tiktok.com/@{}",
    "YouTube": "https://www.youtube.com/@{}",
    "Snapchat": "https://www.snapchat.com/add/{}",
    "GitHub": "https://github.com/{}",
    "Pinterest": "https://www.pinterest.com/{}/"
}

async def check_url(session, url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        async with session.get(url, headers=headers, timeout=5) as response:
            if response.status == 404:
                return True
            return False
    except Exception:
        return False

async def check_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip().lstrip('@')
    
    loading_msg = await update.message.reply_text(f"🔍 جاري فحص توفر اليوزر **@{username}** على جميع المنصات، انتظر لحظات...", parse_mode="Markdown")
    
    results = []
    async with aiohttp.ClientSession() as session:
        tasks = []
        platform_names = list(PLATFORMS.keys())
        
        for name, url_template in PLATFORMS.items():
            url = url_template.format(username)
            tasks.append(check_url(session, url))
        
        status_list = await asyncio.gather(*tasks)
        
        for name, is_available in zip(platform_names, status_list):
            if is_available:
                results.append(f"✅ **{name}**: متاح")
            else:
                results.append(f"❌ **{name}**: محجوز / غير مؤكد")

    result_text = f"📊 نتائج فحص اليوزر: **@{username}**\n\n" + "\n".join(results)
    
    await loading_msg.edit_text(result_text, parse_mode="Markdown")

def main():
    if not TOKEN:
        print("خطأ: لم يتم تعيين التوكن!")
        return
        
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), check_username))
    app.run_polling()

if __name__ == "__main__":
    main()
