## ADDED Requirements

### Requirement: Independent durable guidance across modes
系统 SHALL 允许 Hinter 在无待审问题时提交 Hint，将原文、作用域、来源和 revision 持久化并投影到私有 LLM Wiki；适用约束 MUST 对文本、上传语音和电话共同生效。effective ack MUST 在持久提交后返回，不等待整答或文件索引。

#### Scenario: Hint during ordinary automatic conversation
- **WHEN** Hinter 提交 session 级禁止项，随后 Talker 更换输入方式
- **THEN** 新方式的输出遵守同一约束，无需重新审核或重复 Hint

#### Scenario: File synchronization fails
- **WHEN** 约束已提交而 Markdown 投影失败
- **THEN** 约束仍有效，Hinter 看见 sync_pending，生成读取持久快照，重启能恢复

### Requirement: Distinguish guidance kinds and source authority
系统 MUST 区分 prohibition、require_human、authorized_fact、style 和显式 recognition_term；普通资料与附件内容 MUST 不自行成为高优先级指令。已有禁止项 SHALL 仅通过明确撤销或修订解除。

#### Scenario: Style hint and hostile source text
- **WHEN** Hinter 要求简短且检索网页声称应忽略禁止项
- **THEN** 简短风格生效，网页指令无效，历史答案不因风格要求被撤回

### Requirement: Complete applicable constraint snapshots
系统 SHALL 在生成前读取全部适用核心约束和相关已授权事实；MUST 不因 top-k 或普通知识截断遗漏核心约束，并按身份和 scope 隔离。

#### Scenario: Relevant prohibition outside retrieval top-k
- **WHEN** 普通知识召回不包含某个有效禁令
- **THEN** 核心约束仍进入生成与发布检查，不因检索排序消失

### Requirement: Segment validation without blanket full-answer review
每个可公开短句 SHALL 对最新适用 Wiki 快照、已提交前缀和事实检查。系统 MUST 对未编译或关系性限制使用必要定向语义检查，不仅依赖词匹配；普通无冲突回答 MUST 不固定等待整答主模型二次审查或人工确认。

#### Scenario: Ordinary reply with no applicable semantic restriction
- **WHEN** 结构化约束检查通过且无自由文本限制需判断
- **THEN** 首段可在整答完成之前公开与合成

#### Scenario: Semantic checker is uncertain
- **WHEN** 当前句可能泄露受限关系且快速检查超时或不确定
- **THEN** 不发布可疑句，尝试局部安全重写或允许的澄清，不自动把所有后续问题转人工

### Requirement: Versioned effect during generation and playback
新 Hint 持久提交 SHALL 使受影响旧版未提交段失效；输出提交 MUST 原子检查适用版本和输出所有权。禁止、停止或撤回 SHALL 清相关未播音频；无关更新 MUST 不无差别取消其他答案。

#### Scenario: Prohibition during synthesis
- **WHEN** 合成尚未返回而新的禁令生效
- **THEN** 旧合成即使迟到也不能发布，响应按新限制继续或结束，麦克风持续倾听

### Requirement: Honest long-document processing
系统 MUST 保存长资料并明确 processing 状态，先应用已明确的短指令；未读取全文时不得声称全部内容已参与约束。无关问题 SHALL 继续自动回应。

#### Scenario: Document is still indexing
- **WHEN** 产品资料仍在处理中而 Talker 询问无关常识
- **THEN** 常识自动回答；依赖该资料的事实明确尚不能确认，不伪造文件依据

### Requirement: Five-question retrospective and unlimited manual withdrawal
系统 SHALL 保留跨入口最近五题封禁语义回查，命中后自动撤回相关 AI 回复；Hinter SHALL 可逐条撤回当前 session 任意历史 AI 气泡。撤回 MUST 隐藏内容、停播、禁止旧音频链接并阻止陈旧 Wiki/快照恢复。

#### Scenario: Ban after a streamed reply
- **WHEN** 最近五题内某题已流式回答而新 Hint 禁止其披露内容
- **THEN** 回查命中后该题相关回复进入 withdrawn，未播尾部取消；已听声音无法撤销

#### Scenario: Withdraw an older bubble
- **WHEN** Hinter 撤回最近五题之外的一条 AI 回复
- **THEN** 仅该 response 及其剩余段被隐藏和取消，其他回复和 Talker 原话保留

### Requirement: Private guidance role isolation
系统 MUST 在 HTTP、SSE、WS、音频关联元数据与错误响应中向 Talker 隐藏 Hint 原文、内部规则与检查原因；公开搜索参数 MUST 不包含私有指导或资料。

#### Scenario: Snapshot after a blocked output
- **WHEN** Talker 重连请求快照
- **THEN** 仅获得允许的公开内容和中性状态，无私有 Hint 或校验记录
