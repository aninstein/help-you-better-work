# 个人员工 Agent 与账户平台规划

日期：2026-10-25。状态：**已按批准规格实施并部署**。

## 需求拆分

| OpenSpec change | 内容 | 入口 |
| --- | --- | --- |
| `adopt-deerflow-personal-agent` | 全面 DeerFlow、通用个人员工/会议助手、通用记录、问题展示与摘要扩展点 | [提案](changes/adopt-deerflow-personal-agent/proposal.md) · [设计](changes/adopt-deerflow-personal-agent/design.md) · [契约](changes/adopt-deerflow-personal-agent/contracts.md) · [任务](changes/adopt-deerflow-personal-agent/tasks.md) |
| `add-account-rbac-mysql` | 手机/邮箱注册、密码登录、Hinter 管理员授权、MySQL、旧数据迁移、Compose 与部署验收 | [提案](changes/add-account-rbac-mysql/proposal.md) · [设计](changes/add-account-rbac-mysql/design.md) · [契约](changes/add-account-rbac-mysql/contracts.md) · [任务](changes/add-account-rbac-mysql/tasks.md) |

## 产品与身份决定

- Toker 统一命名为 Talker，Hint 管理侧统一命名为 Hinter。
- 用户注册手机、邮箱和密码，首期使用手机号或邮箱加密码登录；暂不增加验证码、第三方登录和自助密码找回。
- 一套账户系统，普通注册为 `talker`；管理员授予 `hinter_admin` 后可监管用户并管理权限。
- 初始管理员显示名 `Hinter Admin`，登录名与密码均由部署者首次启动时设置，首次登录改密。
- 登录成功后进入个人 Agent 聊天页，右上角展示登录状态；管理员额外有 Hinter 与用户管理入口。
- 一期按单应用 RBAC 管理，管理员监管所有注册用户；以后可增加团队/人员范围。

## Agent 与数据决定

- 全面使用真实 DeerFlow 执行核，STT/TTS、WebSocket 与页面保留为输入输出适配；不保留另一条自研问答执行链。
- Agent 是通用个人员工，会议记录助手是首个工作配置；历史逐题审核、挂起/补答等行为不再作为核心模型。
- 原始聊天/转写记录是来源；首期保留问题形式投影，摘要形式留作后续扩展。
- 通用 Run 的持续执行、取消和重启恢复仍须实现；本轮不新增历史每题永久扫描/补答队列。
- 所有业务文本与 Agent 持久状态进入 MySQL，包括用户、角色、会话、记录、事件、Wiki/记忆、checkpoint 和 Store；音频/附件内容留持久卷，引用和所有权入库。
- 官方当前存储没有直接的 MySQL 配置，provider 适配是明确的交付项，不能把当前分支已有的 LangGraph 节点视为全面接入完成。
- 旧 Talker 经显式账户映射迁移，无法确定归属的数据进入管理员可见的待认领范围，不能由同名注册用户自动获取。

## 建议实施顺序

1. 固定 DeerFlow 版本并完成真实运行时/MySQL provider 的最小联通验证。
2. 落地 MySQL schema、独立容器、迁移与管理员 seed、账户会话和 RBAC。
3. 完成旧数据盘点/归属映射与可重跑迁移，保留备份和撤回/撤销状态。
4. 接入通用 Agent、记录/事件与 worker，切换新输入并退役旧问答核心。
5. 实现登录/注册、个人聊天、登录态、Hinter 管理页面及问题投影。
6. 调整默认/外部模型 Compose 路径、启动/停止与备份恢复脚本，执行部署后的完整登录授权和真实 Agent 验收。

以上顺序是实施依赖，不是需要额外审批的平台流程。两份任务清单及联合验收记录已更新；以实际运行证据说明上线范围。

## 实施与验收

见 [联合验收记录](../docs/personal-employee-acceptance-20261025.md) 与 [部署运维](../docs/personal-employee-deployment.md)。
