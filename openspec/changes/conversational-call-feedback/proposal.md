## Why

电话里长时间无声会让用户误以为没听见或卡住。当前没有区分“已经理解并在做事”“需要澄清”“确实无法执行”，处理失败还会转人工。需要先有自然回应，再持续完成任务，且不以口头禅掩盖漫长等待。

## What Changes

- 直接可答就直接说；真实慢查询/处理先简短回应再做事，后续按真实进展补充。
- 引入独立可取消 WorkItem 和 ReplyAct，慢任务不占据整段对话，结果关联原问题回来。
- 口头回应不经过主 Agent 长检索，不重复堆积，不声称不存在的工具或已经完成的动作。
- 保留倾听/思考主状态与持续麦克风；确认插话可打断回应、思考、工具关联输出和结果播放。
- 分别测首次回应、首个实质结果和任务完成，保留十秒内无无声卡死的边界。

## Capabilities

### New Capabilities
- `conversational-call-feedback`: 电话自然接话、真实进度、异步慢任务和补答。

## Impact

涉及 CallService 调度、受限工具执行事件、回复类型、播放优先级和前端倾听/思考状态。依赖 [A](../default-auto-response/proposal.md)的精简策略、[B](../async-hinter-wiki/proposal.md)的约束、[D](../streaming-response-speech/proposal.md)的统一输出；不更换输入识别模型。

状态：核心功能已实施；详见[实施记录](implementation-status.md)。[设计](design.md) · [任务](tasks.md) · [总览](../../response-flow-redesign.md)。
