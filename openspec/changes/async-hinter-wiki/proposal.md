## Why

Hint 应是独立的控制与知识通道，通过已存储约束影响 AI 的当前和后续回答。现有通话约束、消息审核 Hint、普通 Wiki 编辑路径不统一，仍依赖完整答案后的检查，难以支持自动流式输出。

## What Changes

- 提取跨文本、上传语音和电话的 ConstraintService：持久原文、作用域、版本与撤销关系，投影到私有 LLM Wiki。
- 核心有效约束独立于普通检索 top-k，生成前读取，短句发布时重新验证；不等待 Hinter 批准。
- 取消无差别整答主模型检查，改为结构化检查与必要的定向短句语义检查；新 Hint 撤销受影响的旧输出许可。
- 区分禁止、必须人工、已授权事实、风格和识别术语，不把每个 Hint 当封禁或人工门禁。
- 保留并推广最近五题封禁回查、全部历史 AI 气泡手动撤回和角色隔离。

## Capabilities

### New Capabilities
- `asynchronous-wiki-guidance`: 跨入口持久约束、异步 Hint、发布一致性与撤回。

## Impact

从 calls.py 抽取已有约束与撤回逻辑，接入 service.py/voice_agent/Wiki adapter/API/SSE/WS；共用 SQLite 持久源，不新建独立向量库或审核服务。与 [A](../default-auto-response/proposal.md)约定人工例外，与 [D](../streaming-response-speech/proposal.md)约定段级提交。

状态：核心功能已实施；详见[实施记录](implementation-status.md)。[设计](design.md) · [契约](contracts.md) · [任务](tasks.md) · [总览](../../response-flow-redesign.md)。
