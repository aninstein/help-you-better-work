## ADDED Requirements

### Requirement: Versioned deterministic policy decisions
系统 SHALL 对每个 committed question 产生可审计 PolicyDecision，包含动作、规则、原因、证据、问题版本和 policy_revision；合并动作 MUST 采用 deny > hold > notify > auto。模型只提供分类建议，代码决定动作。

#### Scenario: Multiple rules apply
- **WHEN** 同一问题既请求 ID 数据又明确要求危险违法协助
- **THEN** 采用 deny，保留全部匹配原因，不因某个 auto 分类覆盖严格规则

### Requirement: Open domain review rather than all new utterances
系统 SHALL 默认将人生意义等开放哲学、未知开放领域和低置信度意图归为 hold 并通知 Hinter；已知领域普通新措辞 MUST 不因首次出现而全量人工审核。

#### Scenario: Meaning of life
- **WHEN** Talker 问 “What is the meaning of life?” 且无有效领域处理规则
- **THEN** hold 并请求 Hinter 提示；不先自由发挥再补审

#### Scenario: Routine unseen wording
- **WHEN** Talker 第一次用某种说法询问已知常识
- **THEN** 可以 auto，经 Wiki gate 后回应，不要求 Hinter 逐题批准

### Requirement: Contextual harm routing and sourced lexicons
系统 SHALL 结合中英词汇信号、上下文、请求意图和安全规则判断犯罪/暴力风险；明确危险协助 MUST deny，歧义风险 MUST hold，安全教育/引用语境不得仅凭词命中 deny。外部词表 MUST 离线版本化并保留来源、许可、归属和校验值。

#### Scenario: Harmful operational assistance
- **WHEN** Talker 请求实施犯罪或伤害他人的具体操作帮助
- **THEN** 拒绝危险部分、提醒 Hinter，可给经过 Gate 的简短安全替代，人工批准不能绕过硬限制

#### Scenario: Benign terminology
- **WHEN** Talker 说 “How do I kill a process?” 或询问如何防范诈骗
- **THEN** 使用上下文识别其安全意图，不因为 kill/诈骗 单词自动判犯罪

#### Scenario: Unreviewed remote update
- **WHEN** 远端词表改变或新来源缺少许可
- **THEN** 不自动改变运行策略，继续使用固定版本并记录待评估更新

### Requirement: Business data approval before access
系统 MUST 将实际指标、个人/账户/订单/ID 查询路由为 hold，并在数据读取前完成问题审批和独立工具授权；MUST 绑定 tool、resource、args_hash、scope、question_version 与 expiry。通用解释不属于实际查询。

#### Scenario: Explain versus retrieve DAU
- **WHEN** 用户先问 DAU 定义，再要求查询昨天某产品 DAU
- **THEN** 定义可 auto；查询在任何文档预读、MCP、数据库或搜索前等待授权

#### Scenario: Approval without data access
- **WHEN** Hinter 允许回答但没有资源访问权限或数据工具凭据
- **THEN** 不能执行查询或编造结果，说明暂不能核实并向 Hinter 呈现缺项

#### Scenario: Changed query after approval
- **WHEN** 已批准某 ID，但问题被修订为另一个 ID 或授权过期
- **THEN** 旧 grant 不适用，重新 hold，不复用旧数据答案

### Requirement: Call tool capability ceiling
系统 MUST 对 call 使用专属最小工具 allowlist，禁止 bash、任意文件/环境访问与未授权 MCP；生成前预读与失败回退 MUST 受同一授权规则约束。

#### Scenario: Prompt asks for shell bypass
- **WHEN** Talker 或检索文档要求用 shell 读取真实数据
- **THEN** 没有可用 bash 工具且不执行该命令，不因模型认为有帮助而升级权限

### Requirement: Bounded review waiting without implicit approval
hold 问题 SHALL 允许一次简短英文等待话术，默认 20 秒后过期并明确暂不能核实；Hinter 离线、超时或页面未读 MUST 不视为批准。deny MUST 无需等人才能拒绝。

#### Scenario: No Hinter available
- **WHEN** 真实数据查询等待超时且 Hinter 未连接
- **THEN** 不执行查询、不提供实质答案，记录 expired，可继续独立新问题

#### Scenario: Approval after caller moved on
- **WHEN** 已过期问题收到迟到批准
- **THEN** 拒绝过期决定或要求显式重新激活，再校验新版本与上下文，不直接播报

### Requirement: Fail closed with configurable explainable policy
系统 SHALL 支持版本化 policy 配置、回滚和 Hinter 可见原因；分类失败/超时 MUST hold 而非 auto。手工覆核 MUST 不绕过系统安全限制、核心约束或数据权限。

#### Scenario: Classifier unavailable
- **WHEN** 新轮策略分类失败
- **THEN** 该问题 hold，展示错误类别并可重试，其他已获许可输出不被伪造为已审核
