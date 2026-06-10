# Video Subtitle GPT

输入视频链接或字幕文本，自动提取字幕并调用 OpenAI 兼容接口生成结构化总结。

## 功能

- 支持 YouTube / Bilibili 等公开视频链接
- 自动获取字幕；失败时可下载音频并走 Whisper 转写
- 支持手动粘贴字幕后直接总结
- 历史记录、字幕缓存、Markdown 结果渲染
- 后台自动清理临时任务文件

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 18004 --reload
```

在 `.env` 中至少填写 `OPENAI_API_KEY`；如需音频转写，可填写 `GROQ_API_KEY`。

```bash
python -m pytest -q
```

欢迎 Issue 和 PR：Bug 修复、站点适配、UI 优化、部署文档改进都可以。
