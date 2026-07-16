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

# Từ điển thuật ngữ riêng cho Mudae/anime — Google Translate hay dịch các từ
# này theo nghĩa phổ thông sai hoàn toàn ngữ cảnh game (vd. "roll" -> "cuộn",
# "character" -> "ký tự" như trong lập trình). Danh sách này ép các từ khớp
# LUÔN được thay bằng đúng nghĩa tiếng Việt trong ngữ cảnh Mudae, không đưa
# cho Google Translate tự đoán. Muốn thêm/sửa từ nào cứ bổ sung vào đây,
# KHÔNG cần sửa chỗ nào khác trong code.
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


# Tên các "tier" huy hiệu kakera của Mudae — emoji của chúng đặt tên dính liền
# không cách dạng "bronzeiii", "silveriv"... Nếu chỉ .capitalize() nguyên xi sẽ
# ra "Bronzeiii" xấu và bị dịch sai (phần số La Mã dính liền không được dịch).
# Tách riêng phần chữ + số La Mã ra để hiển thị đúng "Bronze III".
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


# Sau khi in đậm tên emoji (vd. "**Bronze III**"), Mudae hay có sẵn 1 đoạn chữ
# LẶP LẠI y hệt ngay phía sau (vd. "**Bronze III** Bronze III") vì bản thân
# emoji đã mang nghĩa "Bronze III" rồi. Dò và bỏ bớt phần lặp lại thừa này,
# chỉ giữ lại bản in đậm.
_DEDUP_EMOJI_LABEL_PATTERN = re.compile(r"\*\*([^*\n]{2,40})\*\*(\s+)\1\b", re.IGNORECASE)


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
    text = _DEDUP_EMOJI_LABEL_PATTERN.sub(lambda m: f"**{m.group(1)}**", text)
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


async def _translate_raw(text: str, retries: int = 2) -> str:
    """Dịch qua Google Translate. Có retry với backoff ngắn vì dịch vụ free
    thỉnh thoảng chặn/timeout tạm thời khi gọi liên tiếp nhiều lần — trước đây
    không retry, hễ lỗi 1 phát là bỏ luôn đoạn đó, giữ nguyên tiếng Anh -> đây
    là 1 phần nguyên nhân gây cảm giác "dịch thiếu" khi embed có nhiều đoạn."""
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
    """Tạm thay các đoạn không được đụng vào (tên lệnh $xxxx, link) bằng
    1 ký tự Unicode "Private Use Area" (U+E000 trở đi) trước khi dịch.

    Trước đây dùng placeholder dạng chữ "XTOKENX0XTOKENX" — nhưng chữ TOKEN
    là từ tiếng Anh có nghĩa thật, nên Google Translate thỉnh thoảng tự dịch/
    chèn khoảng trắng vào giữa nó, khiến bước khôi phục sau đó không khớp lại
    được nữa -> lệnh bị hỏng cú pháp. Ký tự Private Use Area không thuộc từ
    điển ngôn ngữ nào cả, nên các dịch vụ dịch máy luôn giữ nguyên y hệt,
    không có lý do gì để "dịch" hay chỉnh sửa nó.

    LƯU Ý: GLOSSARY (roll/nhân vật/bắt...) KHÔNG áp dụng ở đây — phải để SAU
    bước in đậm emoji (_stylize_broken_emoji), gọi bằng _apply_glossary() ở
    bước riêng, nếu không glossary sẽ "ăn mất" chữ nằm giữa 2 dấu ":" (vd.
    ":kakera:") trước khi bước in đậm kịp nhận diện, khiến emoji đó bị bỏ
    sót, không in đậm được nữa.
    """
    placeholders: dict[str, str] = {}

    def _replace(pattern: re.Pattern, s: str, override: str | None = None) -> str:
        def _sub(m: re.Match) -> str:
            key = chr(0xE000 + len(placeholders))
            placeholders[key] = override if override is not None else m.group(0)
            return key
        return pattern.sub(_sub, s)

    text = _replace(COMMAND_TOKEN_PATTERN, text)
    text = _replace(URL_PATTERN, text)
    return text, placeholders


def _apply_glossary(text: str, placeholders: dict[str, str]) -> str:
    """Áp dụng từ điển thuật ngữ GLOSSARY, dùng chung 1 dict placeholder với
    _protect_tokens (counter tính theo len(placeholders) nên không bao giờ
    trùng key). Gọi hàm này SAU _stylize_broken_emoji."""
    def _replace(pattern: re.Pattern, s: str, override: str) -> str:
        def _sub(m: re.Match) -> str:
            key = chr(0xE000 + len(placeholders))
            placeholders[key] = override
            return key
        return pattern.sub(_sub, s)

    for pattern, vi_term in GLOSSARY:
        text = _replace(pattern, text, vi_term)
    return text


def _restore_tokens(text: str, placeholders: dict[str, str]) -> tuple[str, bool]:
    """Khôi phục lại token gốc. Trả về (text, ok) — ok=False nếu có ít nhất
    1 placeholder không tìm thấy trong bản dịch (nghĩa là dịch vụ dịch máy đã
    làm mất/hỏng nó) -> gọi nơi khác sẽ HUỶ bản dịch, giữ nguyên văn gốc thay
    vì gửi ra 1 câu có cú pháp lệnh/link bị vỡ."""
    ok = True
    for key, original in placeholders.items():
        if key not in text:
            ok = False
            continue
        text = text.replace(key, original)
    return text, ok


async def process_text(text: str) -> tuple[str, bool]:
    """Xử lý 1 đoạn text: dịch Anh->Việt nếu hợp lệ + thay MỌI emoji (thật lẫn
    bị vỡ) bằng **Tên** in đậm, LUÔN bảo vệ tên lệnh "$xxxx" + link TRƯỚC khi
    chạm vào text.

    LƯU Ý QUAN TRỌNG: hàm này KHÔNG kiểm tra "cả đoạn có phải 1 tin nhắn lệnh
    hay không" (is_command_message) — việc đó chỉ áp dụng đúng 1 lần cho tin
    nhắn gốc ở on_message(). Nếu áp dụng lại ở đây, một field embed dạng
    "$pokerank: Global top 100..." (bắt đầu bằng tên lệnh nhưng thực chất là
    cả 1 đoạn mô tả dài) sẽ bị coi nhầm là "tin nhắn lệnh" và bỏ qua dịch toàn
    bộ câu phía sau -> đây chính là nguyên nhân bug "dịch không hết" trước đây.
    Việc bảo vệ riêng từng "$tenlenh" nằm lẫn trong câu đã được COMMAND_TOKEN_PATTERN
    lo rồi, nên không cần (và không nên) chặn cả câu ở đây nữa.

    Emoji thật <:ten:id> cũng bị thay bằng chữ đậm thay vì giữ nguyên icon:
    đây là giới hạn CỨNG của nền tảng Discord (đã xác minh qua tài liệu chính
    thức discord-api-docs) — webhook do bot tạo chỉ hiển thị được icon của
    emoji thuộc CHÍNH server mà bot đó là thành viên; với emoji từ server khác
    (như kho emoji riêng của Mudae), Discord tự viết đè nội dung tin nhắn
    thành dạng chữ thô ":ten:" ngay ở tầng API, xảy ra SAU khi bot đã gửi đi —
    không có cách nào ở phía code để giữ được icon thật trong trường hợp này.
    Thay chữ đậm chủ động giúp hiển thị ổn định, nhất quán, thay vì để Discord
    tự cắt ra một kết quả xấu không kiểm soát được.

    Trả về (text sau xử lý, có thay đổi so với gốc hay không)."""
    if not text:
        return text, False

    # 1) Bảo vệ tên lệnh "$xxxx" + link trước tiên (không đụng vào khi dịch/stylize)
    protected_text, placeholders = _protect_tokens(text)

    # 2) Thay mọi emoji (thật <:ten:id> lẫn bị vỡ ":ten:" thô) bằng **Tên**
    #    in đậm NGAY TỪ ĐÂY, TRƯỚC khi dịch. Nếu để dịch trước rồi mới
    #    stylize, Google Translate có thể chèn khoảng trắng/đổi hoa-thường
    #    quanh dấu ":", khiến regex nhận diện emoji vỡ không còn khớp được
    #    nữa -> chữ thô ":ten:" lọt qua, không được in đậm.
    working = _stylize_broken_emoji(protected_text)

    # 2b) Áp dụng từ điển thuật ngữ GLOSSARY — PHẢI làm sau bước in đậm emoji
    #     ở trên (xem ghi chú trong _protect_tokens/_apply_glossary).
    working = _apply_glossary(working, placeholders)

    # 3) Dịch (nếu hợp lệ) trên bản đã stylize + bảo vệ token
    if is_translatable_candidate(text):
        try:
            translated = await _translate_raw(working)
            if translated.strip().lower() != working.strip().lower():
                # Chỉ chấp nhận bản dịch nếu khôi phục được ĐẦY ĐỦ mọi token
                # đã bảo vệ (lệnh/link) — nếu dịch vụ dịch máy làm mất 1 token
                # nào đó, HUỶ bản dịch này, giữ nguyên bản gốc thay vì gửi ra
                # câu có cú pháp lệnh/link bị vỡ.
                restored, ok = _restore_tokens(translated, placeholders)
                if ok:
                    final = restored
                    return final, final != text
                else:
                    log.warning("Khôi phục token sau dịch thất bại, giữ nguyên đoạn gốc (đã stylize).")
        except Exception as e:
            log.warning(f"Dịch lỗi, giữ nguyên đoạn gốc: {e}")

    # 4) Không dịch (hoặc dịch thất bại/không an toàn) -> vẫn khôi phục token
    #    trên bản working (chỉ mới được stylize, chưa dịch) để trả về đúng
    final, _ok = _restore_tokens(working, placeholders)
    return final, final != text


_BATCH_SEP = "\uE100"  # Ký tự Private-Use-Area dùng làm dấu phân cách khi gộp
# nhiều đoạn text lại dịch chung 1 lần gọi API. Cùng loại ký tự "vô hình" với
# ký tự bảo vệ token ở _protect_tokens nên dịch máy không đụng vào được.


async def _translate_batch(texts: list[str]) -> tuple[list[str] | None, bool]:
    """Dịch NHIỀU đoạn text trong ĐÚNG 1 lần gọi API (gộp lại bằng ký tự phân
    cách đặc biệt) thay vì gọi API riêng cho từng đoạn.

    Lý do cần hàm này: 1 embed help của Mudae có thể có 20-30 field, trước đây
    mỗi field gọi API dịch riêng -> bắn 20-30 request liên tiếp trong vài giây.
    Google Translate (bản free, không key) rất hay chặn/timeout khi bị gọi dồn
    dập kiểu này; các field bị chặn giữa chừng sẽ lặng lẽ giữ nguyên tiếng Anh
    (đúng cơ chế an toàn đã thiết kế), nhưng nhìn từ phía người dùng thì thành
    ra "dịch thiếu". Gộp lại 1 lần gọi giúp giảm hẳn số request -> giảm hẳn
    khả năng bị chặn giữa chừng.

    Trả về (list kết quả theo đúng thứ tự, batch có thực sự dịch được không).
    Nếu vì lý do gì đó việc tách lại theo dấu phân cách bị lệch số lượng (hiếm,
    nhưng để an toàn tuyệt đối không được để lộn nội dung field này sang field
    khác) -> trả về (None, False), nơi gọi sẽ tự dịch lại từng phần riêng lẻ."""
    if not texts:
        return [], False

    combined = _BATCH_SEP.join(texts)
    protected, placeholders = _protect_tokens(combined)
    working = _stylize_broken_emoji(protected)
    working = _apply_glossary(working, placeholders)

    try:
        translated = await _translate_raw(working)
    except Exception as e:
        log.warning(f"Dịch gộp lỗi, giữ nguyên (đã stylize): {e}")
        restored, _ok = _restore_tokens(working, placeholders)
        parts = restored.split(_BATCH_SEP)
        if len(parts) != len(texts):
            return None, False
        return parts, False

    restored, ok = _restore_tokens(translated, placeholders)
    parts = restored.split(_BATCH_SEP)
    if not ok or len(parts) != len(texts):
        return None, False
    return parts, True



async def translate_embed(embed: discord.Embed) -> tuple[discord.Embed, bool]:
    """Dịch mọi phần chữ dịch được trong 1 embed, giữ nguyên ảnh/màu/link.
    Gộp tất cả các đoạn CẦN dịch (title/description/footer/author/mỗi field)
    thành 1 lần gọi API duy nhất thay vì gọi riêng cho từng phần — xem giải
    thích chi tiết trong docstring của _translate_batch()."""
    changed = False
    data = embed.to_dict()

    # Thu thập tất cả "vị trí" có chữ trong embed, giữ tham chiếu (container,
    # key) để gán kết quả ngược lại đúng chỗ sau khi dịch xong.
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
            # Không cần gọi API dịch (đã tiếng Việt / không có chữ cái) -> vẫn
            # xử lý emoji vỡ + bảo vệ token cho phần này, không tốn request
            styled, did_change = await process_text(val)
            results[i] = styled
            if did_change:
                changed = True

    if batch_texts:
        parts, translated_ok = await _translate_batch(batch_texts)
        if parts is None:
            # Gộp bị lệch số lượng (hiếm) -> an toàn tuyệt đối: dịch lại từng
            # phần riêng lẻ như cách cũ, không được để lộn nội dung giữa các field
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

    # Tin nhắn có nút bấm / menu chọn (vd. EPIC RPG hỏi "Bạn muốn tìm hiểu...").
    # QUAN TRỌNG: Discord chỉ định tuyến sự kiện bấm nút về ĐÚNG bot/application
    # đã tạo ra tin nhắn đó. Webhook không phải là 1 application có thể nhận sự
    # kiện tương tác, nên KHÔNG THỂ "vẽ lại" các nút này qua webhook rồi cho nó
    # hoạt động được -> bấm vào sẽ báo lỗi "This interaction failed". Vì vậy khi
    # tin nhắn có components, ta giữ nguyên bản gốc (để nút còn bấm được), chỉ
    # gửi kèm 1 bản dịch riêng để đọc, không xóa gì cả.
    has_components = bool(getattr(message, "components", None))

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

        # Giữ lại file đính kèm gốc (nếu có), vd ảnh nhân vật gửi trực tiếp.
        # Chỉ tải lại file khi KHÔNG có nút bấm (nếu có nút, giữ nguyên tin gốc
        # nên không cần gửi lại file, tránh tải trùng lãng phí).
        files = []
        if message.attachments and not has_components:
            try:
                files = [await a.to_file() for a in message.attachments]
            except (discord.HTTPException, discord.NotFound) as e:
                log.warning(f"Không tải lại được file đính kèm: {e}")

        if has_components:
            # Chỉ gửi bản dịch làm tin PHỤ, kèm ghi chú, KHÔNG xóa tin gốc để
            # nút bấm trên tin gốc vẫn dùng được bình thường.
            note = "*(Bản dịch — bấm nút ở tin nhắn gốc phía trên nhé)*"
            final_content = f"{final_content}\n{note}" if final_content else note
            kwargs = dict(username=display_name, avatar_url=avatar_url, content=final_content)
            if new_embeds:
                kwargs["embeds"] = new_embeds
            if isinstance(message.channel, discord.Thread):
                await webhook.send(thread=message.channel, **kwargs)
            else:
                await webhook.send(**kwargs)
            # KHÔNG xóa message gốc trong trường hợp này.
            await bot.process_commands(message)
            return

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
