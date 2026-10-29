# Frontend

React + TypeScript + Vite 双通道 Agent 页面。支持真实接口、实时会话、录音与回放、审核和可编辑 Wiki。

本目录开发命令：

```bash
pnpm install --frozen-lockfile
pnpm dev
```

开发服务器监听 `127.0.0.1:8818`，代理 API 到 `127.0.0.1:8820`。完整构建与一键启动在根目录 `scripts/`，说明见 [技术实现](../docs/technical-implementation.md)。
