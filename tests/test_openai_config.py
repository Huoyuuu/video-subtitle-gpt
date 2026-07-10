from types import SimpleNamespace

import pytest

import app.main as m


@pytest.mark.asyncio
async def test_call_gpt_defaults_to_gpt_5_6_sol(monkeypatch):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return {"choices": [{"message": {"content": "ok"}}]}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(m, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    result = await m.call_gpt("总结", "测试字幕")

    assert result == "ok"
    assert captured["model"] == "gpt-5.6-sol"
    assert captured["temperature"] == 0.2