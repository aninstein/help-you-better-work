# 选型依据与适配边界

核对日期：2026-10-25。以下是官方当前文档事实，实施时必须固定实际版本重新核对；目标设计与验收要求见本 change 的 design/specs。

1. [DeerFlow 官方仓库](https://github.com/bytedance/deer-flow) 将 2.x 定位为提供工具、Skills、子 Agent、记忆与沙箱的 harness；仅自建 LangGraph 节点不能等同完整接入。
2. [官方 embedded client 源码](https://github.com/bytedance/deer-flow/blob/main/backend/packages/harness/deerflow/client.py) 提供 `DeerFlowClient` 的会话与流式入口。本项目拟从这里接入执行核，身份与产品 API 由自身接入层统一。
3. [官方数据库配置样例](https://github.com/bytedance/deer-flow/blob/main/config.example.yaml) 的统一持久化配置列出 memory、SQLite、PostgreSQL；[引擎实现](https://github.com/bytedance/deer-flow/blob/main/backend/packages/harness/deerflow/persistence/engine.py) 有对应分支，不能假设填入 MySQL URL 即可工作。
4. [官方后端架构](https://github.com/bytedance/deer-flow/blob/main/backend/README.md) 描述工具、子任务、记忆与运行记录。其默认记忆/持久化选择不直接证明满足本项目的多用户 MySQL 要求。

因此，本方案要求明确实现/验证 MySQL provider、身份传播与记录映射。通用 Run 恢复、RBAC 与问题展示投影是本项目的交付契约，不将其作为无需接线就自动具备的上游保证。
