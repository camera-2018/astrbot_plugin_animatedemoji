import unicodedata
from urllib.parse import quote

import httpx
import emoji as emoji_lib

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

NOTO_BASE_URL = "https://fonts.gstatic.com/s/e/notoemoji/latest"

TELEGRAM_BASE_URL = (
    "https://raw.githubusercontent.com"
    "/Tarikul-Islam-Anik/Telegram-Animated-Emojis/main"
)
TELEGRAM_CATEGORIES = [
    "Smileys",
    "People",
    "Animals and Nature",
    "Food and Drink",
    "Activity",
    "Travel and Places",
    "Objects",
    "Symbols",
    "Flags",
]


def _extract_emoji(text: str) -> str | None:
    """Extract the first emoji from the text using the emoji library."""
    text = text.strip()
    if not text:
        return None
    found = emoji_lib.emoji_list(text)
    if not found:
        return None
    return found[0]["emoji"]


def _emoji_to_noto_codepoints(emoji_str: str) -> str:
    """Convert an emoji string to Noto URL codepoint format."""
    codepoints = []
    for char in emoji_str:
        cp = ord(char)
        if cp == 0xFE0F:
            continue
        codepoints.append(format(cp, "x"))
    return "_".join(codepoints)


def _emoji_to_name(emoji_str: str) -> str | None:
    """Convert an emoji to its human-readable name in title case."""
    # Try emoji library first (handles compound emojis)
    demojized = emoji_lib.demojize(emoji_str, language="en")
    if demojized != emoji_str and demojized.startswith(":") and demojized.endswith(":"):
        name = demojized[1:-1].replace("_", " ").title()
        return name
    # Fallback to unicodedata
    try:
        if len(emoji_str) == 1 or (
            len(emoji_str) == 2 and ord(emoji_str[1]) == 0xFE0F
        ):
            char = emoji_str[0]
            name = unicodedata.name(char)
            return name.title()
    except ValueError:
        pass
    return None


def _get_noto_url(emoji_str: str) -> str:
    """Build the Noto animated emoji GIF URL."""
    codepoints = _emoji_to_noto_codepoints(emoji_str)
    return f"{NOTO_BASE_URL}/{codepoints}/512.gif"


async def _get_telegram_url(emoji_str: str) -> str | None:
    """Search for the Telegram animated emoji across all categories."""
    name = _emoji_to_name(emoji_str)
    if not name:
        return None

    async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
        for category in TELEGRAM_CATEGORIES:
            url = (
                f"{TELEGRAM_BASE_URL}"
                f"/{quote(category)}/{quote(name)}.webp"
            )
            try:
                resp = await client.head(url)
                if resp.status_code == 200:
                    return url
            except httpx.HTTPError:
                continue
    return None


@register(
    "astrbot_plugin_animatedemoji",
    "camera-2018",
    "将静态 emoji 转换为动态 emoji，支持 Noto 和 Telegram 两种方案",
    "1.0.0",
    "https://github.com/camera-2018/astrbot_plugin_animatedemoji",
)
class AnimatedEmojiPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.command("animoji")
    async def animoji(self, event: AstrMessageEvent):
        """将 emoji 转换为动态版本。用法: /animoji [noto|tg] <emoji>"""
        text = event.message_str.strip()

        if not text:
            yield event.plain_result(
                "用法: /animoji [noto|tg] <emoji>\n"
                "示例:\n"
                "  /animoji 😀        (默认使用 noto)\n"
                "  /animoji noto 😀   (Google Noto 动态)\n"
                "  /animoji tg 😀     (Telegram 动态)"
            )
            return

        # Parse source type
        source = "noto"
        parts = text.split(maxsplit=1)
        if parts[0].lower() in ("noto", "tg"):
            source = parts[0].lower()
            text = parts[1] if len(parts) > 1 else ""
        emoji_char = _extract_emoji(text)
        if not emoji_char:
            yield event.plain_result("未检测到有效的 emoji，请输入一个 emoji 表情。")
            return

        if source == "noto":
            url = _get_noto_url(emoji_char)
            logger.info(f"Noto animated emoji URL: {url}")
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=10
            ) as client:
                try:
                    resp = await client.head(url)
                    if resp.status_code != 200:
                        yield event.plain_result(
                            f"未找到该 emoji 的 Noto 动态版本: {emoji_char}"
                        )
                        return
                except httpx.HTTPError as e:
                    logger.error(f"Noto request error: {e}")
                    yield event.plain_result(f"请求 Noto 动态 emoji 失败: {e}")
                    return

            chain = [
                Comp.Plain(f"{emoji_char} 的 Noto 动态版本：\n"),
                Comp.Image.fromURL(url),
            ]
            yield event.chain_result(chain)

        elif source == "tg":
            url = await _get_telegram_url(emoji_char)
            if not url:
                yield event.plain_result(
                    f"未找到该 emoji 的 Telegram 动态版本: {emoji_char}"
                )
                return

            logger.info(f"Telegram animated emoji URL: {url}")
            chain = [
                Comp.Plain(f"{emoji_char} 的 Telegram 动态版本：\n"),
                Comp.Image.fromURL(url),
            ]
            yield event.chain_result(chain)
