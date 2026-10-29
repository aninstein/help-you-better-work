## 1. 规格与性能基线

> 第一版已实施，详见 [实施状态](implementation-status.md)。用户后续要求优先功能落地；本版采用持续 PCM 路径，未完成的生产化/全量基准条目继续保留为后续事项，不作为当前功能试用的前置门禁。

- [ ] 1.1 按 design 的 D1/D7 和迁移计划修订 constitution I/V/VI 的模式适用范围；更新文档，声明 call 基于 PolicyDecision + Wiki Gate，旧模式继续人工确认。
- [ ] 1.2 为现有 session 增加阶段耗时、锁等待和真实播放测量；保存固定工作负载基线，人工等待单列。（voice-latency-observability）
- [ ] 1.3 推理移出 session 锁，增加 compare-and-set；复用 Whisper/模型客户端、缓存 Wiki revision、移动阻塞 IO；验证同等输入提速与旧审核行为不变。
- [ ] 1.4 控制事件由轮询全量快照改为可唤醒增量派发，保留 snapshot、SSE、幂等、游标/gap恢复和角色投影。

## 2. 媒体与模型可行性验证

- [ ] 2.1 在目标 Mac 和 Docker/Linux 验证 Pipecat/SmallWebRTC 与当前 av/Python 依赖交集；锁包版本与模型快照，记录代码/权重/voice许可和校验值。
- [ ] 2.2 验证 sherpa-onnx 英文 online 模型持续识别、Silero endpointing、Kokoro 分句合成/首块时间，记录预热、CPU/内存、实时率、英文数字/口音/噪声结果。
- [ ] 2.3 验证前后端真实持续双向媒体、HTTPS/STUN/TURN与浏览器权限；明确不支持环境行为，禁止以文件上传假替代。
- [ ] 2.4 在随机位置验证接收端真正停播、RTP尾音、队列清理和新epoch接收路径；无法满足时先修媒体停止机制再接入自动回答。

## 3. Call 状态与持续轮次

- [ ] 3.1 添加兼容默认 mode、call/turn/epoch 状态与幂等事务；实现 start/signal/mute/hangup，单 session 单 owner。（realtime-call-session）
- [x] 3.2 新增独立通话媒体 hook/控件，持续字幕和音频；Hinter 默认静音观察，复用旧聊天与 Wiki UI。
- [x] 3.3 实现 partial/final/revision、自然 endpointing 与来源归档；partial只可预警/只读预取，禁止副作用。（streaming-turn-orchestration）
- [ ] 3.4 将 Intake 输出扩为问题顺序/依赖/置信度；普通问题自动路由、按轮去重，追问重说不被吞，实体歧义澄清。
- [ ] 3.5 实现 per-question hold 与独立问题调度，单活动输出；新增短英文 call prompt，保留原私有草稿 prompt。
- [ ] 3.6 实现断线暂停、新connection epoch、状态补齐和10秒恢复超时；重启结束旧call，不回放未完成音频。

## 4. 策略与工具门禁

- [ ] 4.1 实现 policy-seed 的版本化 schema、分类/规则合并和 fail-closed；开放新领域、危险协助与数据查询分别验收。（risk-based-review-routing）
- [x] 4.2 离线审阅并导入固定中英词表子集，保留 CC BY 归属、来源commit/sha256/排除规则；补充语境和规避表达评测，词表不直接等同法律判断。
- [ ] 4.3 实现 hold 20秒等待/过期、人工批准/拒绝/重新激活，Hinter离线和超时不自动放行。
- [ ] 4.4 实现call专属工具allowlist与 ToolGrant；阻止bash/MCP/文档预读/缓存/搜索绕过数据许可；参数/ID/期限变化使授权失效。

## 5. Hinter 监管与持久约束

- [x] 5.1 新增滚动监管时间线、实际播出状态、阅读历史时的新消息提示、聚合例外待办/声音/角标与离线恢复。（hinter-live-supervision）
- [x] 5.2 实现 untargeted hint 的服务端target解析，五类intervention、幂等和版本冲突；stop不等待模型或文件投影。
- [ ] 5.3 实现 ConstraintService 持久原文/修订/scope/有效期、SQLite事务及outbox、Wiki原子投影和恢复；UI区分effective/indexing/wiki_synced。（constraint-wiki-output-gate）
- [ ] 5.4 核心约束独立装配，处理超限/冲突/长文档/短数字歧义；普通Wiki编辑与清空接入约束失效机制，禁止绕过revision。
- [ ] 5.5 新事实/约束revision更新时撤销相关permit，区分call级失效与显式跨会话范围；防止客户/用户scope混入。

## 6. 输出 Gate 与音频取消

- [ ] 6.1 实现完整短答缓冲、确定性+语义Wiki检查、候选hash和版本OutputCheck/Permit；修复最多一次，失败hold，不向Talker泄露候选。
- [ ] 6.2 只对校验候选分句TTS并逐批复核epoch/revision；阻断框架默认RTVI bot text/音频旁路。
- [x] 6.3 实现Talker插话、Hinter纠正/停止、挂断的LLM/TTS取消与陈旧worker拒绝提交；控制事件优先，不因推理占锁等待。
- [ ] 6.4 记录播放ack、已播/未播/精度/中断及历史check依据；未播内容不进入已告知上下文，自动答案不升级为人工确认长期知识。

## 7. 验收、回归与演示

- [ ] 7.1 旧review_chat回归：上传/文字、AI私有草稿、人工确认/拒绝、原音重播、Wiki与新Session；活动call模式冲突测试。
- [ ] 7.2 连续10分钟/至少20轮真实浏览器通话，无反复录音上传；覆盖多问题/依赖、感谢/无问号请求、重复final、要求重说、低置信数字与长停顿。
- [ ] 7.3 策略矩阵：人生意义hold/普通常识auto、危险协助deny/安全教育反例、DAU解释auto/真实DAU及ID查询hold；审核离线/超时/迟到批准/工具授权变更。
- [ ] 7.4 竞态矩阵：hint先于语音、partial时、生成中、Gate通过但TTS未发、播放中、挂断后、重连后；全部验证无旧epoch播出。
- [ ] 7.5 故障注入：数据库失败、Wiki投影失败/重启修复、长文档索引失败、Gate/ASR/TTS超时、队列超限、控制断开但媒体仍在；全部保守处理且可恢复。
- [ ] 7.6 角色canary契约：HTTP/WS/SSE/RTVI/错误/音轨与Wiki均验证不泄露私有hint/候选/证据；Hinter不默认采音/双播放。
- [ ] 7.7 标准环境至少100轮TTFA及100次打断/提示测试，报告P50/P95、失败数、冷启动/工具/人工等待；对照现有session基线，不用等待话术替代实质首音。
- [ ] 7.8 通过验收后启用feature flag并提供演示脚本/阶段耗时/输入时序说明；验证关闭flag可回退旧模式且数据不丢，再归档OpenSpec change。

以上为原始实施计划；已交付的第一版主流程与尚未执行的生产化验收以 implementation-status.md 为准。
