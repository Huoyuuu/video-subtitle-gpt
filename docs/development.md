# 开发交接说明

## 项目概况

- Python FastAPI 应用，入口为 `app/main.py`。
- Web 服务端口：`18004`。
- 生产目录：`/home/huoyuuu/video-subtitle-gpt`。
- systemd 服务：`video-subtitle-gpt`。
- 主项目文档：`docs/video-subtitle-gpt.md`。

## GPT 配置

GPT 总结调用位于 `app/main.py::call_gpt`，使用 OpenAI Python SDK 的 Chat Completions 接口。

读取的环境变量：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`

当前默认模型为 `gpt-5.6-sol`。生产环境必须继续使用服务器 `.env` 中已有的 `OPENAI_API_KEY` 与 `OPENAI_BASE_URL`；不要改接其他环境变量或网关。

`normalize_openai_base_url()` 会在 Base URL 末尾不存在版本路径时自动补 `/v1`。

## 测试

使用 uv 运行：

```powershell
uv run --with-requirements requirements.txt pytest -q
```

模型默认值测试位于 `tests/test_openai_config.py`。测试通过 fake OpenAI client 验证：

- 默认模型为 `gpt-5.6-sol`
- 当前请求仍携带 `temperature=0.2`

2026-07-10 全量结果：`24 passed`。

## 部署

代码推送到 `origin/main` 后，服务器 Git hook 会更新 `/home/huoyuuu/video-subtitle-gpt` 并执行 `scripts/deploy.sh`。

生产 `.env` 不会被仓库中的 `.env.example` 覆盖，因此切换模型时还需要确认：

```bash
OPENAI_MODEL=gpt-5.6-sol
```

随后重启并检查：

```bash
sudo systemctl restart video-subtitle-gpt
systemctl status video-subtitle-gpt --no-pager
curl -fsS http://127.0.0.1:18004/
```

## 工作区注意事项

- `data/debug/` 是本地未跟踪调试数据，不要加入版本控制。
- 当前发布包含此前已在工作区完成的前端历史字幕/复制体验优化和 audio-only 流程修复；相关覆盖位于 `tests/test_integration.py`。
- 文档目录已经从 `doc/` 迁移为 `docs/`，后续引用统一使用新路径。