"""
Animated Emoji Plugin - Unit Tests

Tests: helper functions, mirror URL building, download fallback, caching,
Telegram URL search with mirror fallback, session lifecycle, and terminate.
"""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ============================================================
# Mock astrbot & aiohttp setup (same pattern as emojikitchen)
# ============================================================

_fake_data_dir = Path(tempfile.mkdtemp(prefix="_animatedemoji_test_data_"))


class _FakeStar:
    def __init__(self, context):
        self.context = context


class _FakeContext:
    pass


_star_module = MagicMock()
_star_module.Star = _FakeStar
_star_module.Context = _FakeContext
_star_module.StarTools = MagicMock()
_star_module.StarTools.get_data_dir = MagicMock(return_value=str(_fake_data_dir))
_star_module.register = lambda *a, **kw: lambda cls: cls

_filter_mock = MagicMock()
_filter_mock.command = lambda *a, **kw: lambda fn: fn
_filter_mock.llm_tool = lambda *a, **kw: lambda fn: fn

_event_module = MagicMock()
_event_module.filter = _filter_mock
_event_module.AstrMessageEvent = MagicMock()

_MOCKS = {
    "aiohttp": MagicMock(),
    "astrbot": MagicMock(),
    "astrbot.api": MagicMock(),
    "astrbot.api.event": _event_module,
    "astrbot.api.event.filter": _filter_mock,
    "astrbot.api.star": _star_module,
    "astrbot.api.message_components": MagicMock(),
}

with patch.dict("sys.modules", _MOCKS):
    import main as _main_module
    from main import (
        _extract_emoji,
        _emoji_to_noto_codepoints,
        _emoji_to_name,
        _get_noto_url,
        _url_to_cache_filename,
        _build_mirror_urls,
        NOTO_BASE_URL,
        TELEGRAM_BASE_URL,
        GITHUB_MIRROR_PREFIXES,
        AnimatedEmojiPlugin,
    )


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def plugin(tmp_path):
    img_dir = tmp_path / "images"
    img_dir.mkdir()

    context = _FakeContext()
    p = AnimatedEmojiPlugin(context)
    p._data_dir = tmp_path
    p._img_dir = img_dir
    p.session = MagicMock()
    p.session.closed = False
    p.session.close = AsyncMock()
    return p


def _make_resp_mock(
    status=200,
    chunks=(b"\x89PNG\r\n\x1a\n" + b"x" * 100,),
    raise_on_raise_for_status=False,
):
    resp = MagicMock()
    resp.status = status
    if raise_on_raise_for_status:
        resp.raise_for_status = MagicMock(side_effect=Exception("HTTP error"))
    else:
        resp.raise_for_status = MagicMock()

    async def _iter_chunked(size):
        for chunk in chunks:
            yield chunk

    resp.content = MagicMock()
    resp.content.iter_chunked = _iter_chunked
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=None)
    return resp


# ============================================================
# Tests: Pure helper functions
# ============================================================

class TestExtractEmoji:
    def test_single_emoji(self):
        assert _extract_emoji("😀") == "😀"

    def test_emoji_with_text(self):
        assert _extract_emoji("hello 😀 world") == "😀"

    def test_no_emoji(self):
        assert _extract_emoji("hello world") is None

    def test_empty(self):
        assert _extract_emoji("") is None
        assert _extract_emoji("   ") is None


class TestNotoCodepoints:
    def test_simple_emoji(self):
        assert _emoji_to_noto_codepoints("😀") == "1f600"

    def test_strips_fe0f(self):
        assert "fe0f" not in _emoji_to_noto_codepoints("❤\uFE0F")


class TestEmojiToName:
    def test_known_emoji(self):
        name = _emoji_to_name("😀")
        assert name is not None
        assert isinstance(name, str)
        assert len(name) > 0

    def test_unknown_returns_none(self):
        assert _emoji_to_name("abc") is None


class TestGetNotoUrl:
    def test_url_format(self):
        url = _get_noto_url("😀")
        assert url == f"{NOTO_BASE_URL}/1f600/512.gif"


# ============================================================
# Tests: Cache filename
# ============================================================

class TestUrlToCacheFilename:
    def test_gif_extension(self):
        fn = _url_to_cache_filename("https://example.com/emoji/512.gif")
        assert fn.endswith(".gif")

    def test_webp_extension(self):
        fn = _url_to_cache_filename("https://example.com/emoji/name.webp")
        assert fn.endswith(".webp")

    def test_png_extension(self):
        fn = _url_to_cache_filename("https://example.com/emoji/name.png")
        assert fn.endswith(".png")

    def test_unknown_extension_defaults_gif(self):
        fn = _url_to_cache_filename("https://example.com/emoji/name.xyz")
        assert fn.endswith(".gif")

    def test_deterministic(self):
        url = "https://example.com/test.gif"
        assert _url_to_cache_filename(url) == _url_to_cache_filename(url)

    def test_different_urls_different_filenames(self):
        fn1 = _url_to_cache_filename("https://a.com/1.gif")
        fn2 = _url_to_cache_filename("https://a.com/2.gif")
        assert fn1 != fn2


# ============================================================
# Tests: Mirror URL building
# ============================================================

class TestBuildMirrorUrls:
    def test_github_mirrors(self):
        url = "https://raw.githubusercontent.com/user/repo/main/file.webp"
        mirrors = _build_mirror_urls(url)
        assert mirrors[0] == url
        assert len(mirrors) == 1 + len(GITHUB_MIRROR_PREFIXES)
        for prefix in GITHUB_MIRROR_PREFIXES:
            assert f"{prefix}{url}" in mirrors

    def test_gstatic_mirrors(self):
        url = "https://fonts.gstatic.com/s/e/notoemoji/latest/1f600/512.gif"
        mirrors = _build_mirror_urls(url)
        assert mirrors[0] == url
        assert len(mirrors) == 4  # original + 3 image proxies
        stripped = url.replace("https://", "")
        assert f"https://i0.wp.com/{stripped}" in mirrors
        assert f"https://wsrv.nl/?url={stripped}" in mirrors
        assert f"https://images.weserv.nl/?url={stripped}" in mirrors

    def test_unknown_host_no_mirrors(self):
        url = "https://example.com/image.png"
        mirrors = _build_mirror_urls(url)
        assert mirrors == [url]


# ============================================================
# Tests: Download with fallback
# ============================================================

class TestDownloadImage:
    @pytest.mark.asyncio
    async def test_returns_cached(self, plugin):
        url = "https://fonts.gstatic.com/s/e/notoemoji/latest/1f600/512.gif"
        filename = _url_to_cache_filename(url)
        cached = plugin._img_dir / filename
        cached.write_bytes(b"cached_data" * 20)

        result = await plugin._download_image(url)
        assert result == str(cached)
        # session.get should NOT be called for cached
        plugin.session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_success_first_mirror(self, plugin):
        url = "https://fonts.gstatic.com/s/e/notoemoji/latest/1f600/512.gif"
        resp_mock = _make_resp_mock()
        plugin.session.get = MagicMock(return_value=resp_mock)

        result = await plugin._download_image(url)
        assert result is not None
        assert os.path.exists(result)

    @pytest.mark.asyncio
    async def test_download_fallback_on_failure(self, plugin):
        url = "https://fonts.gstatic.com/s/e/notoemoji/latest/1f600/512.gif"

        fail_resp = _make_resp_mock(raise_on_raise_for_status=True)
        ok_resp = _make_resp_mock()

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return fail_resp
            return ok_resp

        plugin.session.get = MagicMock(side_effect=side_effect)

        result = await plugin._download_image(url)
        assert result is not None
        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_download_all_fail(self, plugin):
        url = "https://fonts.gstatic.com/s/e/notoemoji/latest/1f600/512.gif"
        fail_resp = _make_resp_mock(raise_on_raise_for_status=True)
        plugin.session.get = MagicMock(return_value=fail_resp)

        result = await plugin._download_image(url)
        assert result is None

    @pytest.mark.asyncio
    async def test_download_too_small(self, plugin):
        url = "https://fonts.gstatic.com/s/e/notoemoji/latest/1f600/512.gif"
        resp_mock = _make_resp_mock(chunks=(b"tiny",))
        plugin.session.get = MagicMock(return_value=resp_mock)

        result = await plugin._download_image(url)
        assert result is None

    @pytest.mark.asyncio
    async def test_tmp_file_cleaned_on_failure(self, plugin):
        url = "https://fonts.gstatic.com/s/e/notoemoji/latest/1f600/512.gif"
        fail_resp = _make_resp_mock(raise_on_raise_for_status=True)
        plugin.session.get = MagicMock(return_value=fail_resp)

        await plugin._download_image(url)

        # No .tmp files should remain
        tmp_files = list(plugin._img_dir.glob("*.tmp"))
        assert len(tmp_files) == 0


# ============================================================
# Tests: Telegram URL search with mirror fallback
# ============================================================

class TestGetTelegramUrl:
    @pytest.mark.asyncio
    async def test_found_in_original(self, plugin):
        ok_resp = MagicMock()
        ok_resp.status = 200
        ok_resp.__aenter__ = AsyncMock(return_value=ok_resp)
        ok_resp.__aexit__ = AsyncMock(return_value=None)

        plugin.session.head = MagicMock(return_value=ok_resp)

        result = await plugin._get_telegram_url("😀")
        assert result is not None
        assert TELEGRAM_BASE_URL in result

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self, plugin):
        not_found = MagicMock()
        not_found.status = 404
        not_found.__aenter__ = AsyncMock(return_value=not_found)
        not_found.__aexit__ = AsyncMock(return_value=None)

        plugin.session.head = MagicMock(return_value=not_found)

        result = await plugin._get_telegram_url("😀")
        assert result is None

    @pytest.mark.asyncio
    async def test_fallback_to_mirror_on_connection_error(self, plugin):
        call_count = 0
        total_categories = 9  # len(TELEGRAM_CATEGORIES)

        ok_resp = MagicMock()
        ok_resp.status = 200
        ok_resp.__aenter__ = AsyncMock(return_value=ok_resp)
        ok_resp.__aexit__ = AsyncMock(return_value=None)

        error_resp = MagicMock()
        error_resp.__aenter__ = AsyncMock(
            side_effect=Exception("Connection error")
        )
        error_resp.__aexit__ = AsyncMock(return_value=None)

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # First round (original URLs) all fail
            if call_count <= total_categories:
                return error_resp
            # Mirror round succeeds on first try
            return ok_resp

        plugin.session.head = MagicMock(side_effect=side_effect)

        result = await plugin._get_telegram_url("😀")
        assert result is not None
        assert call_count > total_categories  # Mirrors were tried

    @pytest.mark.asyncio
    async def test_no_name_returns_none(self, plugin):
        result = await plugin._get_telegram_url("abc")
        assert result is None


# ============================================================
# Tests: Session lifecycle
# ============================================================

class TestSessionLifecycle:
    @pytest.mark.asyncio
    async def test_ensure_session_creates_new(self, plugin):
        plugin.session = None

        mock_aiohttp = _main_module.aiohttp
        mock_session = MagicMock()
        mock_session.closed = False
        mock_aiohttp.ClientSession.return_value = mock_session

        await plugin._ensure_session()
        assert plugin.session is mock_session

    @pytest.mark.asyncio
    async def test_ensure_session_replaces_closed(self, plugin):
        plugin.session = MagicMock()
        plugin.session.closed = True

        mock_aiohttp = _main_module.aiohttp
        mock_session = MagicMock()
        mock_session.closed = False
        mock_aiohttp.ClientSession.return_value = mock_session

        await plugin._ensure_session()
        assert plugin.session is mock_session

    @pytest.mark.asyncio
    async def test_terminate_closes_session(self, plugin):
        await plugin.terminate()
        plugin.session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_terminate_skips_closed_session(self, plugin):
        plugin.session.closed = True
        await plugin.terminate()
        plugin.session.close.assert_not_called()


# ============================================================
# Tests: _get_animated_emoji shared helper
# ============================================================

class TestGetAnimatedEmoji:
    @pytest.mark.asyncio
    async def test_noto_success(self, plugin):
        plugin._download_image = AsyncMock(return_value="/tmp/emoji.gif")
        local_path, err = await plugin._get_animated_emoji("😀", "noto")
        assert local_path == "/tmp/emoji.gif"
        assert err is None

    @pytest.mark.asyncio
    async def test_noto_download_fail(self, plugin):
        plugin._download_image = AsyncMock(return_value=None)
        local_path, err = await plugin._get_animated_emoji("😀", "noto")
        assert local_path is None
        assert "Noto" in err

    @pytest.mark.asyncio
    async def test_tg_success(self, plugin):
        plugin._get_telegram_url = AsyncMock(
            return_value="https://example.com/emoji.webp"
        )
        plugin._download_image = AsyncMock(return_value="/tmp/emoji.webp")
        local_path, err = await plugin._get_animated_emoji("😀", "tg")
        assert local_path == "/tmp/emoji.webp"
        assert err is None

    @pytest.mark.asyncio
    async def test_tg_not_found(self, plugin):
        plugin._get_telegram_url = AsyncMock(return_value=None)
        local_path, err = await plugin._get_animated_emoji("😀", "tg")
        assert local_path is None
        assert "Telegram" in err

    @pytest.mark.asyncio
    async def test_tg_download_fail(self, plugin):
        plugin._get_telegram_url = AsyncMock(
            return_value="https://example.com/emoji.webp"
        )
        plugin._download_image = AsyncMock(return_value=None)
        local_path, err = await plugin._get_animated_emoji("😀", "tg")
        assert local_path is None
        assert "下载失败" in err

    @pytest.mark.asyncio
    async def test_unknown_source(self, plugin):
        local_path, err = await plugin._get_animated_emoji("😀", "invalid")
        assert local_path is None
        assert "未知来源" in err


# ============================================================
# Tests: LLM tool
# ============================================================

class TestLlmTool:
    @pytest.mark.asyncio
    async def test_llm_tool_success(self, plugin):
        plugin._get_animated_emoji = AsyncMock(
            return_value=("/tmp/emoji.gif", None)
        )
        mock_event = MagicMock()
        mock_event.plain_result = lambda t: t
        mock_event.chain_result = lambda c: c

        results = []
        async for r in plugin.animated_emoji_tool(mock_event, "😀", "noto"):
            results.append(r)

        assert len(results) == 1
        plugin._get_animated_emoji.assert_called_once_with("😀", "noto")

    @pytest.mark.asyncio
    async def test_llm_tool_invalid_emoji(self, plugin):
        mock_event = MagicMock()
        mock_event.plain_result = lambda t: t

        results = []
        async for r in plugin.animated_emoji_tool(mock_event, "abc", "noto"):
            results.append(r)

        assert len(results) == 1
        assert "未检测到有效的 emoji" in results[0]

    @pytest.mark.asyncio
    async def test_llm_tool_error(self, plugin):
        plugin._get_animated_emoji = AsyncMock(
            return_value=(None, "download error")
        )
        mock_event = MagicMock()
        mock_event.plain_result = lambda t: t

        results = []
        async for r in plugin.animated_emoji_tool(mock_event, "😀", "noto"):
            results.append(r)

        assert len(results) == 1
        assert "download error" in results[0]
