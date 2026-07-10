# 本次工作报告

日期：2026-07-10

## 完成内容

1. 将项目文档目录从 `doc/` 规范化重命名为 `docs/`。
2. 将 GPT 总结默认模型从 `gpt-5.5` / 文档中的旧示例统一更新为 `gpt-5.6-sol`：
   - `app/main.py` 的运行时默认值
   - `.env.example` 的默认配置
   - `docs/video-subtitle-gpt.md` 的部署与环境变量说明
3. 保持原 API 配置方式不变：应用只读取 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`，不引入其他密钥或网关变量。
4. 修正文档中的服务器部署目录，使其与实际部署目录 `/home/huoyuuu/video-subtitle-gpt` 一致。
5. 在 README 中补充默认模型和新文档路径。
6. 新增默认模型回归测试，确保未设置 `OPENAI_MODEL` 时调用 `gpt-5.6-sol`。
7. 本次发布同时包含工作区中已完成的页面复制/历史字幕回看优化，以及“仅下载音频”模式在下载完成后停止处理的修复。

## 上线前验证

- 使用生产服务器原有 `.env` 中的 `OPENAI_API_KEY` 与 `OPENAI_BASE_URL=https://chiyicn.com/v1` 测试 `gpt-5.6-sol`。
- 测试请求：Chat Completions，用户消息 `hi`，并携带应用当前使用的 `temperature=0.2`。
- 结果：请求成功，返回模型为 `gpt-5.6-sol`。
- 自动化测试：`24 passed`。

## 变更原则

- 未修改或迁移生产 API Key。
- 未修改生产 Base URL。
- 未把密钥写入仓库。
- `data/debug/` 等本地调试数据不纳入提交。