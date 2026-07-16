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

DEFAULT_CHANNELS = {int(x) for x in CHANNEL_IDS_RAW.split(",") if x.strip()} if CHANNEL_IDS_RAW else set()
TRANSLATE_ALL_BY_DEFAULT = len(DEFAULT_CHANNELS) == 0

ACTIVE_CHANNELS = DEFAULT_CHANNELS.copy()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("translate-bot")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.webhooks = True

bot = commands.Bot(command_prefix="!", intents=intents)

_webhook_cache: dict[int, discord.Webhook] = {}

URL_PATTERN = re.compile(r"https?://\S+")
COMMAND_TOKEN_PATTERN = re.compile(r"\$\w+")

GLOSSARY: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bwish\s*list\s*rolls?\b", re.IGNORECASE), "lượt roll wishlist"),
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

COMMAND_PREFIXES = "$!.~->?/+*;%^&=:"
VIETNAMESE_CHARS = re.compile("[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.IGNORECASE)

def is_channel_translation_active(channel_id: int) -> bool:
    return (channel_id not in ACTIVE_CHANNELS) if TRANSLATE_ALL_BY_DEFAULT else (channel_id in ACTIVE_CHANNELS)

def is_command_message(content: str) -> bool:
    text = content.strip()
    return len(text) >= 2 and text[0] in COMMAND_PREFIXES and (text[1].isalnum() or text[1] == "_")

def is_translatable_candidate(text: str) -> bool:
    if not text: return False
    stripped = URL_PATTERN.sub("", text).strip()
    return len(stripped) >= 2 and not VIETNAMESE_CHARS.search(stripped) and re.search(r"[A-Za-z]", stripped)

async def _translate_raw(text: str) -> str:
    return await asyncio.to_thread(GoogleTranslator(source="en", target="vi").translate, text)

def _protect_tokens(text: str) -> tuple[str, dict[str, str]]:
    placeholders = {}
    def _sub(m: re.Match) -> str:
        key = f"$zqx{len(placeholders)}qxz"
        placeholders[key] = m.group(0)
        return key
    
    # Protect Emojis, URLs, Commands
    text = re.compile(r"<a?:\w+:\d+>|:[a-zA-Z0-9_]+:|<[@#t][^>]+>|\$\w+|https?://\S+").sub(_sub, text)
    return text, placeholders

async def process_text(text: str) -> tuple[str, bool]:
    if not text: return text, False
    protected, placeholders = _protect_tokens(text)
    try:
        translated = await _translate_raw(protected)
        for k, v in placeholders.items(): translated = translated.replace(k, v)
        return translated, translated != text
    except: return text, False

async def translate_embed(embed: discord.Embed) -> tuple[discord.Embed, bool]:
    d = embed.to_dict()
    changed = False
    
    async def _handle(val):
        nonlocal changed
        res, did = await process_text(val)
        if did: changed = True
        return res

    if 'title' in d: d['title'] = await _handle(d['title'])
    if 'description' in d: d['description'] = await _handle(d['description'])
    if 'fields' in d:
        for f in d['fields']:
            f['name'] = await _handle(f['name'])
            f['value'] = await _handle(f['value'])
    return discord.Embed.from_dict(d), changed

async def get_or_create_webhook(channel: discord.TextChannel) -> discord.Webhook:
    if channel.id in _webhook_cache: return _webhook_cache[channel.id]
    whs = await channel.webhooks()
    for wh in whs:
        if wh.name == "auto-translate-relay":
            _webhook_cache[channel.id] = wh
            return wh
    wh = await channel.create_webhook(name="auto-translate-relay")
    _webhook_cache[channel.id] = wh
    return wh

@bot.command(name="toggledich")
async def toggle_dich(ctx: commands.Context):
    cid = ctx.channel.id
    if cid in ACTIVE_CHANNELS:
        ACTIVE_CHANNELS.remove(cid)
        await ctx.send("🔴 Đã TẮT dịch tại kênh này!", delete_after=5)
    else:
        ACTIVE_CHANNELS.add(cid)
        await ctx.send("🟢 Đã BẬT dịch tại kênh này!", delete_after=5)

@bot.event
async def on_message(message: discord.Message):
    if message.author.id == bot.user.id or message.webhook_id is not None: return
    if message.guild and isinstance(message.channel, (discord.TextChannel, discord.Thread)):
        await bot.process_commands(message)
        if message.author.bot and is_channel_translation_active(message.channel.id) and not is_command_message(message.content):
            content, c_changed = await process_text(message.content) if message.content else (None, False)
            embeds, e_changed = [], False
            for e in message.embeds:
                ne, ch = await translate_embed(e)
                embeds.append(ne)
                if ch: e_changed = True
            
            if c_changed or e_changed:
                try:
                    wh = await get_or_create_webhook(message.channel)
                    kwargs = {"username": message.author.display_name, "avatar_url": message.author.display_avatar.url}
                    if content: kwargs["content"] = content
                    if embeds: kwargs["embeds"] = embeds
                    if message.attachments: kwargs["files"] = [await a.to_file() for a in message.attachments]
                    await wh.send(**kwargs)
                    await message.delete()
                except Exception as e: log.error(f"Error: {e}")

bot.run(TOKEN)
    
