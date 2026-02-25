import os
import hashlib
import unicodedata
from pathlib import Path
from urllib.parse import urlparse, quote

import aiohttp
import emoji as emoji_lib

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools, register
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

# GitHub proxy mirrors for raw.githubusercontent.com
GITHUB_MIRROR_PREFIXES = [
    "https://ghfast.top/",
    "https://gh-proxy.com/",
    "https://mirror.ghproxy.com/",
]

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB


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


def _url_to_cache_filename(url: str) -> str:
    """Convert URL to a safe cache filename using hash."""
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    _, ext = os.path.splitext(urlparse(url).path)
    ext = ext.lower()
    if ext not in (".png", ".gif", ".webp", ".jpg", ".jpeg"):
        ext = ".gif"
    return f"{url_hash}{ext}"


def _build_mirror_urls(url: str) -> list[str]:
    """Build a list of mirror URLs for fallback download."""
    urls = [url]
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if "githubusercontent.com" in host:
        for prefix in GITHUB_MIRROR_PREFIXES:
            urls.append(f"{prefix}{url}")
    elif "gstatic.com" in host:
        stripped = url.replace("https://", "")
        urls.extend([
            f"https://i0.wp.com/{stripped}",
            f"https://wsrv.nl/?url={stripped}",
            f"https://images.weserv.nl/?url={stripped}",
        ])

    return urls


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
        self.session: aiohttp.ClientSession | None = None
        data_dir = StarTools.get_data_dir("astrbot_plugin_animatedemoji")
        self._data_dir = Path(data_dir)
        self._img_dir = self._data_dir / "images"
        self._img_dir.mkdir(parents=True, exist_ok=True)

    async def _ensure_session(self):
        """Ensure aiohttp session is available."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

    async def _download_image(self, url: str) -> str | None:
        """Download image with mirror fallback, return local path or None."""
        filename = _url_to_cache_filename(url)
        local_path = self._img_dir / filename

        if local_path.exists():
            return str(local_path)

        await self._ensure_session()
        mirror_urls = _build_mirror_urls(url)
        timeout = aiohttp.ClientTimeout(total=15, connect=5)
        tmp_path = str(local_path) + ".tmp"

        for mirror_url in mirror_urls:
            try:
                async with self.session.get(
                    mirror_url, timeout=timeout
                ) as resp:
                    resp.raise_for_status()

                    total_size = 0
                    with open(tmp_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(65536):
                            if not chunk:
                                continue
                            total_size += len(chunk)
                            if total_size > MAX_IMAGE_BYTES:
                                raise ValueError(
                                    f"response too large (>{MAX_IMAGE_BYTES} bytes)"
                                )
                            f.write(chunk)

                    if total_size < 100:
                        raise ValueError(
                            f"response too small ({total_size} bytes)"
                        )

                    os.replace(tmp_path, str(local_path))
                    logger.info(
                        "AnimatedEmoji: downloaded from %s",
                        mirror_url.split("?")[0].split("/")[2],
                    )
                    return str(local_path)
            except Exception as e:
                logger.warning(
                    "AnimatedEmoji: download failed from %s: %s",
                    mirror_url.split("?")[0].split("/")[2]
                    if "/" in mirror_url
                    else mirror_url,
                    e,
                )
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

        logger.error("AnimatedEmoji: all sources failed for %s", filename)
        return None

    async def _get_telegram_url(self, emoji_str: str) -> str | None:
        """Search for the Telegram animated emoji across all categories."""
        name = _emoji_to_name(emoji_str)
        if not name:
            return None

        await self._ensure_session()
        timeout = aiohttp.ClientTimeout(total=10, connect=5)

        # Build candidate URLs for all categories
        candidates = []
        for category in TELEGRAM_CATEGORIES:
            url = (
                f"{TELEGRAM_BASE_URL}"
                f"/{quote(category)}/{quote(name)}.webp"
            )
            candidates.append(url)

        # Try original URLs first
        got_any_response = False
        for url in candidates:
            try:
                async with self.session.head(url, timeout=timeout) as resp:
                    got_any_response = True
                    if resp.status == 200:
                        return url
            except Exception:
                continue

        # If we got at least one response (e.g. 404), emoji doesn't exist
        if got_any_response:
            return None

        # All failed with connection errors — try mirror prefixes
        for prefix in GITHUB_MIRROR_PREFIXES:
            for url in candidates:
                mirror_url = f"{prefix}{url}"
                try:
                    async with self.session.head(
                        mirror_url, timeout=timeout
                    ) as resp:
                        if resp.status == 200:
                            return url  # return original URL for downloading
                except Exception:
                    continue

        return None

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

            local_path = await self._download_image(url)
            if not local_path:
                yield event.plain_result(
                    f"未找到该 emoji 的 Noto 动态版本或下载失败: {emoji_char}"
                )
                return

            chain = [
                Comp.Plain(f"{emoji_char} 的 Noto 动态版本：\n"),
                Comp.Image.fromFileSystem(local_path),
            ]
            yield event.chain_result(chain)

        elif source == "tg":
            url = await self._get_telegram_url(emoji_char)
            if not url:
                yield event.plain_result(
                    f"未找到该 emoji 的 Telegram 动态版本: {emoji_char}"
                )
                return

            logger.info(f"Telegram animated emoji URL: {url}")

            local_path = await self._download_image(url)
            if not local_path:
                yield event.plain_result(
                    f"Telegram 动态 emoji 下载失败: {emoji_char}"
                )
                return

            chain = [
                Comp.Plain(f"{emoji_char} 的 Telegram 动态版本：\n"),
                Comp.Image.fromFileSystem(local_path),
            ]
            yield event.chain_result(chain)

    async def terminate(self):
        """Clean up when plugin is unloaded."""
        if self.session and not self.session.closed:
            await self.session.close()
