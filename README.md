# Video Subtitle GPT

输入视频链接或字幕文本，自动提取字幕并调用 OpenAI 兼容接口生成结构化总结。

## 功能

- 支持 YouTube / Bilibili 等公开视频链接
- 自动获取字幕；失败时可下载音频并走 Whisper 转写
- 支持手动粘贴字幕后直接总结
- 历史记录、字幕缓存、Markdown 结果渲染
- 历史记录可直接回看字幕，并支持复制/下载字幕与结果
- “仅下载音频”模式只生成音频文件，不继续转写或总结
- 后台自动清理临时任务文件

## 页面使用优化

- 字幕区和结果区右上角的「复制」按钮支持浏览器 Clipboard API，也会在非 HTTPS 环境下自动回退到传统复制方式。
- 点击左侧历史记录后，如果该记录有关联字幕缓存，页面会自动加载字幕内容，便于继续复制、校对或重跑总结。
- 选择「🎵 仅下载音频」时，任务下载出音频后即完成；如需转写和总结，请使用「🚀 自动全流程」。

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 18004 --reload
```

在 `.env` 中至少填写 `OPENAI_API_KEY`；如需音频转写，可填写 `GROQ_API_KEY`。
GPT 总结默认使用 `gpt-5.6-sol`，并继续读取原有的 `OPENAI_API_KEY` 与 `OPENAI_BASE_URL`。

完整说明见 [`docs/video-subtitle-gpt.md`](docs/video-subtitle-gpt.md)。

## YouTube 依赖与 Cookie

YouTube 在 VPS/机房 IP 上经常要求 JS challenge 和登录态。部署脚本会自动安装 Deno，
`requirements.txt` 使用 `yt-dlp[default]`，应用会把检测到的 JS runtime 显式传给 yt-dlp。

若日志出现 `Sign in to confirm you’re not a bot`，需要将 Netscape `cookies.txt`
放到 `data/cookies/youtube.txt`；B 站 cookie 可放到 `data/cookies/bilibili.txt`。
也可在 `.env` 设置 `YOUTUBE_COOKIE_FILE`、`BILIBILI_COOKIE_FILE` 指向文件。

`api.cobalt.tools` 公共 hosted API 需要鉴权/人机校验，不再适合作为免配置后端备用；
如需 Cobalt 备用下载，请配置自建实例 `COBALT_API_BASE` 以及对应的
`COBALT_API_KEY` 或 `COBALT_BEARER_TOKEN`。

欢迎 Issue 和 PR：Bug 修复、站点适配、UI 优化、部署文档改进都可以。
