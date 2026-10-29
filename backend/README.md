# Backend

FastAPI 单服务：HTTP/WS/SSE、LangChain Agent、SQLite 会话状态、独立 Markdown Wiki 与语音适配器。

在项目根目录运行 `./scripts/build.sh`，再运行 `./scripts/start.sh`；前端和 API 同源提供于 `http://127.0.0.1:8818`。

本目录开发模式：

```bash
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8820
```

检查：`.venv/bin/pytest`、`.venv/bin/ruff check app`。更多设计与 Docker 命令见 [技术实现](../docs/technical-implementation.md)。

Agent 已支持 Web Search、飞书文档读取、bash、Skills 和 MCP。扩展管理使用本地 CLI：
`.venv/bin/python -m app.agent.extensions status`；配置与安装方法见 [Agent 工具说明](../docs/agent-tools.md)。
