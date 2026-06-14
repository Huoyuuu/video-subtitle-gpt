import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from openai import OpenAI
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data")).resolve()
JOBS_DIR = DATA_DIR / "jobs"
PROMPTS_DIR = DATA_DIR / "prompts"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
DB_PATH = DATA_DIR / "app.sqlite3"
MAX_STORAGE_BYTES = int(os.getenv("MAX_STORAGE_BYTES", str(1024**3)))
JOB_TTL_HOURS = int(os.getenv("JOB_TTL_HOURS", "24"))
MAX_JOB_COOKIE_BYTES = int(os.getenv("MAX_JOB_COOKIE_BYTES", str(256 * 1024)))

JOBS_DIR.mkdir(parents=True, exist_ok=True)
PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Video Subtitle GPT", version="1.2.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")
_jobs: dict[str, dict[str, Any]] = {}


def configured_path(env_name: str, default: Path) -> Path:
    value = (os.getenv(env_name) or "").strip()
    if not value:
        return default
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def youtube_cookie_file() -> Path:
    return configured_path("YOUTUBE_COOKIE_FILE", DATA_DIR / "cookies" / "youtube.txt")


def bilibili_cookie_file() -> Path:
    return configured_path("BILIBILI_COOKIE_FILE", DATA_DIR / "cookies" / "bilibili.txt")


def is_youtube_url(url: str) -> bool:
    u = (url or "").lower()
    return "youtube.com" in u or "youtu.be" in u


def is_bilibili_url(url: str) -> bool:
    u = (url or "").lower()
    return "bilibili.com" in u or "b23.tv" in u


def truthy_env(name: str, default: str = "") -> bool:
    return str(os.getenv(name, default) or "").lower() in {"1", "true", "yes", "on"}


def yt_dlp_executable() -> Path | None:
    """优先使用当前虚拟环境里的 yt-dlp；没有时回退到 PATH。"""
    local = Path(sys.executable).parent / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp")
    if local.exists():
        return local
    found = shutil.which("yt-dlp")
    return Path(found) if found else None


def find_executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    # systemd 的 PATH 有时不含 /usr/local/bin；部署脚本会把 deno 安装到这里。
    if os.name != "nt":
        for base in ("/usr/local/bin", "/usr/bin", "/bin", "/opt/homebrew/bin", "/usr/local/sbin"):
            p = Path(base) / name
            if p.exists() and os.access(p, os.X_OK):
                return str(p)
    return None


def env_cli_args(name: str) -> list[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw)
    except ValueError:
        return raw.split()


def non_empty_file(path: Path) -> bool:
    try:
        return path.exists() and path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def normalize_youtube_cookie_text(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    has_netscape_rows = any(
        not line.startswith("#") and "\t" in line and len(line.split("\t")) >= 7
        for line in lines
    )
    if has_netscape_rows:
        content = text.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
        if "Netscape HTTP Cookie File" not in content.splitlines()[0]:
            content = "# Netscape HTTP Cookie File\n" + content
        return content

    cookie_text = "\n".join(lines)
    cookie_text = re.sub(r"(?is)^\s*cookie\s*:\s*", "", cookie_text).strip()
    chunks = re.split(r"[;\n]+", cookie_text)
    rows = ["# Netscape HTTP Cookie File"]
    for chunk in chunks:
        if "=" not in chunk:
            continue
        name, value = chunk.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        rows.append("\t".join([".youtube.com", "TRUE", "/", "TRUE", "1893456000", name, value]))
    if len(rows) == 1:
        raise ValueError("Cookie 格式无法识别，请粘贴 Netscape cookies.txt 或浏览器 Cookie 头。")
    return "\n".join(rows) + "\n"


def write_job_youtube_cookie(job_dir: Path, raw: str) -> Path | None:
    text = normalize_youtube_cookie_text(raw)
    if not text:
        return None
    size = len(text.encode("utf-8"))
    if size > MAX_JOB_COOKIE_BYTES:
        raise ValueError(f"YouTube Cookie 太大，当前限制 {MAX_JOB_COOKIE_BYTES} bytes。")
    path = job_dir / "youtube-cookies.txt"
    path.write_text(text, encoding="utf-8", newline="\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def remove_job_youtube_cookie(job_dir: Path):
    try:
        (job_dir / "youtube-cookies.txt").unlink(missing_ok=True)
    except OSError:
        pass


def youtube_cookie_args(job_id: str | None = None, cookie_file: Path | None = None) -> tuple[list[str], bool]:
    if cookie_file and non_empty_file(cookie_file):
        if job_id:
            add_log(job_id, "使用本次任务提交的 YouTube cookie，yt-dlp 将优先携带该 cookie。")
        return ["--cookies", str(cookie_file)], True

    cookie = youtube_cookie_file()
    browser = (os.getenv("YOUTUBE_COOKIES_FROM_BROWSER") or "").strip()
    if non_empty_file(cookie):
        if job_id:
            add_log(job_id, "检测到 YouTube cookies 文件，yt-dlp 将携带 cookies。")
        return ["--cookies", str(cookie)], True
    if browser:
        if job_id:
            add_log(job_id, f"检测到 YOUTUBE_COOKIES_FROM_BROWSER={browser}，yt-dlp 将从浏览器读取 cookies。")
        return ["--cookies-from-browser", browser], True
    if job_id:
        if cookie.exists():
            add_log(job_id, f"YouTube cookies 文件为空或不可读：{cookie}")
        else:
            add_log(job_id, f"未找到 YouTube cookies 文件：{cookie}")
    return [], False


def yt_dlp_js_runtime_args(job_id: str | None = None) -> list[str]:
    """YouTube 新版提取需要 JS runtime；自动发现 deno/node/qjs/bun，也允许环境变量覆盖。"""
    mode = (os.getenv("YTDLP_JS_RUNTIME") or "auto").strip()
    if mode.lower() in {"", "auto"}:
        candidates = [
            ("deno", "deno"),
            ("node", "node"),
            ("quickjs", "qjs"),
            ("bun", "bun"),
        ]
        for name, exe in candidates:
            found = find_executable(exe)
            if found:
                value = f"{name}:{found}"
                if job_id:
                    add_log(job_id, f"检测到 JS runtime：{value}")
                return ["--js-runtimes", value]
        if job_id:
            add_log(job_id, "未检测到 deno/node/qjs/bun；YouTube 可能因 JS challenge 失败。")
        return []
    if mode.lower() in {"none", "off", "false", "0"}:
        if job_id:
            add_log(job_id, "YTDLP_JS_RUNTIME 已关闭，不向 yt-dlp 传递 JS runtime。")
        return []
    if job_id:
        add_log(job_id, f"使用 YTDLP_JS_RUNTIME={mode}")
    return ["--js-runtimes", mode]


def youtube_ytdlp_args(job_id: str | None = None, cookie_file: Path | None = None) -> tuple[list[str], bool]:
    args: list[str] = []
    args += yt_dlp_js_runtime_args(job_id)
    cookie_args, has_auth = youtube_cookie_args(job_id, cookie_file)
    args += cookie_args

    remote_components = (os.getenv("YTDLP_REMOTE_COMPONENTS") or "").strip()
    if remote_components and remote_components.lower() not in {"none", "off", "false", "0"}:
        args += ["--remote-components", remote_components]
        if job_id:
            add_log(job_id, f"yt-dlp 启用远程组件：{remote_components}")

    user_agent = (os.getenv("YTDLP_USER_AGENT") or "").strip()
    if user_agent:
        args += ["--user-agent", user_agent]
    if truthy_env("YTDLP_FORCE_IPV4"):
        args += ["--force-ipv4"]
    args += env_cli_args("YTDLP_EXTRA_ARGS")
    return args, has_auth


def yt_dlp_failure_hint(url: str, tail: str, youtube_auth_used: bool = False) -> str:
    if not is_youtube_url(url):
        if "Sign in to confirm" in tail or "not a bot" in tail:
            return "\n\n提示：站点触发了反机器人校验，请更新 cookies。"
        return ""
    hints: list[str] = []
    if "No supported JavaScript runtime" in tail:
        hints.append("当前环境缺少 deno/node/qjs/bun。请重跑部署脚本或安装 Deno >= 2.3.0；项目也会把检测到的 runtime 显式传给 yt-dlp。")
    if "Sign in to confirm" in tail or "not a bot" in tail:
        if youtube_auth_used:
            hints.append("YouTube 仍要求登录/真人确认，通常是 cookies 过期、导出账号未通过验证，或 cookies 与服务器出口 IP 不匹配；请重新导出 Netscape cookies 到 data/cookies/youtube.txt。")
        else:
            hints.append("YouTube 在 VPS/机房 IP 上触发反机器人校验；公开视频也可能必须提供登录态。请把 Netscape cookies 放到 data/cookies/youtube.txt，或设置 YOUTUBE_COOKIE_FILE。")
    if "HTTP Error 429" in tail or "Too Many Requests" in tail:
        hints.append("YouTube 限制了当前出口 IP；请降低频率、换出口 IP/代理，或用同一出口 IP 完成人机验证后重新导出 cookies。")
    if not hints:
        hints.append("建议确认 yt-dlp 已升级为 yt-dlp[default]、已安装 JS runtime，并配置可用 YouTube cookies。")
    return "\n\n提示：\n- " + "\n- ".join(hints)


def cobalt_headers() -> tuple[dict[str, str], str | None]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    bearer = (os.getenv("COBALT_BEARER_TOKEN") or os.getenv("COBALT_JWT") or "").strip()
    api_key = (os.getenv("COBALT_API_KEY") or "").strip()
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
        return headers, "Bearer"
    if api_key:
        headers["Authorization"] = f"Api-Key {api_key}"
        return headers, "Api-Key"
    return headers, None


def cobalt_auth_hint(api_base: str, detail: str = "") -> str:
    if "api.cobalt.tools" in api_base:
        return (
            "Cobalt 公共 hosted API 启用了 bot protection，官方文档也说明它不适合未经许可直接接入第三方项目。"
            "请改用自建 Cobalt API，或配置实例所有者提供的 COBALT_API_KEY / COBALT_BEARER_TOKEN。"
        )
    if "api.auth.jwt.missing" in detail or "auth.jwt.missing" in detail:
        return "该 Cobalt 实例要求 Bearer/JWT 鉴权，请配置 COBALT_BEARER_TOKEN，或改用支持 Api-Key/免鉴权的自建实例。"
    if "api.auth" in detail:
        return "该 Cobalt 实例要求鉴权，请按实例配置提供 COBALT_API_KEY 或 COBALT_BEARER_TOKEN。"
    return ""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_job(job_id: str, **kw):
    job = _jobs[job_id]
    job.update(kw)
    job["updated_at"] = now_iso()


def add_log(job_id: str, msg: str):
    _jobs[job_id].setdefault("logs", []).append({"time": now_iso(), "msg": msg})


def normalize_url(url: str) -> str:
    return (url or "").strip()


def md5_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def title_from_url(url: str) -> str:
    u = normalize_url(url)
    if not u:
        return "手动字幕总结"
    m = re.search(r"/video/([^/?#]+)", u)
    if m:
        return m.group(1)
    m = re.search(r"[?&]v=([^&#]+)", u)
    if m:
        return m.group(1)
    return u[:80]


def cache_hash_for(url: str, transcript: str | None = None) -> str:
    nurl = normalize_url(url)
    if nurl:
        return md5_text(nurl)
    return md5_text("manual:" + (transcript or ""))


def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transcript_cache (
                cache_hash TEXT PRIMARY KEY,
                url TEXT,
                transcript_path TEXT NOT NULL,
                transcript_len INTEGER NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_transcript_cache_url ON transcript_cache(url)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                url TEXT,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                result_path TEXT,
                transcript_hash TEXT,
                logs_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC)")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(history)").fetchall()}
        if "logs_json" not in cols:
            conn.execute("ALTER TABLE history ADD COLUMN logs_json TEXT")


init_db()


def get_cached_transcript(url: str) -> tuple[str, str, sqlite3.Row] | None:
    nurl = normalize_url(url)
    if not nurl:
        return None
    h = cache_hash_for(nurl)
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM transcript_cache WHERE cache_hash=?", (h,)).fetchone()
    if not row:
        return None
    p = Path(row["transcript_path"])
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        return None
    return h, text, row


def save_transcript_cache(url: str, transcript: str, source: str) -> str:
    text = transcript or ""
    h = cache_hash_for(url, text)
    p = TRANSCRIPTS_DIR / f"{h}.txt"
    p.write_text(text, encoding="utf-8")
    ts = now_iso()
    with db_conn() as conn:
        old = conn.execute("SELECT created_at FROM transcript_cache WHERE cache_hash=?", (h,)).fetchone()
        conn.execute(
            """
            INSERT INTO transcript_cache(cache_hash,url,transcript_path,transcript_len,source,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(cache_hash) DO UPDATE SET
                url=excluded.url,
                transcript_path=excluded.transcript_path,
                transcript_len=excluded.transcript_len,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (h, normalize_url(url), str(p), len(text), source, old["created_at"] if old else ts, ts),
        )
    return h


def extract_youtube_id(url: str) -> str | None:
    patterns = [r"youtu\.be/([A-Za-z0-9_-]{11})", r"v=([A-Za-z0-9_-]{11})", r"/shorts/([A-Za-z0-9_-]{11})"]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def format_youtube(items) -> str:
    lines = []
    for it in items:
        text = it.get("text", "").replace("\n", " ").strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def clean_vtt_to_text(raw: str) -> str:
    lines: list[str] = []
    seen = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT" or line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        if "-->" in line:
            continue
        line = re.sub(r"<\d{2}:\d{2}:\d{2}\.\d{3}>", "", line)
        line = re.sub(r"</?c[^>]*>", "", line)
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines)


async def get_youtube_transcript_ytdlp(
    url: str,
    job_dir: Path,
    job_id: str | None = None,
    youtube_cookie_file: Path | None = None,
) -> str | None:
    yt_dlp_bin = yt_dlp_executable()
    if not yt_dlp_bin:
        return None
    outtmpl = str(job_dir / "yt_sub.%(ext)s")
    yt_args, _ = youtube_ytdlp_args(job_id, youtube_cookie_file)
    cmd = [
        str(yt_dlp_bin),
        *yt_args,
        "--skip-download",
        "--ignore-no-formats-error",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", "zh-Hans,zh-Hant,zh.*,en.*",
        "--sub-format", "vtt/best",
        "--no-playlist",
        "-o", outtmpl,
        url,
    ]
    try:
        await run_cmd(cmd, job_dir)
    except Exception as e:
        if job_id:
            add_log(job_id, f"yt-dlp 字幕抓取未成功：{e}")
    preferred = []
    for pat in ["*.zh-Hans.vtt", "*.zh.vtt", "*.zh-Hant.vtt", "*.en.vtt", "*.en-orig.vtt", "*.vtt"]:
        preferred.extend(job_dir.glob(pat))
    for f in preferred:
        try:
            text = clean_vtt_to_text(f.read_text(encoding="utf-8", errors="ignore"))
            if len(text.strip()) > 20:
                if job_id:
                    add_log(job_id, f"已通过 yt-dlp 获取 YouTube 字幕：{f.name}，长度 {len(text)}。")
                return text
        except Exception:
            pass
    return None


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def cleanup_storage():
    """只清理临时任务目录；DATA_DIR/transcripts 永久保留。"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=JOB_TTL_HOURS)
    for d in JOBS_DIR.iterdir():
        if not d.is_dir():
            continue
        try:
            mtime = datetime.fromtimestamp(d.stat().st_mtime, timezone.utc)
            if mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass
    while dir_size(JOBS_DIR) > MAX_STORAGE_BYTES:
        dirs = [d for d in JOBS_DIR.iterdir() if d.is_dir()]
        if not dirs:
            break
        oldest = min(dirs, key=lambda x: x.stat().st_mtime)
        shutil.rmtree(oldest, ignore_errors=True)


async def run_cmd(cmd: list[str], cwd: Path):
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError((err or out).decode(errors="ignore")[-2000:])
    return out.decode(errors="ignore")


async def get_youtube_transcript(url: str) -> str | None:
    vid = extract_youtube_id(url)
    if not vid:
        return None
    try:
        data = await asyncio.to_thread(YouTubeTranscriptApi.get_transcript, vid, languages=["zh-Hans", "zh", "en"])
        return format_youtube(data)
    except (TranscriptsDisabled, NoTranscriptFound, Exception):
        return None


async def download_audio_via_cobalt(url: str, job_dir: Path, job_id: str | None = None) -> Path:
    api_base = (os.getenv("COBALT_API_BASE") or "https://api.cobalt.tools").rstrip("/")
    headers, auth_kind = cobalt_headers()
    payload = {"url": url, "downloadMode": "audio", "audioFormat": "mp3", "audioBitrate": "128", "filenameStyle": "basic", "disableMetadata": True, "alwaysProxy": True}
    if job_id:
        add_log(job_id, f"yt-dlp 失败，尝试第三方 Cobalt 下载：{api_base}（鉴权：{auth_kind or '未配置'}）")
        set_job(job_id, stage="第三方下载", download_status="请求 Cobalt API")
    if "api.cobalt.tools" in api_base and not auth_kind:
        raise RuntimeError(cobalt_auth_hint(api_base))
    async with httpx.AsyncClient(timeout=600, follow_redirects=True) as client:
        r = await client.post(api_base + "/", headers=headers, json=payload)
        if r.status_code >= 400:
            detail = r.text[:500]
            hint = cobalt_auth_hint(api_base, detail)
            raise RuntimeError(f"Cobalt API 请求失败：HTTP {r.status_code} {detail}" + (f"\n{hint}" if hint else ""))
        data = r.json()
        if data.get("status") == "error":
            detail = json.dumps(data.get("error"), ensure_ascii=False)
            hint = cobalt_auth_hint(api_base, detail)
            raise RuntimeError(f"Cobalt API 返回错误：{detail}" + (f"\n{hint}" if hint else ""))
        dl_url = data.get("url") or data.get("audio")
        if not dl_url:
            raise RuntimeError(f"Cobalt API 未返回可下载 URL：{json.dumps(data, ensure_ascii=False)[:800]}")
        if job_id:
            add_log(job_id, f"Cobalt 返回 {data.get('status')}，开始下载音频文件。")
            set_job(job_id, progress=40, stage="第三方下载", download_status="第三方音频下载中")
        out = job_dir / "audio.mp3"
        async with client.stream("GET", dl_url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or resp.headers.get("Estimated-Content-Length") or 0)
            done = 0
            with out.open("wb") as f:
                async for chunk in resp.aiter_bytes(1024 * 256):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if job_id and total:
                        pct = min(100, done * 100 / total)
                        set_job(job_id, progress=40 + min(14, int(pct * 0.14)), stage="第三方下载", download_percent=round(pct, 1), download_status=f"Cobalt 下载 {pct:.1f}%")
        if out.stat().st_size == 0:
            raise RuntimeError("Cobalt 下载完成但文件为空。")
        if job_id:
            add_log(job_id, f"第三方音频下载完成：{out.name}，大小 {out.stat().st_size} bytes")
            set_job(job_id, progress=55, stage="Whisper 转写", download_percent=100, download_status="第三方音频下载完成")
        return out


async def download_audio(
    url: str,
    job_dir: Path,
    job_id: str | None = None,
    youtube_cookie_file: Path | None = None,
) -> Path:
    outtmpl = str(job_dir / "audio.%(ext)s")
    yt_dlp_bin = yt_dlp_executable()
    if not yt_dlp_bin:
        raise RuntimeError("未找到 yt-dlp 可执行文件，请确认已在虚拟环境或 PATH 中安装 yt-dlp。")
    bilibili_cookie = bilibili_cookie_file()
    youtube_auth_used = False
    cmd = [
        str(yt_dlp_bin),
        "--newline",
        "--progress-template", "download:%(progress._percent_str)s %(progress._speed_str)s ETA %(progress._eta_str)s",
    ]
    if is_bilibili_url(url) and bilibili_cookie.exists():
        cmd += ["--cookies", str(bilibili_cookie)]
        if job_id:
            add_log(job_id, "检测到 B 站 cookies 文件，下载音频时将携带 cookies。")
    if is_youtube_url(url):
        yt_args, youtube_auth_used = youtube_ytdlp_args(job_id, youtube_cookie_file)
        cmd += yt_args
    cmd += [
        "-x", "--audio-format", "mp3", "--audio-quality", "5",
        "--no-playlist", "-o", outtmpl, url,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(job_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    lines: list[str] = []
    assert proc.stdout is not None
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            break
        line = raw.decode(errors="ignore").strip()
        if not line:
            continue
        lines.append(line)
        if len(lines) > 80:
            lines = lines[-80:]
        if job_id:
            if line.startswith("download:"):
                m = re.search(r"([0-9]+(?:\.[0-9]+)?)%", line)
                if m:
                    pct = float(m.group(1))
                    total_progress = 30 + min(24, int(pct * 0.24))
                    set_job(job_id, progress=total_progress, stage="下载音频", download_percent=round(pct, 1), download_status=line)
            elif "ExtractAudio" in line or "Destination" in line or "Deleting original file" in line:
                add_log(job_id, line[:240])
    rc = await proc.wait()
    if rc != 0:
        tail = "\n".join(lines[-20:])
        hint = yt_dlp_failure_hint(url, tail, youtube_auth_used)
        if is_youtube_url(url):
            try:
                return await download_audio_via_cobalt(url, job_dir, job_id)
            except Exception as ce:
                raise RuntimeError(f"音频下载失败，yt-dlp 返回 {rc}：\n{tail}{hint}\n\n第三方下载也失败：{ce}")
        raise RuntimeError(f"音频下载失败，yt-dlp 返回 {rc}：\n{tail}{hint}")
    files = list(job_dir.glob("audio.*"))
    if not files:
        raise RuntimeError("音频下载完成但未找到 audio.* 文件，请检查 yt-dlp 输出或站点限制。")
    if job_id:
        set_job(job_id, progress=55, stage="Whisper 转写", download_percent=100, download_status="音频下载完成")
    return files[0]


async def groq_whisper(audio: Path) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("未配置 GROQ_API_KEY")
    url = (os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/") + "/audio/transcriptions")
    async with httpx.AsyncClient(timeout=600) as client:
        with audio.open("rb") as f:
            r = await client.post(url, headers={"Authorization": f"Bearer {api_key}"}, data={"model": os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")}, files={"file": (audio.name, f, "audio/mpeg")})
    if r.status_code in (400, 401, 403, 429):
        raise RuntimeError(f"Groq Whisper 不可用/限额耗尽：{r.status_code} {r.text[:300]}")
    r.raise_for_status()
    return r.json().get("text", "")


def normalize_openai_base_url() -> str | None:
    base = (os.getenv("OPENAI_BASE_URL") or "").strip().rstrip("/")
    if not base:
        return None
    if not re.search(r"/v\d+(?:/)?$", base):
        base += "/v1"
    return base


def reject_html_response(text: str, where: str):
    head = (text or "").lstrip()[:80].lower()
    if head.startswith("<!doctype html") or head.startswith("<html"):
        raise RuntimeError(f"{where} 返回了 HTML 页面，通常是 OPENAI_BASE_URL 没有指向 API 路径。当前已按规则使用：{normalize_openai_base_url()}")


async def call_gpt(prompt_text: str, transcript: str, job_id: str | None = None) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY")
    base_url = normalize_openai_base_url()
    client = OpenAI(api_key=api_key, base_url=base_url)
    final_prompt = prompt_text.replace("{transcript}", transcript) if "{transcript}" in prompt_text else f"{prompt_text}\n\n字幕：\n{transcript}"
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            if job_id:
                add_log(job_id, f"调用 GPT（第 {attempt}/3 次），模型：{os.getenv('OPENAI_MODEL', 'gpt-5.5')}，base_url：{base_url}")
            resp = await asyncio.to_thread(
                client.chat.completions.create,
                model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
                messages=[{"role": "user", "content": final_prompt}],
                temperature=0.2,
            )
            if isinstance(resp, str):
                reject_html_response(resp, "GPT 接口")
                return resp
            if isinstance(resp, dict):
                try:
                    text = resp["choices"][0]["message"]["content"] or ""
                except Exception:
                    text = json.dumps(resp, ensure_ascii=False)
                reject_html_response(text, "GPT 接口")
                return text
            text = resp.choices[0].message.content or ""
            reject_html_response(text, "GPT 接口")
            return text
        except Exception as e:
            last_error = e
            if job_id:
                add_log(job_id, f"GPT 调用失败（第 {attempt}/3 次）：{e}")
            if attempt < 3:
                await asyncio.sleep(1.5 * attempt)
    raise RuntimeError(f"GPT 调用重试 3 次仍失败：{last_error}")


def load_prompt(name: str) -> str:
    safe = re.sub(r"[^\w\-.\u4e00-\u9fa5]", "_", name or "default")
    p = PROMPTS_DIR / (safe if safe.endswith(".txt") else safe + ".txt")
    if not p.exists():
        p = PROMPTS_DIR / "default.txt"
    if not p.exists():
        return "请用简单直白的话总结下面的字幕，输出一段完整的话。\n\n字幕：\n{transcript}"
    return p.read_text(encoding="utf-8")


def write_job_transcript(job_id: str, url: str, transcript: str, source: str) -> str:
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "transcript.txt").write_text(transcript, encoding="utf-8")
    h = save_transcript_cache(url, transcript, source)
    set_job(
        job_id,
        transcript=transcript,
        transcript_hash=h,
        transcript_len=len(transcript),
        transcript_url=f"/cache/transcripts/{h}.txt",
    )
    return h


def save_history(job_id: str, url: str, result: str):
    job = _jobs.get(job_id, {})
    summary = (result or "").strip()
    title = title_from_url(url)
    logs_json = json.dumps(job.get("logs", []), ensure_ascii=False)
    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO history(job_id,url,title,summary,result_path,transcript_hash,logs_json,created_at)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (job_id, normalize_url(url), title, summary, str(JOBS_DIR / job_id / "result.md"), job.get("transcript_hash"), logs_json, now_iso()),
        )


def list_history(limit: int = 50):
    limit = max(1, min(int(limit or 50), 200))
    with db_conn() as conn:
        rows = conn.execute("SELECT * FROM history ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        try:
            d["logs"] = json.loads(d.get("logs_json") or "[]")
        except Exception:
            d["logs"] = []
        items.append(d)
    return items


async def summarize_transcript(job_id: str, url: str, transcript: str, prompt_name: str, custom_prompt: str | None, source: str):
    if not transcript or not transcript.strip():
        raise RuntimeError("字幕为空，无法调用 GPT 总结。")
    write_job_transcript(job_id, url, transcript, source)
    set_job(job_id, status="running", progress=75, stage="调用 GPT")
    prompt = custom_prompt.strip() if custom_prompt and custom_prompt.strip() else load_prompt(prompt_name)
    result = await call_gpt(prompt, transcript, job_id=job_id)
    job_dir = JOBS_DIR / job_id
    (job_dir / "result.md").write_text(result, encoding="utf-8")
    save_history(job_id, url, result)
    set_job(job_id, status="done", progress=100, stage="完成", result=result, result_url=f"/download/{job_id}/result.md", title=title_from_url(url))


async def process(
    job_id: str,
    url: str,
    prompt_name: str,
    custom_prompt: str | None,
    start_mode: str = "auto",
    manual_transcript: str = "",
    use_cache: bool = True,
    youtube_cookie: str = "",
):
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    url = normalize_url(url)
    job_youtube_cookie_file: Path | None = None
    try:
        cleanup_storage()
        if youtube_cookie.strip():
            if not is_youtube_url(url):
                add_log(job_id, "已填写 YouTube cookie，但当前链接不是 YouTube，已忽略。")
            else:
                job_youtube_cookie_file = write_job_youtube_cookie(job_dir, youtube_cookie)
                add_log(job_id, "已接收本次任务的 YouTube cookie。")
        set_job(job_id, status="running", progress=5, stage="准备", url_hash=cache_hash_for(url) if url else None)
        if start_mode == "summary":
            add_log(job_id, "按选择从『已有字幕 → GPT 总结』开始。")
            await summarize_transcript(job_id, url, manual_transcript, prompt_name, custom_prompt, "manual")
            return

        if use_cache and url:
            cached = get_cached_transcript(url)
            if cached:
                h, transcript, row = cached
                add_log(job_id, f"字幕缓存命中：MD5={h}，来源={row['source']}，直接进入 GPT 总结。")
                set_job(job_id, cache_hit=True)
                await summarize_transcript(job_id, url, transcript, prompt_name, custom_prompt, "cache")
                return
            add_log(job_id, f"字幕缓存未命中：MD5={cache_hash_for(url)}。")

        set_job(job_id, progress=10, stage="获取字幕")
        is_bilibili = is_bilibili_url(url)
        transcript = None
        if start_mode == "audio":
            add_log(job_id, "按选择从『下载音频』开始，跳过直接字幕获取。")
        elif is_bilibili:
            add_log(job_id, "检测到 B 站链接：按配置跳过字幕获取，直接下载音频。")
        else:
            add_log(job_id, "尝试直接获取 YouTube 字幕；非 YouTube 站点会直接进入音频下载。")
            if "youtu" in url:
                transcript = await get_youtube_transcript(url)
                if not transcript:
                    add_log(job_id, "YouTube Transcript API 未拿到字幕，改用 yt-dlp 抓字幕文件。")
                    transcript = await get_youtube_transcript_ytdlp(url, job_dir, job_id, job_youtube_cookie_file)
            else:
                transcript = None

        audio_path = None
        if not transcript and is_youtube_url(url) and start_mode != "audio":
            add_log(job_id, "YouTube 字幕获取失败，尝试下载音频后用 Whisper 转写。")
        if not transcript:
            if not url:
                raise RuntimeError("没有视频链接，无法下载音频。若已有字幕，请选择『已有字幕 → 只跑总结』。")
            set_job(job_id, progress=30, stage="下载音频")
            add_log(job_id, "直接字幕不可用，开始用 yt-dlp 下载音频。")
            audio_path = await download_audio(url, job_dir, job_id, job_youtube_cookie_file)
            set_job(job_id, audio_url=f"/download/{job_id}/{audio_path.name}")
            set_job(job_id, progress=55, stage="Whisper 转写")
            try:
                transcript = await groq_whisper(audio_path)
            except Exception as e:
                add_log(job_id, str(e))
                set_job(job_id, status="audio_ready", progress=65, stage="请手动处理字幕", message="Whisper 不可用或限额耗尽，音频已提供下载。", audio_url=f"/download/{job_id}/{audio_path.name}")
                return
        await summarize_transcript(job_id, url, transcript, prompt_name, custom_prompt, "youtube" if "youtu" in url else "whisper")
    except Exception as e:
        set_job(job_id, status="error", progress=100, stage="失败", message=str(e))
    finally:
        remove_job_youtube_cookie(job_dir)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    prompts = sorted([p.stem for p in PROMPTS_DIR.glob("*.txt")])
    return templates.TemplateResponse("index.html", {"request": request, "prompts": prompts})


def truthy(v: str | bool | None) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").lower() in {"1", "true", "yes", "on"}


@app.post("/api/jobs")
async def create_job(
    url: str = Form(""),
    prompt_name: str = Form("default"),
    custom_prompt: str = Form(""),
    start_mode: str = Form("auto"),
    manual_transcript: str = Form(""),
    use_cache: str = Form("true"),
    youtube_cookie: str = Form(""),
):
    if start_mode != "summary" and not normalize_url(url):
        raise HTTPException(400, "请填写视频链接，或选择『已有字幕 → 只跑总结』。")
    if start_mode == "summary" and not manual_transcript.strip():
        raise HTTPException(400, "选择『已有字幕 → 只跑总结』时必须粘贴字幕。")
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {
        "id": job_id,
        "url": normalize_url(url),
        "url_hash": cache_hash_for(url) if normalize_url(url) else None,
        "prompt_name": prompt_name,
        "custom_prompt": custom_prompt,
        "start_mode": start_mode,
        "manual_transcript": manual_transcript,
        "use_cache": truthy(use_cache),
        "status": "queued",
        "progress": 0,
        "stage": "排队中",
        "created_at": now_iso(),
        "logs": [],
        "retry_count": 0,
    }
    asyncio.create_task(process(job_id, url, prompt_name, custom_prompt, start_mode, manual_transcript, truthy(use_cache), youtube_cookie))
    return {"job_id": job_id}


@app.get("/api/history")
async def api_history(limit: int = 50):
    return {"items": list_history(limit)}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "job not found")
    return _jobs[job_id]


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str, start_mode: str = Form("same")):
    if job_id not in _jobs:
        raise HTTPException(404, "job not found")
    old = _jobs[job_id]
    mode = old.get("start_mode", "auto") if start_mode == "same" else start_mode
    manual = old.get("manual_transcript", "")
    if mode == "summary" and not manual and old.get("transcript"):
        manual = old["transcript"]
    new_id = uuid.uuid4().hex
    _jobs[new_id] = {
        "id": new_id,
        "retry_of": job_id,
        "url": old.get("url", ""),
        "url_hash": old.get("url_hash"),
        "prompt_name": old.get("prompt_name", "default"),
        "custom_prompt": old.get("custom_prompt", ""),
        "start_mode": mode,
        "manual_transcript": manual,
        "use_cache": old.get("use_cache", True),
        "status": "queued",
        "progress": 0,
        "stage": "排队中",
        "created_at": now_iso(),
        "logs": [{"time": now_iso(), "msg": f"从任务 {job_id} 重试，模式：{mode}"}],
        "retry_count": int(old.get("retry_count", 0)) + 1,
    }
    asyncio.create_task(process(new_id, old.get("url", ""), old.get("prompt_name", "default"), old.get("custom_prompt", ""), mode, manual, old.get("use_cache", True)))
    return {"job_id": new_id}


@app.post("/api/jobs/{job_id}/transcript")
async def submit_transcript(job_id: str, transcript: str = Form(...), prompt_name: str = Form("default"), custom_prompt: str = Form("")):
    if job_id not in _jobs:
        raise HTTPException(404, "job not found")
    async def resume():
        try:
            add_log(job_id, "收到手动字幕，进入 GPT 总结。")
            await summarize_transcript(job_id, _jobs[job_id].get("url", ""), transcript, prompt_name, custom_prompt, "manual")
        except Exception as e:
            set_job(job_id, status="error", progress=100, stage="失败", message=str(e))
    asyncio.create_task(resume())
    return {"ok": True}


@app.get("/download/{job_id}/{filename}")
async def download(job_id: str, filename: str):
    if "cookie" in filename.lower():
        raise HTTPException(404, "file not found")
    p = JOBS_DIR / job_id / filename
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(p, filename=filename)


@app.get("/cache/transcripts/{cache_hash}.txt")
async def download_cached_transcript(cache_hash: str):
    if not re.fullmatch(r"[0-9a-f]{32}", cache_hash):
        raise HTTPException(400, "bad hash")
    p = TRANSCRIPTS_DIR / f"{cache_hash}.txt"
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "transcript not found")
    return FileResponse(p, filename=f"transcript-{cache_hash}.txt")
