import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

import app.main as m


@pytest.mark.asyncio
async def test_create_job_done_flow(monkeypatch):
    async def fake_get_youtube_transcript(url):
        return "这是一段测试字幕"

    async def fake_call_gpt(prompt, transcript, job_id=None):
        return "这是 GPT 测试输出：" + transcript

    monkeypatch.setattr(m, "get_youtube_transcript", fake_get_youtube_transcript)
    monkeypatch.setattr(m, "call_gpt", fake_call_gpt)

    async with AsyncClient(transport=ASGITransport(app=m.app), base_url="http://test") as client:
        r = await client.post("/api/jobs", data={"url": "https://youtu.be/abcdefghijk", "prompt_name": "default", "custom_prompt": "", "start_mode": "auto"})
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        final = None
        for _ in range(30):
            jr = await client.get(f"/api/jobs/{job_id}")
            final = jr.json()
            if final["status"] == "done":
                break
            await asyncio.sleep(0.1)
        assert final["status"] == "done"
        assert final["progress"] == 100
        assert "测试字幕" in final["transcript"]
        assert "GPT 测试输出" in final["result"]
        assert final["url"].startswith("https://youtu.be/")
        assert final.get("transcript_hash")


@pytest.mark.asyncio
async def test_cache_hit_skips_transcription(monkeypatch):
    async def fake_call_gpt(prompt, transcript, job_id=None):
        return "缓存字幕总结：" + transcript

    async def fail_get_youtube_transcript(url):
        raise AssertionError("cache hit should skip direct subtitle fetch")

    url = "https://example.com/video-cache-test"
    h = m.save_transcript_cache(url, "缓存里的字幕", "test")
    monkeypatch.setattr(m, "get_youtube_transcript", fail_get_youtube_transcript)
    monkeypatch.setattr(m, "call_gpt", fake_call_gpt)

    async with AsyncClient(transport=ASGITransport(app=m.app), base_url="http://test") as client:
        r = await client.post("/api/jobs", data={"url": url, "prompt_name": "default", "start_mode": "auto", "use_cache": "true"})
        job_id = r.json()["job_id"]
        final = None
        for _ in range(30):
            final = (await client.get(f"/api/jobs/{job_id}")).json()
            if final["status"] == "done":
                break
            await asyncio.sleep(0.1)
        assert final["status"] == "done"
        assert final["cache_hit"] is True
        assert final["transcript_hash"] == h
        assert "缓存字幕总结" in final["result"]


@pytest.mark.asyncio
async def test_audio_ready_when_whisper_fails(monkeypatch, tmp_path):
    async def fake_download_audio(url, job_dir, job_id=None):
        p = job_dir / "audio.mp3"
        p.write_bytes(b"fake mp3")
        return p

    async def fake_groq_whisper(audio):
        raise RuntimeError("Groq Whisper 不可用/限额耗尽：429")

    monkeypatch.setattr(m, "get_youtube_transcript", lambda url: None)
    monkeypatch.setattr(m, "download_audio", fake_download_audio)
    monkeypatch.setattr(m, "groq_whisper", fake_groq_whisper)

    async with AsyncClient(transport=ASGITransport(app=m.app), base_url="http://test") as client:
        r = await client.post("/api/jobs", data={"url": "https://www.bilibili.com/video/BV17eVL6GEAh", "prompt_name": "default", "custom_prompt": ""})
        job_id = r.json()["job_id"]
        final = None
        for _ in range(30):
            final = (await client.get(f"/api/jobs/{job_id}")).json()
            if final["status"] == "audio_ready":
                break
            await asyncio.sleep(0.1)
        assert final["status"] == "audio_ready"
        assert final["audio_url"].endswith("audio.mp3")


@pytest.mark.asyncio
async def test_resume_with_manual_transcript(monkeypatch):
    async def fake_call_gpt(prompt, transcript, job_id=None):
        return "手动字幕处理结果：" + transcript

    monkeypatch.setattr(m, "call_gpt", fake_call_gpt)
    job_id = "manual-test"
    m._jobs[job_id] = {"id": job_id, "url": "https://example.com/manual", "status": "audio_ready", "progress": 65, "stage": "请手动处理字幕", "created_at": m.now_iso(), "logs": []}

    async with AsyncClient(transport=ASGITransport(app=m.app), base_url="http://test") as client:
        r = await client.post(f"/api/jobs/{job_id}/transcript", data={"transcript": "手动字幕", "prompt_name": "default", "custom_prompt": ""})
        assert r.status_code == 200
        final = None
        for _ in range(30):
            final = (await client.get(f"/api/jobs/{job_id}")).json()
            if final["status"] == "done":
                break
            await asyncio.sleep(0.1)
        assert final["status"] == "done"
        assert "手动字幕处理结果" in final["result"]
        assert final.get("transcript_url")


@pytest.mark.asyncio
async def test_start_from_summary_only(monkeypatch):
    async def fake_call_gpt(prompt, transcript, job_id=None):
        return "只总结：" + transcript

    monkeypatch.setattr(m, "call_gpt", fake_call_gpt)
    async with AsyncClient(transport=ASGITransport(app=m.app), base_url="http://test") as client:
        r = await client.post("/api/jobs", data={"url": "", "prompt_name": "default", "start_mode": "summary", "manual_transcript": "我已经有字幕"})
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        final = None
        for _ in range(30):
            final = (await client.get(f"/api/jobs/{job_id}")).json()
            if final["status"] == "done":
                break
            await asyncio.sleep(0.1)
        assert final["status"] == "done"
        assert "只总结" in final["result"]


@pytest.mark.asyncio
async def test_retry_creates_new_job(monkeypatch):
    async def fake_call_gpt(prompt, transcript, job_id=None):
        return "重试总结：" + transcript

    monkeypatch.setattr(m, "call_gpt", fake_call_gpt)
    old_id = "retry-old"
    m._jobs[old_id] = {"id": old_id, "url": "", "status": "error", "progress": 100, "stage": "失败", "created_at": m.now_iso(), "logs": [], "start_mode": "summary", "manual_transcript": "重试字幕", "prompt_name": "default", "custom_prompt": ""}

    async with AsyncClient(transport=ASGITransport(app=m.app), base_url="http://test") as client:
        r = await client.post(f"/api/jobs/{old_id}/retry", data={"start_mode": "same"})
        assert r.status_code == 200
        new_id = r.json()["job_id"]
        final = None
        for _ in range(30):
            final = (await client.get(f"/api/jobs/{new_id}")).json()
            if final["status"] == "done":
                break
            await asyncio.sleep(0.1)
        assert final["status"] == "done"
        assert final["retry_of"] == old_id
