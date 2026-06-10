# Video Subtitle GPT 项目文档

## 功能概览

- 输入 YouTube、Bilibili 等视频链接，自动提取字幕并用 GPT 总结。
- 字幕获取链路（自动全流程模式，按顺序尝试，前一步成功则跳过后续）：
  1. YouTube Transcript API（无需 cookie，直接拿字幕）
  2. yt-dlp 抓 VTT 字幕文件（支持 cookie，可选）
  3. yt-dlp 下载音频 → Groq Whisper 转写
  4. Cobalt API 下载音频（yt-dlp 失败时的备用，仅 YouTube）
  5. Whisper 失败时展示音频下载链接，可手动粘贴字幕继续
- B 站链接直接走音频下载 + Whisper，无需 cookie 也能处理公开视频。
- 支持手动粘贴字幕仅跑 GPT 总结（无需视频链接）。
- AI 总结结果渲染为 Markdown，支持标题、列表、代码块等格式。
- 字幕和任务结果自动缓存，相同链接重复提交直接命中缓存。
- 后台定期清理 `data/jobs`，默认超 24 小时或总占用超 1 GB 时删除旧任务。

## Cookie 说明（可选，不强制）

Cookie 文件放在 `data/cookies/` 下，有则自动使用，没有也能正常运行公开视频：

| 文件 | 作用 |
|------|------|
| `data/cookies/youtube.txt` | 让 yt-dlp 以账号身份绕过年龄限制、会员内容 |
| `data/cookies/bilibili.txt` | 让 yt-dlp 下载 B 站大会员内容 |

也可以在 `.env` 里用 `YOUTUBE_COOKIE_FILE`、`BILIBILI_COOKIE_FILE` 指定自定义路径。

导出格式为 Netscape cookies（浏览器插件 `cookies.txt` 或 `Get cookies.txt LOCALLY` 可导出）。

Cookie 过期只影响需要登录的内容，公开视频不受影响。服务不依赖 cookie 启动或运行。

## 前端说明

页面分三个区域：

1. **新建任务**（顶部）：输入链接，选择模式，点击「开始」。
   - 🚀 自动全流程：字幕 → Whisper → GPT 全自动
   - 🎵 仅下载音频：只下载 mp3，不转写
   - 📝 仅总结字幕：粘贴已有字幕直接调 GPT
   - 「高级」按钮展开自定义 Prompt 和手动字幕输入框

2. **当前任务**（提交后显示）：进度条 + 状态 badge + 日志折叠面板，任务完成后自动刷新历史。

3. **结果区**（左右分栏）：
   - 左侧：历史记录列表，点击查看历史总结
   - 右侧：字幕原文 + AI 总结（Markdown 渲染）

## 环境变量（.env）

```env
# OpenAI 兼容接口
OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://api.openai.com   # 或兼容网关，如 https://sub2.congmingai.com
OPENAI_MODEL=gpt-4o

# Groq Whisper 转写（可选，不配则跳过 Whisper 步骤）
GROQ_API_KEY=your_groq_key
GROQ_BASE_URL=https://api.groq.com/openai/v1
GROQ_WHISPER_MODEL=whisper-large-v3-turbo

# Cookie 文件路径（可选，Netscape cookies.txt 格式）
YOUTUBE_COOKIE_FILE=./data/cookies/youtube.txt
BILIBILI_COOKIE_FILE=./data/cookies/bilibili.txt

# Cobalt 音频下载备用（可选）
COBALT_API_BASE=https://api.cobalt.tools
COBALT_API_KEY=

# 存储与清理
DATA_DIR=./data
MAX_STORAGE_BYTES=1073741824   # 1 GB
JOB_TTL_HOURS=24

# 服务地址
APP_HOST=0.0.0.0
APP_PORT=18004
PUBLIC_BASE_URL=https://your-domain.example
```

`OPENAI_API_KEY` 和至少一个可用 API 端点是必须的，其余均为可选。

## 本地运行

```bash
cd video-subtitle-gpt
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env               # 填写 OPENAI_API_KEY 等
uvicorn app.main:app --host 0.0.0.0 --port 18004 --reload
```

ffmpeg 必须安装（yt-dlp 音频转码依赖）：

```bash
sudo apt install -y ffmpeg         # Debian/Ubuntu
brew install ffmpeg                # macOS
```

## 部署到服务器

```bash
ssh <user>@<server-host>
bash /opt/video-subtitle-gpt/deploy/install_or_update.sh \
  https://github.com/huoyuuu/video-subtitle-gpt.git
```

首次部署后配置环境变量：

```bash
cd /opt/video-subtitle-gpt
nano .env
sudo systemctl restart video-subtitle-gpt
```

检查服务状态：

```bash
systemctl status video-subtitle-gpt --no-pager
journalctl -u video-subtitle-gpt -f
curl http://localhost:18004
```

## 自动部署

### 方式 A：服务器定时 pull（推荐，简单可靠）

```bash
crontab -e
# 每 2 分钟检查更新
*/2 * * * * cd /opt/video-subtitle-gpt && git pull --ff-only && bash scripts/deploy.sh >> /var/log/vsg-deploy.log 2>&1
```

### 方式 B：Gitea Webhook

收到 push 事件后执行：

```bash
cd /opt/video-subtitle-gpt && git pull --ff-only && bash scripts/deploy.sh
```

webhook 记得配置 secret，并限制只执行固定脚本。

## Prompt 管理

- 默认 Prompt：`data/prompts/default.txt`
- 新增 Prompt：在 `data/prompts/` 下创建 `xxx.txt`，刷新页面下拉框自动出现
- Prompt 中写 `{transcript}` 会被替换为字幕全文；不写则字幕自动追加在 Prompt 末尾

## 存储清理规则

每次新任务启动时触发一次清理：

1. 删除 `data/jobs/` 下超过 `JOB_TTL_HOURS` 小时的任务目录
2. 若总大小仍超过 `MAX_STORAGE_BYTES`，按最旧优先继续删除

`data/transcripts/` 字幕缓存目录**不参与清理**，永久保留。

## 依赖说明

| 依赖 | 用途 | 是否必须 |
|------|------|----------|
| `yt-dlp` | 下载音频、抓 VTT 字幕 | 是 |
| `ffmpeg` | 音频转码（yt-dlp 依赖） | 是 |
| `youtube-transcript-api` | YouTube 无 cookie 字幕获取 | 是 |
| `openai` SDK | 调用 GPT 总结 | 是 |
| `groq` / `httpx` | Whisper 转写 | 否（不配则跳过） |
| `cobalt` API | YouTube 音频备用下载 | 否 |
