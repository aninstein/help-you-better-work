## Why

当前消息必须审核和确认，通话的开放领域、数据类、模糊类甚至超时都容易进入人工队列。问题被记录后不能自然得到回应，Hinter 变成逐题值班员。用户要求三种入口默认自动答，只有少数明确例外等待人工。

## What Changes

- 文本、上传语音、电话统一自动路由：直接回答、公开工具、向 Talker 澄清、安全拒绝、明确能力限制、少量人工等待。
- 记录问题和通知 Hinter 不再隐含阻塞；人生意义、新话题、一般歧义和失败不默认 hold。
- 把禁止回答与必须人工区分：禁止项直接遵守；只有有效的 `require_human` 规则或必须由人工提供的非公开依据进入人工待补。
- 旧草稿不批量公开；新输入不依赖旧 `ai_mode` 开关、review ID、二次确认。
- 独立题先答；需要人工的子问题独立保留和补答。实际数据库/写操作权限不随策略放宽而扩大。

## Capabilities

### New Capabilities
- `automatic-response-routing`: 三入口默认自动、少量例外、逐题待补和历史迁移。

## Impact

涉及 service.py 的 intake/review/generate/publish、call_policy/call_reasoner、问题状态与角色投影、QuestionPanel/ReviewComposer/AI 模式开关、模式切换和恢复。实施时修订 constitution I/V。约束服务由 [B](../async-hinter-wiki/proposal.md)负责，输出流由 [D](../streaming-response-speech/proposal.md)负责。

状态：核心功能已实施；详见[实施记录](implementation-status.md)。[总览](../../response-flow-redesign.md) · [设计](design.md) · [拟定策略](policy-examples.yaml) · [任务](tasks.md)。
