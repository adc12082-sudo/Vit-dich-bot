"""
Discord Auto-Translate Bot (Anh -> Việt, dịch mọi thứ dịch được từ BOT khác)
------------------------------------------------------------------------------
- Dịch TẤT CẢ nội dung tiếng Anh dịch được từ BOT KHÁC (vd. Mudae) sang tiếng Việt:
    + Tin nhắn chữ thường
    + Cả nội dung bên trong khung ảnh (embed): tiêu đề, mô tả, từng field, footer, author
- KHÔNG đụng vào tin nhắn của người dùng thật (bỏ qua hoàn toàn)
- KHÔNG dịch, KHÔNG xóa bất kỳ "tin nhắn lệnh" nào (tin CẢ TIN NHẮN bắt đầu
  bằng ký tự lệnh như $, !, ., ~, -, >, ?, /... ví dụ "$mn", "!help", ".roll")
  — áp dụng chung cho mọi bot, để không làm hỏng chức năng game
- KHÔNG dịch tên lệnh dạng "$tenlenh" dù nó nằm LẪN TRONG một câu mô tả dài
  (vd. trong bảng hướng dẫn/help của bot khác) — trước đây đây là lỗi hay gặp
  nhất: dịch cả câu sẽ dịch luôn cả tên lệnh bên trong, khiến lệnh không còn
  dùng được (vd. "$noun" bị dịch thành "$danh từ")
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
# Emoji tùy chỉnh của Discord dạng <:tên:mãsố> hoặc <a:tên:mãsố> (animated).
# LƯU Ý: dù đây là emoji "thật" (còn nguyên id), Discord vẫn hiện thành chữ
# thô ":ten:" phía người xem nếu webhook/bot gửi lại KHÔNG có quyền
# "Use External Emojis" trong kênh đó (rất hay thiếu quyền này) — đây chính
# là nguyên nhân icon bị vỡ dù code đã "bảo vệ" nguyên vẹn thẻ emoji. Để
# không còn phụ thuộc vào quyền server nữa, ta chủ động thay LUÔN emoji thật
# này bằng chữ in đậm, y như cách xử lý emoji đã vỡ sẵn.
CUSTOM_EMOJI_PATTERN = re.compile(r"<a?:(\w+):(\d+)>")
# Discord tự cắt emoji ngoài server thành dạng chữ thô ":ten:" trước khi bot kịp
# nhận tin nhắn (giới hạn nền tảng, không sửa được). Regex này bắt các đoạn đó
# để thay bằng chữ in đậm, bỏ dấu hai chấm, cho nổi bật thay vì hiện thô.
BROKEN_EMOJI_PATTERN = re.compile(r":([a-zA-Z_]{2,32}):")

# Tên lệnh dạng "$tenlenh" (vd. $rdmpokemon, $cdn, $customcd, $beam...) xuất hiện
# BẤT KỲ ĐÂU trong văn bản (kể cả nằm giữa câu mô tả trong embed, không chỉ ở đầu
# tin nhắn). Đây chính là nguyên nhân gây lỗi "dịch luôn cả lệnh": trước đây bot
# chỉ bỏ qua dịch khi CẢ TIN NHẮN là một lệnh (vd. "$mn"), nhưng khi lệnh nằm
# lẫn trong một câu văn dài (vd. "Sử dụng $noun, $adverb... trong câu của bạn"),
# Google Translate vẫn dịch luôn cả "$noun" -> "$danh từ", khiến lệnh không còn
# dùng được. Regex này bắt riêng từng token "$xxxx" để giữ nguyên 100%, không
# phụ thuộc vị trí của nó trong văn bản.
COMMAND_TOKEN_PATTERN = re.compile(r"\$\w+")


def _bold_emoji_name(name: str, text: str, start: int) -> str:
    prefix = " " if start > 0 and text[start - 1] not in (" ", "\n") else ""
    return f"{prefix}**{name.capitalize()}**"


def _stylize_broken_emoji(text: str) -> str:
    """Thay TẤT CẢ emoji (thật <:ten:id> lẫn bị vỡ ':ten:') bằng **Tên** in đậm,
    để icon luôn hiển thị ổn định, không phụ thuộc quyền 'Use External Emojis'."""
    if not text:
        return text

    text = CUSTOM_EMOJI_PATTERN.sub(
        lambda m: _bold_emoji_name(m.group(1), text, m.start()), text
    )
    text = BROKEN_EMOJI_PATTERN.sub(
        lambda m: _bold_emoji_name(m.group(1), text, m.start()), text
    )
    return text

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


def _protect_tokens(text: str) -> tuple[str, dict[str, str]]:
    """Tạm thay các đoạn không được đụng vào (emoji tùy chỉnh, link) bằng
    placeholder trước khi dịch, để Google Translate không làm vỡ chúng."""
    placeholders: dict[str, str] = {}
    counter = 0

    def _replace(pattern: re.Pattern, s: str) -> str:
        nonlocal counter
        def _sub(m: re.Match) -> str:
            nonlocal counter
            key = f"XTOKENX{counter}XTOKENX"
            placeholders[key] = m.group(0)
            counter += 1
            return key
        return pattern.sub(_sub, s)

    # Bảo vệ tên lệnh "$xxxx" TRƯỚC (dù nằm ở đâu trong câu), rồi mới tới link,
    # để Google Translate không đụng được vào cú pháp lệnh của bot khác.
    text = _replace(COMMAND_TOKEN_PATTERN, text)
    text = _replace(URL_PATTERN, text)
    return text, placeholders


def _restore_tokens(text: str, placeholders: dict[str, str]) -> str:
    for key, original in placeholders.items():
        # Google Translate có thể đổi hoa/thường hoặc thêm khoảng trắng quanh token
        pattern = re.compile(re.escape(key), re.IGNORECASE)
        text = pattern.sub(lambda _m, o=original: o, text)
    return text


async def process_text(text: str) -> tuple[str, bool]:
    """Xử lý 1 đoạn text: dịch Anh->Việt nếu hợp lệ + thay MỌI emoji (thật lẫn
    bị vỡ) bằng **Tên** in đậm, LUÔN bảo vệ link TRƯỚC khi chạm vào text.
    Emoji thật <:ten:id> cũng bị thay bằng chữ đậm thay vì giữ nguyên icon,
    vì icon rất hay bị Discord hiện thành chữ thô ":ten:" khi webhook gửi lại
    thiếu quyền "Use External Emojis" — thay chữ đậm giúp hiển thị ổn định,
    không phụ thuộc quyền server nữa.
    Trả về (text sau xử lý, có thay đổi so với gốc hay không)."""
    if not text:
        return text, False
    if is_command_message(text):
        return text, False

    # 1) Bảo vệ link trước tiên (không đụng vào link khi dịch/stylize)
    protected_text, placeholders = _protect_tokens(text)

    # 2) Thay mọi emoji (thật <:ten:id> lẫn bị vỡ ":ten:" thô) bằng **Tên**
    #    in đậm NGAY TỪ ĐÂY, TRƯỚC khi dịch. Nếu để dịch trước rồi mới
    #    stylize, Google Translate có thể chèn khoảng trắng/đổi hoa-thường
    #    quanh dấu ":", khiến regex nhận diện emoji vỡ không còn khớp được
    #    nữa -> chữ thô ":ten:" lọt qua, không được in đậm.
    working = _stylize_broken_emoji(protected_text)

    # 3) Dịch (nếu hợp lệ) trên bản đã stylize + bảo vệ token
    if is_translatable_candidate(text):
        try:
            translated = await _translate_raw(working)
            if translated.strip().lower() != working.strip().lower():
                working = translated
        except Exception as e:
            log.warning(f"Dịch lỗi, giữ nguyên đoạn gốc: {e}")

    # 4) Khôi phục lại link nguyên vẹn
    final = _restore_tokens(working, placeholders)
    return final, final != text


async def translate_embed(embed: discord.Embed) -> tuple[discord.Embed, bool]:
    """Dịch mọi phần chữ dịch được trong 1 embed, giữ nguyên ảnh/màu/link."""
    changed = False
    data = embed.to_dict()

    async def tr(value):
        nonlocal changed
        new_value, did_change = await process_text(value)
        if did_change:
            changed = True
        return new_value

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

    final_content = message.content
    content_changed = False
    if message.content and message.content.strip():
        final_content, content_changed = await process_text(message.content)

    new_embeds: list[discord.Embed] = []
    embeds_changed = False
    for embed in message.embeds:
        new_embed, changed = await translate_embed(embed)
        new_embeds.append(new_embed)
        if changed:
            embeds_changed = True

    if not content_changed and not embeds_changed:
        return  # không có gì để dịch/sửa -> giữ nguyên tin gốc, không đụng vào

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

        try:
            await message.delete()
        except discord.NotFound:
            # Tin gốc đã bị xóa/sửa từ trước (vd. bot khác tự dọn tin) -> bỏ
            # qua, không phải lỗi thật, bản dịch đã gửi thành công rồi.
            pass

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
