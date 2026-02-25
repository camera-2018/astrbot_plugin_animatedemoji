# Animated Emoji Plugin for AstrBot

将静态 emoji 转换为动态 emoji，支持两种动态方案：

- **Google Noto Animated Emoji** — 来自 Google Fonts 的动态 Noto Color Emoji（GIF 格式）
- **Telegram Animated Emoji** — 来自 Telegram 的动态表情（WebP 格式）

## 使用方法

```
/animoji <emoji>          默认使用 Noto 动态 emoji
/animoji noto <emoji>     使用 Google Noto 动态 emoji
/animoji tg <emoji>       使用 Telegram 动态 emoji
```

### 示例

```
/animoji 😀
/animoji noto ❤️
/animoji tg 🎉
```

## 安装

将本插件放置在 AstrBot 的插件目录中，或通过 AstrBot 插件市场安装。

## 数据来源

- **Noto**: [Google Noto Emoji](https://fonts.google.com/noto/emoji) — 动画 GIF 通过 Google Fonts CDN 提供
- **Telegram**: [Telegram-Animated-Emojis](https://github.com/Tarikul-Islam-Anik/Telegram-Animated-Emojis) — Telegram 风格的动态表情

## 依赖

- `httpx` — 异步 HTTP 客户端
- `emoji` — emoji 名称解析库
