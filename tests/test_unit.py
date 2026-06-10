from pathlib import Path

from app.main import extract_youtube_id, format_youtube, load_prompt, cleanup_storage, JOBS_DIR


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
