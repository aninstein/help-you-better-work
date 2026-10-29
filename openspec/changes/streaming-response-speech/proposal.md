## Why

当前电话先生成完整候选并校验，再分句合成；消息回答还需完整草稿确认后生成音频文件。部分工具模型的 `.astream` 实际被聚合到本轮结束才 yield。长回答首声慢，且推理、合成、播放的取消和交付边界不统一。

## What Changes

- 从模型真正增量消费正文，完整短句即可自动检查、公开和合成，不等待整答结束。
- 为文本、上传语音、电话共用 ResponseStream 与 Segment 模型，按模式选择展示/播放。
- Kokoro 先支持生成中短句流水线；TTS adapter 能扩展双向文本/音频，不把整句 offline generate 冒称模型原生流式。
- 改为有界队列、输出所有权、段号、版本与实际播放确认，贯穿推理、校验、工具、合成和播放中断。
- 已公开/已播内容不可用整答 reset 重写；模型中途失败保留事实和已交付前缀，安全结束或明确续答。

## Capabilities

### New Capabilities
- `incremental-response-delivery`: 真正正文流、短句提交、音频增量、全程可取消与真实交付。

## Impact

修改 VoiceAgent / CallReasoner / ModelRouter 的流式契约、CallService.respond、消息生成/发布/TTS、speech adapters、前端 SSE 气泡、useVoiceCall 播放/音频协议和恢复。使用 [B](../async-hinter-wiki/proposal.md)的最新约束验证、[A](../default-auto-response/proposal.md)的默认自动及 [C](../conversational-call-feedback/proposal.md)的回应类型。实施时修订 constitution VI。

状态：核心功能已实施；详见[实施记录](implementation-status.md)。[技术设计](design.md) · [协议](contracts.md) · [任务](tasks.md) · [总览](../../response-flow-redesign.md)。
