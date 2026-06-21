from pathlib import Path

from app.main import extract_youtube_id, format_youtube, load_prompt, cleanup_storage, JOBS_DIR, normalize_url


def test_normalize_url_extracts_url_from_mixed_text():
    raw = "【求生之路2官图《毫不留情》专家单通绝境17特】 https://www.bilibili.com/video/BV1W47y65Ecg/?share_source=copy_web&vd_source=418e92dfaf37df0a35ed6b1ff4da6b14"
    assert normalize_url(raw) == "https://www.bilibili.com/video/BV1W47y65Ecg"


def test_normalize_url_keeps_youtube_query_video_id():
    raw = "看看这个 https://www.youtube.com/watch?v=abcdefghijk&t=1 后面的文字"
    assert normalize_url(raw) == "https://www.youtube.com/watch?v=abcdefghijk&t=1"


def test_extract_youtube_id_variants():
    assert extract_youtube_id("https://youtu.be/abcdefghijk") == "abcdefghijk"
    assert extract_youtube_id("https://www.youtube.com/watch?v=abcdefghijk&t=1") == "abcdefghijk"
    assert extract_youtube_id("https://www.youtube.com/shorts/abcdefghijk") == "abcdefghijk"
    assert extract_youtube_id("https://www.bilibili.com/video/BV17eVL6GEAh") is None


def test_format_youtube_strips_newlines():
    data = [{"text": "hello\nworld"}, {"text": "  "}, {"text": "again"}]
    assert format_youtube(data) == "hello world\nagain"


def test_load_prompt_fallback():
    text = load_prompt("not-exist")
    assert "{transcript}" in text or "字幕" in text


def test_cookie_file_env_override(monkeypatch, tmp_path):
    import app.main as m
    youtube = tmp_path / "youtube.txt"
    monkeypatch.setenv("YOUTUBE_COOKIE_FILE", str(youtube))
    monkeypatch.setenv("BILIBILI_COOKIE_FILE", "data/cookies/bilibili.txt")
    assert m.youtube_cookie_file() == youtube.resolve()
    assert m.bilibili_cookie_file() == (m.BASE_DIR / "data/cookies/bilibili.txt").resolve()


def test_youtube_ytdlp_args_include_cookie_and_runtime(monkeypatch, tmp_path):
    import app.main as m
    cookie = tmp_path / "youtube.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("YOUTUBE_COOKIE_FILE", str(cookie))
    monkeypatch.setenv("YTDLP_JS_RUNTIME", "deno:/usr/local/bin/deno")
    args, has_auth = m.youtube_ytdlp_args()
    assert has_auth is True
    assert "--cookies" in args
    assert str(cookie.resolve()) in args
    assert "deno:/usr/local/bin/deno" in args


def test_cookie_header_normalized_to_netscape():
    import app.main as m
    text = m.normalize_youtube_cookie_text("Cookie: SID=abc; HSID=def; __Secure-3PSID=ghi")
    assert text.startswith("# Netscape HTTP Cookie File")
    assert ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tabc" in text
    assert ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tHSID\tdef" in text
    assert "__Secure-3PSID\tghi" in text


def test_job_cookie_file_overrides_global_cookie(monkeypatch, tmp_path):
    import app.main as m
    global_cookie = tmp_path / "global.txt"
    job_cookie = tmp_path / "job.txt"
    global_cookie.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tglobal\n", encoding="utf-8")
    job_cookie.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tjob\n", encoding="utf-8")
    monkeypatch.setenv("YOUTUBE_COOKIE_FILE", str(global_cookie))
    monkeypatch.setenv("YTDLP_JS_RUNTIME", "deno:/usr/local/bin/deno")
    args, has_auth = m.youtube_ytdlp_args(cookie_file=job_cookie)
    assert has_auth is True
    assert str(job_cookie) in args
    assert str(global_cookie) not in args


def test_youtube_failure_hint_mentions_cookie_and_runtime():
    import app.main as m
    tail = "WARNING: No supported JavaScript runtime could be found\nERROR: Sign in to confirm you’re not a bot"
    hint = m.yt_dlp_failure_hint("https://www.youtube.com/watch?v=abcdefghijk", tail, youtube_auth_used=False)
    assert "Deno" in hint
    assert "cookies" in hint


def test_cobalt_headers_prefer_bearer(monkeypatch):
    import app.main as m
    monkeypatch.setenv("COBALT_BEARER_TOKEN", "jwt-token")
    monkeypatch.setenv("COBALT_API_KEY", "api-key")
    headers, kind = m.cobalt_headers()
    assert kind == "Bearer"
    assert headers["Authorization"] == "Bearer jwt-token"


def test_cobalt_public_api_hint():
    import app.main as m
    hint = m.cobalt_auth_hint("https://api.cobalt.tools", '{"code":"error.api.auth.jwt.missing"}')
    assert "公共 hosted API" in hint
    assert "COBALT" in hint


def test_cleanup_storage_removes_oldest_when_over_limit(monkeypatch):
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    a = JOBS_DIR / "old"
    b = JOBS_DIR / "new"
    a.mkdir(exist_ok=True)
    b.mkdir(exist_ok=True)
    (a / "x.bin").write_bytes(b"a" * 20)
    (b / "x.bin").write_bytes(b"b" * 20)
    import app.main as m
    monkeypatch.setattr(m, "MAX_STORAGE_BYTES", 25)
    cleanup_storage()
    assert not a.exists() or not b.exists()
