"""
Discord Auto-Translate Bot (Việt <-> Anh)
------------------------------------------
- Tự động phát hiện tin nhắn tiếng Anh -> dịch sang tiếng Việt
- Tự động phát hiện tin nhắn tiếng Việt -> dịch sang tiếng Anh
- Hoạt động với CẢ tin nhắn từ người dùng lẫn từ bot khác
- Xóa tin nhắn gốc, gửi lại bản dịch qua Webhook giả danh
  đúng tên hiển thị + avatar của người/bot gửi gốc
  (nhìn như tin nhắn "tự đổi ngôn ngữ", không lộ là bot dịch)

Cách dùng:
1. Điền TOKEN vào file .env (copy từ .env.example)
2. Điền ID các kênh muốn bot hoạt động vào TRANSLATE_CHANNEL_IDS
   (để trống = áp dụng cho toàn bộ kênh text trong server)
3. Chạy: python bot.py
"""

import os
import re
import asyncio
import logging

import discord
from discord.ext import commands
from dotenv import load_dotenv
from langdetect import detect, LangDetectException
from deep_translator import GoogleTranslator

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_IDS_RAW = os.getenv("TRANSLATE_CHANNEL_IDS", "").strip()
TRANSLATE_CHANNEL_IDS = (
    {int(x) for x in CHANNEL_IDS_RAW.split(",") if x.strip()}
    if CHANNEL_IDS_RAW
    else None  # None = áp dụng mọi kênh
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("translate-bot")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.webhooks = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Lưu webhook đã tạo theo từng kênh để tái sử dụng (channel_id -> discord.Webhook)
_webhook_cache: dict[int, discord.Webhook] = {}
# Lưu ID các webhook do chính bot này tạo, để tránh dịch lại tin do chính nó gửi (chống loop)
_own_webhook_ids: set[int] = set()

URL_PATTERN = re.compile(r"https?://\S+")


def should_process_channel(channel_id: int) -> bool:
    if TRANSLATE_CHANNEL_IDS is None:
        return True
    return channel_id in TRANSLATE_CHANNEL_IDS


def detect_lang(text: str) -> str | None:
    """Trả về 'en', 'vi', hoặc None nếu không đoán được / không cần dịch."""
    # Bỏ qua tin chỉ có link, emoji, hoặc quá ngắn (khó đoán ngôn ngữ chính xác)
    stripped = URL_PATTERN.sub("", text).strip()
    if len(stripped) < 2:
        return None
    try:
        lang = detect(stripped)
    except LangDetectException:
        return None
    if lang == "en":
        return "en"
    if lang == "vi":
        return "vi"
    return None  # ngôn ngữ khác thì bỏ qua, không dịch


async def translate_text(text: str, source: str, target: str) -> str:
    def _translate():
        return GoogleTranslator(source=source, target=target).translate(text)
    # deep_translator là thư viện đồng bộ (blocking) -> chạy trong thread riêng
    # để không chặn event loop của bot
    return await asyncio.to_thread(_translate)


async def get_or_create_webhook(channel: discord.TextChannel) -> discord.Webhook:
    if channel.id in _webhook_cache:
        return _webhook_cache[channel.id]

    # Tìm webhook cũ do bot tạo trước đó (đặt tên cố định để nhận diện lại sau khi restart)
    webhooks = await channel.webhooks()
    for wh in webhooks:
        if wh.name == "auto-translate-relay" and wh.user and wh.user.id == bot.user.id:
            _webhook_cache[channel.id] = wh
            _own_webhook_ids.add(wh.id)
            return wh

    wh = await channel.create_webhook(name="auto-translate-relay")
    _webhook_cache[channel.id] = wh
    _own_webhook_ids.add(wh.id)
    return wh


@bot.event
async def on_ready():
    log.info(f"Đã đăng nhập: {bot.user} (ID: {bot.user.id})")
    log.info("Bot đã sẵn sàng dịch tự động.")


@bot.event
async def on_message(message: discord.Message):
    # Bỏ qua DM, tin không có nội dung text (chỉ có ảnh/embed), hoặc kênh không thuộc danh sách
    if message.guild is None:
        return
    if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
        return
    if not should_process_channel(message.channel.id):
        return

    # Chống loop: bỏ qua tin nhắn do CHÍNH webhook dịch của bot này gửi ra
    if message.webhook_id is not None and message.webhook_id in _own_webhook_ids:
        return

    content = message.content
    if not content or not content.strip():
        # Vẫn cho phép xử lý các lệnh của bot framework (nếu có)
        await bot.process_commands(message)
        return

    lang = detect_lang(content)
    if lang is None:
        return  # không phải en/vi rõ ràng -> để nguyên, không đụng vào

    target = "vi" if lang == "en" else "en"

    try:
        translated = await translate_text(content, source=lang, target=target)
    except Exception as e:
        log.warning(f"Dịch lỗi, giữ nguyên tin gốc: {e}")
        return

    if translated.strip().lower() == content.strip().lower():
        return  # dịch ra giống hệt gốc (vd. toàn tên riêng) -> không cần thay

    try:
        webhook = await get_or_create_webhook(message.channel)

        display_name = message.author.display_name
        avatar_url = message.author.display_avatar.url

        # Gửi bản dịch, giả danh đúng tên + avatar người/bot gửi gốc
        kwargs = dict(
            content=translated,
            username=display_name,
            avatar_url=avatar_url,
        )
        if isinstance(message.channel, discord.Thread):
            await webhook.send(thread=message.channel, **kwargs)
        else:
            await webhook.send(**kwargs)

        await message.delete()

    except discord.Forbidden:
        log.error(
            "Thiếu quyền! Cần cấp Manage Messages + Manage Webhooks cho bot trong kênh này."
        )
    except discord.HTTPException as e:
        log.error(f"Lỗi Discord API: {e}")

    await bot.process_commands(message)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Chưa có DISCORD_TOKEN trong file .env")
    bot.run(TOKEN)
