# 设计

## 数据归属
SQLite 增加 Talker 注册表，Session 增加 talker_id、talker_name 和 talker_user_id。历史 Session 自动归入默认 Talker；其 Wiki 沿用原目录，避免迁移丢失。新 Talker 使用独立 Wiki 根目录。Talker 的公开记忆与 Hinter 私有约束是同一 Talker 空间内的两种可见性，不是两份身份所有权。Hinter 可编辑选中 Talker 的记忆、查看 private/ 下的约束；Talker 不接收私有文档。

## 首页与目录
首页创建 Talker 时同时建立首个 Session；列表显示名称、会话数与最近会话入口。唯一 Hinter 入口进入工作台，左侧目录以 Talker 分组，展示 Session 日期、状态与待补充数量。窄屏可折叠目录。选择空 Talker 可新建会话。

## 原子切换
URL 保存 role、talker、session。切换时废弃旧连接和请求结果，先清理原会话视图，再加载目标快照；整个工作区按 Session 重建，问题选择、提示表单、Wiki 编辑器、监管面板不混用旧数据。目录始终可用，加载失败可切换重试。同一 Talker 切换会话保持同一 Wiki；跨 Talker 切换全部记忆与约束。Hinter 观察历史会话不会因为 Talker 新建会话被自动跳走，也不会挂断对方通话。

## 后端贯通
生成、提示投影、记忆整理、撤回清理、Wiki API 全部通过 Session 找到 Talker Wiki。Hint 范围键增加 Talker 身份，默认 Talker 保留旧键兼容已有约束。Wiki 写入事件只通知所属 Talker 的会话。清空 Wiki 只作用于当前 Talker，沿用现有确认交互。

## 验证边界
仅做构建、静态检查和两个 Talker/多个 Session 的针对性功能验证，不扩充历史审核测试或新门禁。本地 Demo 身份令牌不是生产多用户认证系统。
