"""
Discord Auto-Translate Bot (Anh -> Việt, dịch mọi thứ dịch được từ BOT khác)
------------------------------------------------------------------------------
- Dịch TẤT CẢ nội dung tiếng Anh dịch được từ BOT KHÁC (vd. Mudae) sang tiếng Việt:
    + Tin nhắn chữ thường
    + Cả nội dung bên trong khung ảnh (embed): tiêu đề, mô tả, từng field, footer, author
- KHÔNG đụng vào tin nhắn của người dùng thật (bỏ qua hoàn toàn)
- KHÔNG dịch, KHÔNG xóa bất kỳ "tin nhắn lệnh" nào (tin bắt đầu bằng ký tự lệnh
  như $, !, ., ~, -, >, ?, /... ví dụ "$mn", "!help", ".roll") — áp dụng chung
  cho mọi bot, để không làm hỏng chức năng game
- Giữ nguyên ảnh/màu sắc/link trong embed, chỉ thay phần chữ
- Xóa tin nhắn gốc, gửi lại bản dịch qua Webhook giả danh đúng tên + avatar
  của bot gửi gốc (vd. vẫn hiện tên "Mudae"), kèm file đính kèm nếu có
- Áp dụng cho TOÀN SERVER theo mặc định (mọi kênh text)

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

# Các ký tự lệnh phổ biến của bot Discord (Mudae mặc định dùng "$", nhưng server
# có thể đổi prefix khác, và nhiều bot khác dùng !, ., ~, -, >, ? ...).
# Bất kỳ tin nhắn nào bắt đầu bằng 1 trong các ký tự này + ký tự chữ/số ngay sau
# đều được coi là "tin nhắn lệnh" và sẽ được GIỮ NGUYÊN, không dịch không xóa.
COMMAND_PREFIXES = "$!.~->?/+*;%^&=:"

# Các ký tự có dấu tiếng Việt, dùng để nhận biết nhanh 1 đoạn văn đã là tiếng Việt
VIETNAMESE_CHARS = re.compile(
    "[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡ"
    "ùúụủũưừứựửữỳýỵỷỹđ]",
    re.IGNORECASE,
)


def should_process_channel(channel_id: int) -> bool:
    if TRANSLATE_CHANNEL_IDS is None:
        return True
    return channel_id in TRANSLATE_CHANNEL_IDS


def is_command_message(content: str) -> bool:
    """True nếu tin nhắn là một lệnh (vd. '$mn', '!help', '.roll')."""
    text = content.strip()
    if len(text) < 2:
        return False
    first, second = text[0], text[1]
    if first in COMMAND_PREFIXES and (second.isalnum() or second == "_"):
        return True
    return False


def is_translatable_candidate(text: str) -> bool:
    """Lọc nhanh: đoạn text có đáng để gọi API dịch hay không."""
    if text is None:
        return False
    stripped = URL_PATTERN.sub("", text).strip()
    if len(stripped) < 2:
        return False
    if VIETNAMESE_CHARS.search(stripped):
        return False  # đã là tiếng Việt rồi, khỏi dịch
    if not re.search(r"[A-Za-z]", stripped):
        return False  # không có chữ cái nào (toàn số/emoji/ký hiệu) -> không dịch
    return True


MAX_CHUNK_CHARS = 4500  # dưới ngưỡng ~5000 ký tự/lần gọi của Google Translate


def _split_into_chunks(text: str, max_len: int = MAX_CHUNK_CHARS) -> list[str]:
    """Chia văn bản dài thành các đoạn nhỏ hơn max_len, cắt theo dòng/câu
    để không cắt giữa từ, tránh dịch sai nghĩa."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""
    # Ưu tiên cắt theo dòng trước, nếu 1 dòng vẫn quá dài thì cắt theo câu
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= max_len:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(line) <= max_len:
            current = line
        else:
            # Dòng quá dài -> cắt theo câu
            for sentence in re.split(r"(?<=[.!?]) ", line):
                sub_candidate = f"{current} {sentence}".strip() if current else sentence
                if len(sub_candidate) <= max_len:
                    current = sub_candidate
                else:
                    if current:
                        chunks.append(current)
                    current = sentence[:max_len]  # phòng hờ 1 câu vẫn quá dài
    if current:
        chunks.append(current)
    return chunks


async def _translate_raw(text: str) -> str:
    def _translate(chunk: str):
        return GoogleTranslator(source="en", target="vi").translate(chunk)

    chunks = _split_into_chunks(text)
    if len(chunks) == 1:
        # deep_translator là thư viện đồng bộ (blocking) -> chạy trong thread riêng
        # để không chặn event loop của bot
        return await asyncio.to_thread(_translate, chunks[0])

    translated_parts = []
    for chunk in chunks:
        translated_parts.append(await asyncio.to_thread(_translate, chunk))
    return "\n".join(translated_parts)


async def translate_maybe(text: str) -> str | None:
    """Dịch text Anh -> Việt nếu hợp lệ. Trả None nếu không cần/không dịch được."""
    if not text or is_command_message(text) or not is_translatable_candidate(text):
        return None
    try:
        translated = await _translate_raw(text)
    except Exception as e:
        log.warning(f"Dịch lỗi, giữ nguyên đoạn gốc: {e}")
        return None
    if translated.strip().lower() == text.strip().lower():
        return None  # dịch ra giống hệt gốc (vd. tên riêng) -> không cần thay
    return translated


async def translate_embed(embed: discord.Embed) -> tuple[discord.Embed, bool]:
    """Dịch mọi phần chữ dịch được trong 1 embed, giữ nguyên ảnh/màu/link."""
    changed = False
    data = embed.to_dict()

    async def tr(value):
        nonlocal changed
        result = await translate_maybe(value)
        if result is not None:
            changed = True
            return result
        return value

    if data.get("title"):
        data["title"] = await tr(data["title"])
    if data.get("description"):
        data["description"] = await tr(data["description"])
    if data.get("footer", {}).get("text"):
        data["footer"]["text"] = await tr(data["footer"]["text"])
    if data.get("author", {}).get("name"):
        data["author"]["name"] = await tr(data["author"]["name"])
    for field in data.get("fields", []):
        if field.get("name"):
            field["name"] = await tr(field["name"])
        if field.get("value"):
            field["value"] = await tr(field["value"])

    return discord.Embed.from_dict(data), changed


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
    if message.guild is None:
        return
    if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
        return
    if not should_process_channel(message.channel.id):
        return

    # Chống loop: bỏ qua tin nhắn do CHÍNH webhook dịch của bot này gửi ra
    if message.webhook_id is not None and message.webhook_id in _own_webhook_ids:
        return

    # CHỈ xử lý tin nhắn từ BOT KHÁC (vd. Mudae). Bỏ qua hoàn toàn tin nhắn
    # do người dùng thật gửi.
    if not message.author.bot:
        await bot.process_commands(message)
        return

    # "Tin nhắn lệnh" -> giữ nguyên tuyệt đối, không đụng vào
    if is_command_message(message.content):
        return

    translated_content = None
    if message.content and message.content.strip():
        translated_content = await translate_maybe(message.content)

    new_embeds: list[discord.Embed] = []
    embeds_changed = False
    for embed in message.embeds:
        new_embed, changed = await translate_embed(embed)
        new_embeds.append(new_embed)
        if changed:
            embeds_changed = True

    if translated_content is None and not embeds_changed:
        return  # không có gì để dịch -> giữ nguyên tin gốc, không đụng vào

    final_content = translated_content if translated_content is not None else message.content

    try:
        webhook = await get_or_create_webhook(message.channel)

        display_name = message.author.display_name
        avatar_url = message.author.display_avatar.url

        # Giữ lại file đính kèm gốc (nếu có), vd ảnh nhân vật gửi trực tiếp
        files = []
        if message.attachments:
            try:
                files = [await a.to_file() for a in message.attachments]
            except (discord.HTTPException, discord.NotFound) as e:
                log.warning(f"Không tải lại được file đính kèm: {e}")

        kwargs = dict(username=display_name, avatar_url=avatar_url)
        if final_content:
            kwargs["content"] = final_content
        if new_embeds:
            kwargs["embeds"] = new_embeds
        if files:
            kwargs["files"] = files

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
