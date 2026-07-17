"""
Discord Auto-Translate Bot (Anh -> Việt) - Phiên bản Hoàn Chỉnh
------------------------------------------------------------------------------
- Chống lặp tin nhắn 100% (Cache + Webhook Blocker).
- Mở rộng từ điển dịch thuật cho Anime/Gacha/Visual Novel.
- Tích hợp Trạng thái hoạt động (Rich Presence).
- [MỚI] Bộ mẫu câu tự nhiên (template) cho các câu Mudae hay lặp lại, ưu tiên
  dùng TRƯỚC máy dịch -> đọc mượt như người viết, không còn giọng "dịch máy".
- [MỚI] Glossary phụ nạp từ file glossary.json, có lệnh !themtu để thêm từ
  ngay trong Discord, không cần sửa code/deploy lại.
"""

import os
import re
import json
import asyncio
import logging
from collections import deque

import discord
from discord.ext import commands
from dotenv import load_dotenv
from deep_translator import GoogleTranslator

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_IDS_RAW = os.getenv("TRANSLATE_CHANNEL_IDS", "").strip()

DEFAULT_CHANNELS = {int(x) for x in CHANNEL_IDS_RAW.split(",") if x.strip()} if CHANNEL_IDS_RAW else set()
TRANSLATE_ALL_BY_DEFAULT = len(DEFAULT_CHANNELS) == 0
ACTIVE_CHANNELS = DEFAULT_CHANNELS.copy()

_processed_messages = deque(maxlen=200)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("translate-bot")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.webhooks = True

bot = commands.Bot(command_prefix="!", intents=intents)

_webhook_cache: dict[int, discord.Webhook] = {}

URL_PATTERN = re.compile(r"https?://\S+")
CUSTOM_EMOJI_PATTERN = re.compile(r"<a?:(\w+):(\d+)>")
BROKEN_EMOJI_PATTERN = re.compile(r":([a-zA-Z_]{2,32}):")
COMMAND_TOKEN_PATTERN = re.compile(r"\$\w+")

GLOSSARY: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bbronze\b", re.IGNORECASE), "Đồng"),
    (re.compile(r"\bsilver\b", re.IGNORECASE), "Bạc"),
    (re.compile(r"\bgold\b", re.IGNORECASE), "Vàng"),
    (re.compile(r"\bsapphire\b", re.IGNORECASE), "Sapphire"),
    (re.compile(r"\bruby\b", re.IGNORECASE), "Ruby"),
    (re.compile(r"\bemerald\b", re.IGNORECASE), "Emerald"),
    (re.compile(r"\bdiamond\b", re.IGNORECASE), "Kim cương"),

    (re.compile(r"\balready\s*claimed\s*rolls?\b", re.IGNORECASE), "các lượt roll đã có chủ"),
    (re.compile(r"\balready\s*claimed\s*characters?\b", re.IGNORECASE), "nhân vật đã có chủ"),
    (re.compile(r"\bwish\s*list\s*slots?\b", re.IGNORECASE), "lượt danh sách yêu thích"),
    (re.compile(r"\bwish\s*list\s*rolls?\b", re.IGNORECASE), "lượt roll wishlist"),
    (re.compile(r"wishlist\s*rolls?", re.IGNORECASE), "lượt roll wishlist"),
    (re.compile(r"wishlist\s*spawn\s*rate", re.IGNORECASE), "tỷ lệ xuất hiện wishlist"),
    (re.compile(r"kakera\s*power\s*consumption", re.IGNORECASE), "mức tiêu thụ năng lượng kakera"),
    (re.compile(r"\bpower\s*badges?\b", re.IGNORECASE), "huy hiệu sức mạnh"),
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
    (re.compile(r"\blike\s*rank\b", re.IGNORECASE), "thứ hạng yêu thích"),
    (re.compile(r"\bcharacter\s*claims?\b", re.IGNORECASE), "lượt bắt nhân vật"),
    (re.compile(r"\bpity\b", re.IGNORECASE), "bảo hiểm (pity)"),

    (re.compile(r"\bsanity\b", re.IGNORECASE), "điểm lý trí (sanity)"),
    (re.compile(r"\boperator(s)?\b", re.IGNORECASE), "Toán viên"),
    (re.compile(r"\btrainer(s)?\b", re.IGNORECASE), "Huấn luyện viên"),
    (re.compile(r"\borundum\b", re.IGNORECASE), "Đá đỏ (Orundum)"),
    (re.compile(r"\broute(s)?\b", re.IGNORECASE), "tuyến truyện (route)"),
    (re.compile(r"\bcanon\b", re.IGNORECASE), "chính thức (canon)"),
]

GLOSSARY_FILE = "glossary.json"
_extra_glossary_raw: dict[str, str] = {}
EXTRA_GLOSSARY: list[tuple[re.Pattern, str]] = []


def _compile_extra_glossary() -> None:
    global EXTRA_GLOSSARY
    compiled = []
    for phrase, vi_term in _extra_glossary_raw.items():
        escaped = re.escape(phrase.strip()).replace(r"\ ", r"\s*")
        pattern = re.compile(rf"\b{escaped}\b", re.IGNORECASE)
        compiled.append((pattern, vi_term))
    compiled.sort(key=lambda item: len(item[1]), reverse=True)
    EXTRA_GLOSSARY = compiled


def _load_extra_glossary() -> None:
    global _extra_glossary_raw
    if os.path.exists(GLOSSARY_FILE):
        try:
            with open(GLOSSARY_FILE, "r", encoding="utf-8") as f:
                _extra_glossary_raw = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Không đọc được {GLOSSARY_FILE}, dùng glossary rỗng: {e}")
            _extra_glossary_raw = {}
    else:
        _extra_glossary_raw = {}
        _save_extra_glossary()
    _compile_extra_glossary()


def _save_extra_glossary() -> None:
    try:
        with open(GLOSSARY_FILE, "w", encoding="utf-8") as f:
            json.dump(_extra_glossary_raw, f, ensure_ascii=False, indent=2, sort_keys=True)
    except OSError as e:
        log.warning(f"Không ghi được {GLOSSARY_FILE}: {e}")


_load_extra_glossary()

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


def _bold_emoji_name(name: str, text: str, start: int, match_len: int) -> str:
    prefix = " " if start > 0 and text[start - 1] not in (" ", "\n") else ""
    end_idx = start + match_len
    suffix = " " if end_idx < len(text) and text[end_idx].isalnum() else ""
    return f"{prefix}**{_format_emoji_label(name)}**{suffix}"


_DEDUP_EMOJI_LABEL_PATTERN = re.compile(r"\*\*([^*\n]{2,40})\*\*(\s+)(?:\*\*)?\1(?:\*\*)?(?!\w)", re.IGNORECASE)


def _stylize_broken_emoji(text: str) -> str:
    if not text:
        return text
    text = CUSTOM_EMOJI_PATTERN.sub(
        lambda m: _bold_emoji_name(m.group(1), text, m.start(), len(m.group(0))), text
    )
    text = BROKEN_EMOJI_PATTERN.sub(
        lambda m: _bold_emoji_name(m.group(1), text, m.start(), len(m.group(0))), text
    )
    text = _DEDUP_EMOJI_LABEL_PATTERN.sub(lambda m: f"**{m.group(1)}**", text)
    return text


TEMPLATE_RULES: list[tuple[re.Pattern, "callable"]] = [
    (
        re.compile(r"^(?P<user>.+?), you can claim right now! The next claim reset is in (?P<time>.+?)\.?$", re.IGNORECASE),
        lambda m: f"{m.group('user')}, bạn có thể bắt ngay bây giờ! Lần đặt lại tiếp theo sau {m.group('time')}.",
    ),
    (
        re.compile(r"^You have (?P<n>\d+) rolls? left\. Next roll reset in (?P<time>.+?)\.?$", re.IGNORECASE),
        lambda m: f"Bạn còn {m.group('n')} roll. Lần đặt lại roll tiếp theo sau {m.group('time')}.",
    ),
    (
        re.compile(r"^Your likelist must have at least (?P<n>\d+) characters? to use this command!?$", re.IGNORECASE),
        lambda m: f"Danh sách thích của bạn phải có ít nhất {m.group('n')} nhân vật để dùng lệnh này!",
    ),
    (
        re.compile(r"^You can react to kakera right now!?$", re.IGNORECASE),
        lambda m: "Bạn có thể phản ứng với kakera ngay bây giờ!",
    ),
    (
        re.compile(r"^Power:\s*(?P<n>\d+)%$", re.IGNORECASE),
        lambda m: f"Sức mạnh: {m.group('n')}%",
    ),
    (
        re.compile(r"^Each kakera button consumes (?P<n>\d+)% of your reaction power\.?$", re.IGNORECASE),
        lambda m: f"Mỗi nút kakera tiêu thụ {m.group('n')}% năng lượng phản ứng của bạn.",
    ),
    (
        re.compile(r"^Your characters? with (?P<n>\d+)\+ keys? consumes? half the power \((?P<m>\d+)%\)\.?$", re.IGNORECASE),
        lambda m: f"Nhân vật của bạn có từ {m.group('n')} chìa khóa trở lên sẽ tiêu thụ một nửa năng lượng ({m.group('m')}%).",
    ),
    (
        re.compile(r"^Stock:\s*(?P<n>[\d.,]+)\s*kakera$", re.IGNORECASE),
        lambda m: f"Kho: {m.group('n')} kakera",
    ),
    (
        re.compile(r"^You may vote right now!?$", re.IGNORECASE),
        lambda m: "Bạn có thể bình chọn ngay bây giờ!",
    ),
    (
        re.compile(r"^(?P<cmd>\$\w+) is ready!?$", re.IGNORECASE),
        lambda m: f"{m.group('cmd')} đã sẵn sàng!",
    ),
    (
        re.compile(r"^You (?:can|may) (?P<action>\w+) right now!?$", re.IGNORECASE),
        lambda m: f"Bạn có thể {m.group('action')} ngay bây giờ!",
    ),
]


def _apply_templates(text: str) -> list[tuple[str, bool]]:
    lines = text.split("\n")
    result: list[tuple[str, bool]] = []
    for line in lines:
        stripped = line.strip()
        leading_ws = line[: len(line) - len(line.lstrip())]
        matched = False
        if stripped:
            for pattern, formatter in TEMPLATE_RULES:
                m = pattern.match(stripped)
                if m:
                    try:
                        vi_line = formatter(m)
                    except Exception:
                        continue
                    result.append((leading_ws + vi_line, True))
                    matched = True
                    break
        if not matched:
            result.append((line, False))
    return result


COMMAND_PREFIXES = "$!.~->?/+*;%^&=:"
VIETNAMESE_CHARS = re.compile(
    "[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡ"
    "ùúụủũưừứựửữỳýỵỷỹđ]",
    re.IGNORECASE,
)


def is_channel_translation_active(channel_id: int) -> bool:
    if TRANSLATE_ALL_BY_DEFAULT:
        return channel_id not in ACTIVE_CHANNELS
    else:
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
    for pattern, vi_term in EXTRA_GLOSSARY:
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


def _fix_markdown_spaces(text: str) -> str:
    if not text:
        return text
    text = re.sub(r'([^\s(\[{\'"])\s*(\*\*[^*]+\*\*)', r'\1 \2', text)
    text = re.sub(r'(\*\*[^*]+\*\*)\s*([^\s.,!?:;\])}\'"])', r'\1 \2', text)
    return text


async def _translate_pipeline(text: str) -> tuple[str, bool]:
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
                log.warning(f"Dịch lỗi (lần {attempt + 1}/3), giữ nguyên: {e}")
                break
            if translated.strip().lower() == working.strip().lower():
                break
            restored, ok = _restore_tokens(translated, placeholders)
            if ok:
                final = _fix_markdown_spaces(restored)
                return final, final != text
            log.warning(f"Khôi phục token thất bại (lần {attempt + 1}/3).")

    final, _ok = _restore_tokens(working, placeholders)
    final = _fix_markdown_spaces(final)
    return final, final != text


async def process_text(text: str) -> tuple[str, bool]:
    if not text:
        return text, False

    lines_info = _apply_templates(text)

    if not any(done for _, done in lines_info):
        return await _translate_pipeline(text)

    pending_lines = [ln for ln, done in lines_info if not done]
    translated_lines: list[str] = []
    if pending_lines:
        joined_pending = "\n".join(pending_lines)
        translated_joined, _ = await _translate_pipeline(joined_pending)
        translated_lines = translated_joined.split("\n")
        while len(translated_lines) < len(pending_lines):
            translated_lines.append(pending_lines[len(translated_lines)])
        translated_lines = translated_lines[: len(pending_lines)]

    final_lines = []
    idx = 0
    for ln, done in lines_info:
        if done:
            final_lines.append(ln)
        else:
            final_lines.append(translated_lines[idx])
            idx += 1
    final = "\n".join(final_lines)
    return final, final != text


_BATCH_SEP = "$zqxsepqxz"


async def _translate_batch(texts: list[str]) -> tuple[list[str] | None, bool]:
    if not texts:
        return [], False

    per_text_lines = [_apply_templates(t) for t in texts]
    any_templated = any(any(done for _, done in li) for li in per_text_lines)

    if not any_templated:
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
                return [_fix_markdown_spaces(p) for p in parts], True

        restored, _ok = _restore_tokens(working, placeholders)
        parts = restored.split(_BATCH_SEP)
        if len(parts) != len(texts):
            return None, False
        return [_fix_markdown_spaces(p) for p in parts], False

    results = []
    any_changed = False
    for t in texts:
        r, changed = await process_text(t)
        results.append(r)
        if changed:
            any_changed = True
    return results, any_changed


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
            re
