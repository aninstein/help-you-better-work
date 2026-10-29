## ADDED Requirements

### Requirement: Measured current session optimization
系统 SHALL 在优化现有 session 前后用同一工作负载测量音频接收、STT、Intake、锁等待、生成、人工等待、Wiki、TTS 和播放阶段，人工等待与软件耗时分列。外部推理 MUST 移出 session 临界区并在提交时复核版本。

#### Scenario: Long intake concurrent with stop
- **WHEN** Intake 等待模型时另有 Hinter stop/提示命令
- **THEN** 提示控制不被该模型调用持锁阻塞，旧 Intake 只在版本有效时提交

#### Scenario: Performance report
- **WHEN** 报告现有 session 提速
- **THEN** 包含相同输入、模型、硬件、样本数、分位数与前后耗时，不把人工点击时间减少冒充模型速度提升

### Requirement: Warm reusable adapters and bounded hot path
系统 SHALL 预热复用 ASR/TTS/模型客户端，按 revision 缓存 Wiki，使用有界增量控制事件；CPU 推理、同步文件工作与音频归档 MUST 不阻塞控制 event loop。热路径 MUST 不每帧读全 Wiki/写全 session。

#### Scenario: Second transcription
- **WHEN** 同一服务进行第二段旧模式转写或新 call 轮次
- **THEN** 复用已加载模型或隔离推理服务，不为每个请求重载模型；首次冷启动单列

### Requirement: End to end metrics and clock discipline
系统 SHALL 关联 call/turn/question/response ID 记录真实语音末尾、endpoint、ASR final、决策、候选完成、Gate、首音收到/可听、hint effective 和 playback stop；MUST 用同端 monotonic 时钟或有误差记录的跨端校准，不直接相减不同机器墙钟。

#### Scenario: Cached filler precedes answer
- **WHEN** 系统先播放 “Let me check” 再给实质回答
- **THEN** 分别记录 acknowledgment 与 substantive TTFA，不能用 filler 首音宣称实质回答达标

### Requirement: Proposed warm call latency acceptance
依据后续 `fix-call-turn-taking` 的三秒停顿策略，系统 SHALL 将语音末尾到首音的总耗时拆为有意静音等待、最终ASR、独立校正和commit后回答/播放，并报告实际样本数、耗时和失败。原语音末尾起算P50≤1.5s/P95≤3s与新产品等待冲突，MUST 不再作为本轮达标依据；不能靠改起点排除三秒后声称满足旧目标。冷启动、hold和故障 SHALL 单列；本轮只做代表性回放/实听，不要求百轮基准作为功能前置门禁。

#### Scenario: Standard warm benchmark
- **WHEN** 执行本轮代表性固定语音回放和浏览器实听
- **THEN** 从真实语音结束到首个实质可听音频记录总耗时，分别报告三秒等待和各处理阶段，连同实际样本数及失败/超时，不虚报未测量的分位数

### Requirement: Intervention responsiveness acceptance
系统 SHALL 分别测量有效语音开始到插话确认、插话确认到本地实际停播，不能将300/800ms人声确认等待隐藏在指标外；确认后的停播拟定目标≤200ms，尚非实测结论。Hinter stop服务端接收到停播ack、短提示收到到durable effective仍分别记录，拟定目标≤300ms；本轮不要求100次测量作为前置门禁。停止和提示生效 MUST 分别测量。

#### Scenario: Stop at arbitrary audio position
- **WHEN** 在代表性播放位置检查有效插话和Hinter停止
- **THEN** 使用接收端播放与音频采样确认停播，记录检测等待和尾音；不能只用发送stop事件时间作为成功

### Requirement: Backpressure and degraded behavior
系统 SHALL 限制每 call 待发音频队列≤250ms、输入等待队列≤1s；超限或依赖超时 MUST 可观察地降级、停止旧输出并请求重说/重试，不能无限积压。性能不足不得通过绕过策略或 Gate 补偿。

#### Scenario: Slow synthesizer or network
- **WHEN** TTS/网络使音频队列超限
- **THEN** 停止/丢弃过期 epoch，显示 degraded，不在数十秒后自动补播旧答案

### Requirement: Reproducible conversation acceptance
系统 SHALL 提供可复现持续通话验收，覆盖至少10分钟/20轮、插话、提示前后时序、三类风险、Hinter 离线、断线恢复和角色 canary；新模式验收 MUST 同时包含旧模式回归。

#### Scenario: Full demonstration
- **WHEN** 执行实施清单中的通话演示与竞态用例
- **THEN** 无需重复上传即可连续对话，策略命中正确，提示与旧音频不串线，私有内容不泄露，旧审核聊天仍可用；失败项明确记录而非省略
