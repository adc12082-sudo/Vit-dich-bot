""
Discord Auto-Translate Bot (Anh -> Việt, dịch mọi thứ dịch được từ BOT khác)
------------------------------------------------------------------------------
- Dịch TẤT CẢ nội dung tiếng Anh dịch được từ BOT KHÁC (vd. Mudae) sang tiếng Việt
- Giữ nguyên tên bot gốc, avatar gốc, hình ảnh, file đính kèm, màu sắc embed.
- Tích hợp lệnh !toggledich để bật/tắt nhanh cơ chế dịch tại kênh bất kỳ.
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

# Bộ nhớ tạm để quản lý việc bật/tắt dịch tự động tại các kênh chat
# Nếu file .env có điền TRANSLATE_CHANNEL_IDS, đó sẽ là danh sách kênh mặc định ban đầu.
DEFAULT_CHANNELS = {int(x) for x in CHANNEL_IDS_RAW.split(",") if x.strip()} if CHANNEL_IDS_RAW else set()
TRANSLATE_ALL_BY_DEFAULT = len(DEFAULT_CHANNELS) == 0

# Biến toàn cục để theo dõi trạng thái bật/tắt động trong lúc bot chạy
# (Người dùng gõ lệnh !toggledich sẽ trực tiếp thay đổi trạng thái trong này)
ACTIVE_CHANNELS = DEFAULT_CHANNELS.copy()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("translate-bot")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.webhooks = True

bot = commands.Bot(command_prefix="!", intents=intents)

_webhook_cache: dict[int, discord.Webhook] = {}
_own_webhook_ids: set[int] = set()

URL_PATTERN = re.compile(r"https?://\S+")
CUSTOM_EMOJI_PATTERN = re.compile(r"<a?:(\w+):(\d+)>")
BROKEN_EMOJI_PATTERN = re.compile(r":([a-zA-Z_]{2,32}):")
COMMAND_TOKEN_PATTERN = re.compile(r"\$\w+")

# Từ điển thuật ngữ tối ưu cho game Gacha/Anime
GLOSSARY: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bcharacters?\b", re.IGNORECASE), "nhân vật"),
    (re.compile(r"\brolls?\b", re.IGNORECASE), "roll"),
    (re.compile(r"\brolled\b", re.IGNORECASE), "roll"),
    (re.compile(r"\brolling\b", re.IGNORECASE), "roll"),
    (re.compile(r"\bclaims?\b", re.IGNORECASE), "bắt"),
    (re.compile(r"\bclaimed\b", re.IGNORECASE), "bắt"),
    (re.compile(r"\bwish\s*list(s)?\b", re.IGNORECASE), "danh sách yêu thích"),
    (re.compile(r"\bharem\b", re.IGNORECASE), "harem"),
    (re.compile(r"\bkeys?\b", re.IGNORECASE), "chìa khóa"),
    (re.compile(r"\bslots?\b", re.IGNORECASE), "lượt"),
    (re.compile(r"\bmarr(y|ied|iage)\b", re.IGNORECASE), "cưới"),
    (re.compile(r"\bwaifu(s)?\b", re.IGNORECASE), "waifu"),
    (re.compile(r"\bhusbando(s)?\b", re.IGNORECASE), "husbando"),
    (re.compile(r"\bkakera\b", re.IGNORECASE), "kakera"),
]

_TIER_BASES = ("bronze", "silver", "gold", "sapphire", "ruby", "emerald", "diamond", "amethyst", "topaz")
_ROMAN_MAP = {"i": "I", "ii": "II", "iii": "III", "iv": "IV", "v": "V"}

def _format_emoji_label(name: str) -> str:
    lower = name.lower()
    for base in _TIER_BASES:
        if lower.startswith(base):
            suffix = lower[len(base):]
            if suffix in _ROMAN_MAP:
                return f"{base.capitalize()} {_ROMAN_MAP[suffix]}"
    return name.capitalize()

def _bold_emoji_name(name: str, text: str, start: int) -> str:
    prefix = " " if start > 0 and text[start - 1] not in (" ", "\n") else ""
    return f"{prefix}**{_format_emoji_label(name)}**"

_DEDUP_EMOJI_LABEL_PATTERN = re.compile(r"\*\*([^*\n]{2,40})\*\*(\s+)\1\b", re.IGNORECASE)

def _stylize_broken_emoji(text: str) -> str:
    if not text:
        return text
    text = CUSTOM_EMOJI_PATTERN.sub(
        lambda m: _bold_emoji_name(m.group(1), text, m.start()), text
    )
    text = BROKEN_EMOJI_PATTERN.sub(
        lambda m: _bold_emoji_name(m.group(1), text, m.start()), text
    )
    text = _DEDUP_EMOJI_LABEL_PATTERN.sub(lambda m: f"**{m.group(1)}**", text)
    return text

COMMAND_PREFIXES = "$!.~->?/+*;%^&=:"
VIETNAMESE_CHARS = re.compile(
    "[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡ"
    "ùúụủũưừứựửữỳýỵỷỹđ]",
    re.IGNORECASE,
)

def is_channel_translation_active(channel_id: int) -> bool:
    """Kiểm tra xem kênh hiện tại có được phép chạy dịch tự động hay không."""
    if TRANSLATE_ALL_BY_DEFAULT:
        # Mặc định dịch tất cả kênh, trừ những kênh bị tắt (nằm trong ACTIVE_CHANNELS dưới dạng black-list)
        return channel_id not in ACTIVE_CHANNELS
    else:
        # Chỉ dịch những kênh nằm trong ACTIVE_CHANNELS (white-list)
        return channel_id in ACTIVE_CHANNELS

def is_command_message(content: str) -> bool:
    text = content.strip()
    if len(text) < 2:
        return False
    first, second = text[0], text[1]
    if first in COMMAND_PREFIXES and (second.isalnum() or second == "_"):
        return True
    return False

def is_translatable_candidate(text: str) -> bool:
    if text is None:
        return False
    stripped = URL_PATTERN.sub("", text).strip()
    if len(stripped) < 2:
        return False
    if VIETNAMESE_CHARS.search(stripped):
        return False
    if not re.search(r"[A-Za-z]", stripped):
        return False
    return True

MAX_CHUNK_CHARS = 4500

def _split_into_chunks(text: str, max_len: int = MAX_CHUNK_CHARS) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    current = ""
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
            for sentence in re.split(r"(?<=[.!?]) ", line):
                sub_candidate = f"{current} {sentence}".strip() if current else sentence
                if len(sub_candidate) <= max_len:
                    current = sub_candidate
                else:
                    if current:
                        chunks.append(current)
                    current = sentence[:max_len]
    if current:
        chunks.append(current)
    return chunks

async def _translate_raw(text: str, retries: int = 2) -> str:
    def _translate(chunk: str):
        return GoogleTranslator(source="en", target="vi").translate(chunk)

    chunks = _split_into_chunks(text)

    async def _translate_chunk_with_retry(chunk: str) -> str:
        last_err = None
        for attempt in range(retries + 1):
            try:
                return await asyncio.to_thread(_translate, chunk)
            except Exception as e:
                last_err = e
                if attempt < retries:
                    await asyncio.sleep(0.6 * (attempt + 1))
        raise last_err

    if len(chunks) == 1:
        return await _translate_chunk_with_retry(chunks[0])

    translated_parts = []
    for chunk in chunks:
        translated_parts.append(await _translate_chunk_with_retry(chunk))
    return "\n".join(translated_parts)

def _protect_tokens(text: str) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}
    def _replace(pattern: re.Pattern, s: str, override: str | None = None) -> str:
        def _sub(m: re.Match) -> str:
            key = f"$zqx{len(placeholders)}qxz"
            placeholders[key] = override if override is not None else m.group(0)
            return key
        return pattern.sub(_sub, s)

    text = _replace(COMMAND_TOKEN_PATTERN, text)
    text = _replace(URL_PATTERN, text)
    return text, placeholders

def _apply_glossary(text: str, placeholders: dict[str, str]) -> str:
    def _replace(pattern: re.Pattern, s: str, override: str) -> str:
        def _sub(m: re.Match) -> str:
            key = f"$zqx{len(placeholders)}qxz"
            placeholders[key] = override
            return key
        return pattern.sub(_sub, s)

    for pattern, vi_term in GLOSSARY:
        text = _replace(pattern, text, vi_term)
    return text

def _restore_tokens(text: str, placeholders: dict[str, str]) -> tuple[str, bool]:
    ok = True
    for key, original in placeholders.items():
        if key not in text:
            ok = False
            continue
        text = text.replace(key, original)
    return text, ok

async def process_text(text: str) -> tuple[str, bool]:
    if not text:
        return text, False

    protected_text, placeholders = _protect_tokens(text)
    working = _stylize_broken_emoji(protected_text)
    working = _apply_glossary(working, placeholders)

    if is_translatable_candidate(text):
        for attempt in range(3):
            try:
                translated = await _translate_raw(working)
            except Exception as e:
                log.warning(f"Dịch lỗi (lần {attempt + 1}/3), giữ nguyên đoạn gốc: {e}")
                break
            if translated.strip().lower() == working.strip().lower():
                break
            restored, ok = _restore_tokens(translated, placeholders)
            if ok:
                return restored, restored != text
            log.warning(f"Khôi phục token sau dịch thất bại (lần {attempt + 1}/3), thử lại.")

    final, _ok = _restore_tokens(working, placeholders)
    return final, final != text

_BATCH_SEP = "$zqxsepqxz"

async def _translate_batch(texts: list[str]) -> tuple[list[str] | None, bool]:
    if not texts:
        return [], False

    combined = _BATCH_SEP.join(texts)
    protected, placeholders = _protect_tokens(combined)
    working = _stylize_broken_emoji(protected)
    working = _apply_glossary(working, placeholders)

    for attempt in range(3):
        try:
            translated = await _translate_raw(working)
        except Exception as e:
            log.warning(f"Dịch gộp lỗi (lần {attempt + 1}/3): {e}")
            continue
        restored, ok = _restore_tokens(translated, placeholders)
        parts = restored.split(_BATCH_SEP)
        if ok and len(parts) == len(texts):
            return parts, True
        log.warning(f"Khôi phục token dịch gộp thất bại (lần {attempt + 1}/3), thử lại.")

    restored, _ok = _restore_tokens(working, placeholders)
    parts = restored.split(_BATCH_SEP)
    if len(parts) != len(texts):
        return None, False
    return parts, False

async def translate_embed(embed: discord.Embed) -> tuple[discord.Embed, bool]:
    changed = False
    data = embed.to_dict()
    slots: list[tuple[dict, str, str]] = []

    def register(container: dict | None, key: str):
        if container and container.get(key):
            slots.append((container, key, container[key]))

    register(data, "title")
    register(data, "description")
    register(data.get("footer"), "text")
    register(data.get("author"), "name")
    for field in data.get("fields", []):
        register(field, "name")
        register(field, "value")

    if not slots:
        return embed, False

    results: list[str | None] = [None] * len(slots)
    batch_idx: list[int] = []
    batch_texts: list[str] = []

    for i, (_container, _key, val) in enumerate(slots):
        if is_translatable_candidate(val):
            batch_idx.append(i)
            batch_texts.append(val)
        else:
            styled, did_change = await process_text(val)
            results[i] = styled
            if did_change:
                changed = True

    if batch_texts:
        parts, translated_ok = await _translate_batch(batch_texts)
        if parts is None:
            log.warning("Tách kết quả dịch gộp bị lệch, chuyển sang dịch từng phần riêng lẻ.")
            for idx in batch_idx:
                r, did_change = await process_text(slots[idx][2])
                results[idx] = r
                if did_change:
                    changed = True
        else:
            for j, idx in enumerate(batch_idx):
                results[idx] = parts[j]
                if translated_ok and parts[j] != slots[idx][2]:
                    changed = True

    for (container, key, _orig), result in zip(slots, results):
        container[key] = result

    return discord.Embed.from_dict(data), changed

async def get_or_create_webhook(channel: discord.TextChannel) -> discord.Webhook:
    if channel.id in _webhook_cache:
        return _webhook_cache[channel.id]

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

# ================= LỆNH BẬT / TẮT DỊCH CHO NGƯỜI DÙNG =================
@bot.command(name="toggledich")
async def toggle_dich(ctx: commands.Context):
    """Bật hoặc tắt chức năng tự động dịch tại kênh hiện tại (Áp dụng cho tất cả mọi người)."""
    channel_id = ctx.channel.id
    
    if TRANSLATE_ALL_BY_DEFAULT:
        # Nếu mặc định là dịch tất cả, ACTIVE_CHANNELS hoạt động như một Blacklist (danh sách tắt)
        if channel_id in ACTIVE_CHANNELS:
            ACTIVE_CHANNELS.remove(channel_id)
            status = "🟢 **BẬT**"
        else:
            ACTIVE_CHANNELS.add(channel_id)
            status = "🔴 **TẮT**"
    else:
        # Nếu mặc định chỉ dịch kênh chỉ định, ACTIVE_CHANNELS hoạt động như một Whitelist (danh sách bật)
        if channel_id in ACTIVE_CHANNELS:
            ACTIVE_CHANNELS.remove(channel_id)
            status = "🔴 **TẮT**"
        else:
            ACTIVE_CHANNELS.add(channel_id)
            status = "🟢 **BẬT**"
            
    await ctx.send(f"{status} tự động dịch cho kênh này!", delete_after=10)


# ================= SỰ KIỆN CHÍNH =================
@bot.event
async def on_ready():
    log.info(f"Đã đăng nhập: {bot.user} (ID: {bot.user.id})")
    log.info("Bot dịch tự động đã sẵn sàng hoạt động.")

@bot.event
async def on_message(message: discord.Message):
    if message.guild is None:
        return
    if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
        return

    # Luôn ưu tiên xử lý các lệnh từ người dùng (như !toggledich) trước tiên
    await bot.process_commands(message)

    # Chống vòng lặp: bỏ qua tin nhắn do chính các webhook của bot này tạo ra
    if message.webhook_id is not None and message.webhook_id in _own_webhook_ids:
        return

    # YÊU CẦU: Chỉ dịch bot khác. Bỏ qua hoàn toàn tin nhắn từ người dùng thực.
    if not message.author.bot:
        return

    # YÊU CẦU: Có thể tạm dừng dịch ở kênh hiện tại thông qua lệnh toggle
    if not is_channel_translation_active(message.channel.id):
        return

    # Giữ nguyên tuyệt đối các tin nhắn lệnh (vd. $mn, !help, .roll) của các bot khác
    if is_command_message(message.content):
        return

    # YÊU CẦU: Dịch tự động và dịch toàn bộ nội dung từ ngắn đến dài của Bot khác
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

    # Không có bất kỳ thay đổi nào để dịch -> giữ nguyên trạng, không đụng vào
    if not content_changed and not embeds_changed:
        return  

    has_components = bool(getattr(message, "components", None))

    try:
        webhook = await get_or_create_webhook(message.channel)
        display_name = message.author.display_name
        avatar_url = message.author.display_avatar.url

        files = []
        if message.attachments and not has_components:
            try:
                files = [await a.to_file() for a in message.attachments]
            except (discord.HTTPException, discord.NotFound) as e:
                log.warning(f"Không tải lại được file đính kèm: {e}")

        # YÊU CẦU: Dịch cả nút (nhưng webhook bị giới hạn API không giữ tương tác được)
        # -> Giải pháp tối ưu: Nếu có nút, giữ nguyên tin gốc để người dùng thao tác bấm. 
        # Gửi thêm bản dịch sạch đè ngay dưới không có bất kỳ ký hiệu thừa nào.
        if has_components:
            kwargs = dict(username=display_name, avatar_url=avatar_url)
            if final_content:
                kwargs["content"] = final_content
            if new_embeds:
                kwargs["embeds"] = new_embeds

            if isinstance(message.channel, discord.Thread):
                await webhook.send(thread=message.channel, **kwargs)
            else:
                await webhook.send(**kwargs)
            return

        # YÊU CẦU: Ghi bản dịch đè lên tin nhắn gốc & dịch không dấu vết (không hiện chú thích dịch)
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
            pass

    except discord.Forbidden:
        log.error("Thiếu quyền! Cần cấp Manage Messages + Manage Webhooks cho bot trong kênh.")
    except discord.HTTPException as e:
        log.error(f"Lỗi Discord API: {e}")

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Chưa có DISCORD_TOKEN trong file .env")
    bot.run(TOKEN)
    
