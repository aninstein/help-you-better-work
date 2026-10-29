# 接口与状态增量

协议3已用于新客户端；协议2继续服务未升级客户端，start请求未声明协议时按2兼容。

## 服务内接口

| 组件 | 输入 | 输出与边界 |
| --- | --- | --- |
| EndOfTurnAdapter | call/turn/revision、末尾≤8秒16k PCM、音频范围 | p_complete、model_revision、duration_ms；不直接提交消息，不做说话人识别 |
| TurnController | VAD、端点预测、稳定字幕、转写完整性/槽位信号、当前call停顿统计 | wait / candidate_end / commit / clarify；返回decision_id和使用的音频revision |
| InterruptionDecision | 当前有效人声、AI输出阶段、是否期待确认、稳定词片段、可用回声证据 | backchannel / candidate / interrupt / non_interactive / uncertain；不能仅凭800ms时长确认 |
| RecognitionContext | 明确可给ASR的公开/授权术语及发音 | glossary_version与有界词表；不包含完整Hint、客户业务事实或工具输出 |
| TranscriptRefiner | 本轮ASR证据、有限Caller上下文、授权术语 | verified_passthrough / corrected / degraded / needs_clarification，保留route与调用状态；不检索Wiki/工具 |

端点预测只接受同一turn最新revision，恢复说话、静音、丢帧、断线和挂断会使旧结果作废。正式消息与业务Agent仍只在commit后启动一次；候选问题提取只能是本地临时信息，不得提前查询或记为已问事实。`clarify`可冻结并提交一次needs_clarification输入，挂起业务动作，只经现有检查路径发出局部澄清。

## 媒体协议3

`ready`协商`turn_protocol=3`、输入采样率、帧大小、endpoint_mode和可用能力。协议2继续用于已有通话，不能把有头的新帧交给旧的裸PCM解码器。

输入帧在PCM前包含20字节头：4字节ASCII `DVP3`、uint32 seq、uint64 sample_start、uint16 sample_count、uint16保留flags=0；整数为little-endian。后接sample_count个mono S16LE样本，长度必须吻合ready协商的帧限制。sample_start按实际采集连续推进，即使某帧因积压没有发送也不回退；不得用不同客户端墙钟代替音频样本时钟。缺失或重复范围必须可检测。发生不可恢复缺口时标记受影响输入并请求局部澄清，不填零制造“用户已停顿”。

输出仍保留response_id与epoch；新增候选降音事件不改变epoch。若传输将来改RTC，这些业务ID和决策契约也不依赖房间实现。

| 控制事件 | 关键数据 | 客户端行为 |
| --- | --- | --- |
| input.endpoint_candidate | turn/revision/decision_id | 继续显示倾听，不创建气泡 |
| input.endpoint_decided | turn/revision、reason_code、effective_pause_ms | 已确认交接才进入思考；内部概率仅Hinter诊断可见 |
| playback.duck | response_id/epoch/candidate_id、gain、短期有效期 | 只降低对应当前输出音量，不停止采集、不清队列；过期自动恢复 |
| playback.restore | response_id/epoch/candidate_id | 只恢复仍匹配的未撤销输出，旧事件不能影响新回复 |
| interruption.confirmed / stop | 沿用epoch推进及原因 | 立即停播并清旧队列，迟到restore不能复活被取消输出 |
| input.degraded | 原因：endpoint_unavailable/audio_gap/asr_quality等 | 原字幕区提供一次可恢复说明，不泄露私有内容 |

## 快照与诊断

Talker公开字段保持简洁：activity_state、当前字幕、endpoint_mode、需要用户知道的降级状态和待补充数量。概率、词汇来源、原始识别质量与设备参数仅按角色给必要诊断，不默认在界面展开；设备ID和音频原文不写通用遥测。

阶段记录使用同端monotonic时间：最后有效人声、端点决定、最终ASR就绪、校正选择/完成、正式commit、候选完成、Wiki完成、首音发送、浏览器首音播放、候选插话/确认/停播。音频样本时间和墙钟不直接相减；跨端只在校准并保留误差后比较。

十秒的软件处理预算继续关联一次输入/补答，不因内部重试重复分配；听用户续说、人工等待和已准备答案的排队播放另列，但真实语音末尾到可听首音总时间也必须报告。新转写、临时意图、来源与诊断字段统一继承撤回隐藏规则，不允许从“识别详情”恢复被撤回内容。

隐藏只作用于实际被撤回/封禁隐藏的对象；单条AI回复被人工撤回，不等于删除未撤回的Talker原话或其他问题。候选降音500ms内仍不确定时，恢复仍有效的输出并继续采集，等待新的稳定词/显式中断证据；同一候选不逐帧重新降音，不按时长自动升级为确认。

识别词表：Hinter POST `/api/v1/sessions/{session_id}/calls/{call_id}/recognition-context`，body为`{terms: string[]}`，返回`version`与`effective: next_turn`。start显式传`{turn_protocol:3}`启用新帧；ready包含`frame_samples:512`和真实`endpoint_mode`。`media.settings`只接受采样率、声道数和AEC/NS/AGC状态，不记录deviceId。音频恢复后发`input.recovered`清除临时缺帧提示。
