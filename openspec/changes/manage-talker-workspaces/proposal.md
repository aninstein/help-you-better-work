# Talker 空间与统一 Hinter 工作台

## Why
固定两个 Demo 身份和全局活动会话无法支持多 Talker；Wiki 的所有权也被误映射为角色。需要以 Talker 为根组织个人助手空间。

## What Changes
- 首页创建 Talker、列出已存在的 Talker，并进入各自空间；Hinter 只有一个入口。
- Session 明确归属 Talker，Hinter 通过 Talker → Session 目录选择整个工作空间。
- LLM Wiki 归属 Talker，同一 Talker 跨 Session 共享，不同 Talker 相互独立。
- 历史数据保留在默认 Talker 下；保留现有聊天、通话、异步提示与撤回能力。

## Scope
实现本地 Demo 的持久化空间目录与切换，不引入账户注册、租户权限平台、审批门禁或历史测试迁移。
