# 开发交接说明

## 项目概况

- Python FastAPI 应用，入口为 `app/main.py`。
- systemd 服务名与部署目录由部署环境自行配置，不在公共文档记录具体值。
- 主项目文档：`docs/video-subtitle-gpt.md`。

## GPT 配置

GPT 总结调用位于 `app/main.py::call_gpt`，使用 OpenAI Python SDK 的 Chat Completions 接口。

读取的环境变量：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`

当前默认模型为 `gpt-5.6-sol`。生产环境继续使用现有 OpenAI 兼容配置，但 API Key 和 Base URL 的真实值不得写入文档、测试、日志样例或版本库。

`normalize_openai_base_url()` 会在 Base URL 末尾不存在版本路径时自动补 `/v1`。

## 测试

使用 uv 运行：

```powershell
uv run --with-requirements requirements.txt pytest -q
```

模型默认值测试位于 `tests/test_openai_config.py`，验证默认模型和当前请求参数。

2026-07-10 全量结果：`24 passed`。

## 部署

通过项目既有部署流程发布。公共文档仅使用以下占位符：

- `<server-host>`：服务器地址
- `<app-dir>`：应用目录
- `<repository-url>`：仓库地址
- `<your-openai-compatible-api-base>`：OpenAI 兼容接口地址

生产 `.env` 不会被 `.env.example` 覆盖。发布时只确认模型配置，不输出或记录密钥与接口地址：

```bash
OPENAI_MODEL=gpt-5.6-sol
```

## 工作区注意事项

- `data/debug/` 是本地未跟踪调试数据，不要加入版本控制。
- 当前发布包含前端历史字幕/复制体验优化和 audio-only 流程修复；相关覆盖位于 `tests/test_integration.py`。
- 文档目录统一使用 `docs/`。
