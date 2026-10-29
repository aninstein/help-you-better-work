## ADDED Requirements

### Requirement: Two activity states with continuous listening
通话 SHALL 对外呈现倾听和思考两种活动状态；持续输入 PCM 在两种状态及 AI 播放期间均可接收。约三秒有效静音 SHALL 触发倾听到思考转换，而非强制一问一答。partial MUST 只更新实时字幕，完整输入校正后才成为单条 Talker 消息。

#### Scenario: Long utterance contains five questions
- **WHEN** Talker 连续描述五个问题并停顿约三秒
- **THEN** 系统进入思考、保留一个原话气泡并拆分最多五个问题，不为每个 ASR 片段分别回答

#### Scenario: Speech resumes during refinement
- **WHEN** 原输入尚未提交，Talker 又开始说话
- **THEN** 回到倾听并合并续说，旧校正 revision 无权创建消息或答案

### Requirement: Shared ten second processing deadline
系统 MUST 从静音满足的状态转换开始，为最终识别、校正、拆题、查询、检查和首段音频准备共用最多十秒处理预算。人工等待、用户继续说话和已就绪答案的播放排队 SHALL 单独记录；MUST 不对每个工具或模型重试重新分配十秒。超时 MUST 退出思考、保留待核实问题并显示可恢复状态，迟到结果不得发布，未经 Wiki 检查的内容不得播出。

#### Scenario: Slow candidate check
- **WHEN** 最终检查在剩余预算内无法完成
- **THEN** 不发布候选，保留该问题提醒 Hinter，其他已就绪问题可继续输出，用户可继续说话

#### Scenario: All questions await human help
- **WHEN** 拆出的所有问题都需要 Hinter
- **THEN** 问题保留，通话回到倾听并显示待补充数量，不为等待人工一直显示思考，也不虚构回答

### Requirement: Independent question readiness and public knowledge
系统 SHALL 将普通问题与需要 Hinter 的问题独立路由，普通问题可使用模型知识或有依据的公开网页证据，先准备完成的独立问题可先回答。实际业务指标、账户/ID 查询和其他需人工的类别 MUST 保持挂起。公开搜索词 MUST 仅来自原话的相应问题片段，不得包含私有 Hint 或 Wiki。

#### Scenario: Three ordinary questions and two held questions
- **WHEN** 同一长输入中三问可自动处理，两问需要人工
- **THEN** 三问经校验后先回答，两问独立保留并提醒 Hinter，二者互不阻塞

#### Scenario: Current public fact requires search
- **WHEN** 普通问题需要最新公开资料
- **THEN** 系统只查询该原话片段并用读取到的公共页面证据生成候选，查询失败或预算耗尽则保留该题，不编造实时结果

#### Scenario: A comparison depends on real data
- **WHEN** 一个普通比较问题依赖尚待人工核实的真实指标
- **THEN** 比较问题也等待前置结果交付，其他独立问题可以先答

### Requirement: Late human supplements retain original question identity
人工待处理问题 SHALL 在通话内持续保留，后续新话题 MUST 不将其取消。人工批准后系统 SHALL 将补答绑定原问题与原输入，等当前说话、拆题和播放结束后补答。输出 MUST 串行且最终重新检查最新 Hint/Wiki；原问题已被撤回、封禁或跳过则不得补答。

#### Scenario: Human review completes after another caller turn
- **WHEN** 原五问中的两问获人工处理，此时 Talker 又说了新话题
- **THEN** 新话题可继续处理，两条补答仍指向原问题，在合适输出间隙追加并标注补充回答

#### Scenario: New prohibition arrives while supplement is queued
- **WHEN** 补答已准备而尚未播放，Hinter 新提示改变其约束
- **THEN** 旧候选不播出，使用原问题和最新约束重新生成与校验；被封禁的原题直接失效
