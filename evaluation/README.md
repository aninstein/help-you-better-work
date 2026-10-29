# 双通道客服 Agent 评测集

本目录包含 **57 条当前自动回答链路的用户反馈回归场景**，以及保留历史协议的 **24 个跨域场景包**（120 条主链路用例，外加 24 条跨轮记忆链）。当前回归集见下方“本次用户反馈回归集”。

原跨域场景包验证以下草稿审核链路，保留供已有报告比较：

```text
Talker 输入 → Intake 拆题 → Hinter 审核/提示/阻断 → AI 答复 → 正向反馈 → Wiki 记忆 → 后续追问
```

每个场景包都固定覆盖五种交互模式：

| 模式 | 要验证的能力 | 历史执行协议 |
| --- | --- | --- |
| `direct` | Talker 的正常问题能获得无额外业务提示的答复 | 旧协议由 Hinter 以空 hint 请求草稿并确认发送；当前自动回答是否绕过不必要审批，由新增回归集验证。 |
| `hinter_hint` | Hint 能纠正重点、格式或风险边界，且不泄露给 Talker | Hinter 带私有 hint 请求草稿并确认发布；检查答案采纳证据与 Talker 投影隔离。 |
| `hinter_block` | Hinter 可阻断高风险/越权问题 | 旧协议检查拒绝后无实质生成、无敏感内容，并发送 `Sorry, I dont know.`；当前禁令的固定英文回复和完成状态由新增回归集验证。 |
| `multi_question` | 一条消息中的多个独立意图被拆为可审核子问题 | 提交一个复合句，检查数目、关键意图、批量审核后的答复。 |
| `rapid_burst` | AI 尚未回答时连续输入的多条消息不串题、不丢题 | 不等待 Intake 完成便连续提交，再分别收集并审核本批问题。 |

每个场景随后运行 `memory_chain`：带 hint 的首问 → Talker 正向确认并声明应保存的信息 → 新会话追问。这样可以同时看到“本轮上下文可用”和“跨会话长期记忆真的落盘”是否成立。

## 本次用户反馈回归集

[user-feedback.json](regressions/user-feedback.json) 新增 **57 条场景、7 类问题**，覆盖当前自动回答链路：

| 分类 | 场景数 | 重点 |
| --- | ---: | --- |
| 路由与数据范围 | 10 | 普通题直接回答；公开天气、TikTok 指标先搜索；内部数据缺依据才请求 Hinter；明确人工确认规则与混合子题 |
| 禁令与拒答 | 13 | 命中时完整发布 `I don't know.`；next_turn 边界、转写歧义、历史规则污染、无关问题误拒答 |
| 语音整理 | 6 | 去口头禅、重复和明显误词；城市不清才澄清；保护否定和标识；纯口头禅不建题 |
| Hinter 与重试 | 9 | 匹配事实替换失败搜索；人数不能回答营收；取消过期查询；Try again 复用原问题 |
| 联网证据 | 7 | 搜索 RSS 兼容、天气时效、部分证据、原文引用、候选页读取、已完成搜索不重复执行 |
| 回复生命周期 | 9 | 单问题原文直出；多问题原位更新并播报变化项；不刷失败卡片；音频不耗尽生成预算 |
| 工具扩展 | 3 | 飞书文档读取与认证错误、当前回答链路的 MCP、本地文件 bash 工具 |

每条场景都保存输入步骤、上下文、原始失败表现、明确期望、已有确定性测试引用及人工检查项。合成数值不能用作真实经营指标；实时天气不固定温度。原始用户录音、会话标识和认证信息不进入题库。

这部分不沿用下方旧协议的“先生成草稿再审批”前提。当前策略是默认自动回答；缺少内部事实或命中明确人工确认规则才请求 Hinter；有效禁令以正常完成的可见英文拒答结束，不标为生成失败。

```bash
# 同时检查原 24 个场景包与新增回归集；校验测试引用确实存在
backend/.venv/bin/python evaluation/scripts/validate_catalog.py

# 运行 57 条场景关联的确定性测试，去重执行；报告每条场景的结果
backend/.venv/bin/python evaluation/scripts/run_regressions.py

# 查看场景，或按分类/单题运行
backend/.venv/bin/python evaluation/scripts/run_regressions.py --list
backend/.venv/bin/python evaluation/scripts/run_regressions.py --group prohibition
backend/.venv/bin/python evaluation/scripts/run_regressions.py --case public-shanghai-weather
```

确定性执行器使用已有测试中的模型和网络替身、合成数据及临时存储，不连接当前 Demo，也不等于真实模型、联网或音频验收。报告明确将 `live_status` 与 `manual_rubric_status` 记为 `not_run`；跳过、漏收集或失败的测试都不能算场景通过。场景步骤和语义期望供真实回放及人工检查使用，执行器运行的是 `pytest_nodes` 对应的测试断言，不会把这些文本自动转成模型测试输入。

真实验收顺序写在题库的 `live_acceptance_sequence`：设置 customer 范围的 Lemon8 禁令并选择“仅后续生效”，在同一次通话依次回放 **Lemon8 DAU/MAU → TikTok 营收/税额/DAU/MAU → 上海天气 → DAU 定义 → 酒店预订 → 团队效率与工时 → And that much**。需要真实模型、实际工具轨迹、来源正文、最终文本、音频帧和错误事件全部核对；只有 ACK、无来源数字、重复失败卡片都不算通过。必须使用实际 PCM 输入才可宣称语音链路通过。Hinter 补充等多轮场景按各自步骤另行验收，不能由这七问代替。

原 `catalog.json` 的 24 个场景包与 `run_live.py` 保留历史协议，用于兼容已有评分与报告；其成绩不能替代新增自动回答链路的验收。

## 目录结构

```text
evaluation/
  catalog.json                         24 个场景包及可机读题目
  regressions/user-feedback.json        57 条用户反馈回归场景与真实通话验收顺序
  fixtures/company-app-synthetic.json  公司 APP 场景的合成测试记录
  README.md                            评测协议、口径和执行方式
  scripts/
    validate_catalog.py                离线结构/覆盖率检查
    regression_catalog.py              用户反馈集结构与测试引用校验
    run_regressions.py                 确定性回归执行与逐场景报告
    run_live.py                        通过真实 FastAPI + 模型跑集并打自动证据分
    render_report.py                   将 JSON 结果渲染为离线 HTML 图表
  precheck-report/                     题库初检与结构检查结果
  reports/                             版本化 JSON/HTML/Markdown 评测证据；server.log 不提交
```

## 版本化报告索引

`reports/` 是可离线审阅的评测证据，已纳入版本控制。报告来自隔离服务或确定性回归执行；它们记录当时的代码、模型与评分口径，**不等同于当前代码的实时质量承诺**。报告中不包含 API key、模型密钥、原始用户录音、SQLite 数据或后台服务日志。

| 报告 | 内容 | 结果/用途 |
| --- | --- | --- |
| [综合评测与自更新轮次报告.md](reports/综合评测与自更新轮次报告.md) / [HTML](reports/综合评测与自更新轮次报告.html) | round-00 至 round-15 的迭代汇总、趋势和领域短板 | 最近完整全量分 81.0/100；用于人工复盘 |
| `reports/iterations/` | 每轮完整 JSON/HTML 证据，含预检、调试、安全与专项轮次 | 用于追溯分数变化与回滚判断 |
| `dual-channel-eval-20261021T192412Z.*` | 单套件历史实跑与按新口径重评分 | 原始 79.8/100；重评分 72.6/100 |
| `dual-channel-eval-20261022T042025Z.*` | 20 套件历史全量实跑 | 自动分 67.4/100；保留原始失败证据 |
| `user-feedback-*.json` | 自动回答链路的用户反馈回归执行记录 | 从 33/34 修复到最近连续 39/39 通过 |
| `evolution-preview.html` | 早期单套件自进化报告预览 | 离线展示模板与历史结果 |

报告生成目录中的 `server.log` 有本地服务日志，可能包含短期签名 URL 或会话查询参数，明确继续被 Git 忽略。重新生成报告会产生新文件；请先执行密钥扫描，再有选择地加入版本控制，避免把运行时数据、认证信息或用户内容混入证据目录。

## 场景覆盖

| 场景群 | 场景包 |
| --- | --- |
| 差旅 | 航班预订、行程改签与中断、地面交通与差旅报销 |
| 酒店 | 预订与改期、住中服务与账单 |
| 医疗前台 | 来访/病房咨询、预约与就诊准备 |
| 项目会议 | Kick-off、进展同步、需求评审、技术复盘 |
| 学习上课 | AI 课程提问、作业与 Office Hour |
| 通用客服 | 订单物流、退货退款、支付卡、账号安全、网络故障、保险理赔、订阅取消 |
| 在线查询 | 当前天气、短时降雨、官方预警、户外活动风险；需实时联网并附来源和更新时间 |
| 公司内部应用 | 使用明确标注的合成项目台夹具验证授权 MCP 查询、字段追溯和隐私阻断；未连接夹具时不得编造或用公共搜索替代 |
| 公开披露与上市公司财报 | SEC 原始年报中的经营指标、合并/分部口径、审计状态、同比和利润率计算 |

这些域来自公开任务型对话数据的**覆盖设计**，未复制其中对话、标注或个人信息：

- [MultiWOZ](https://arxiv.org/abs/1810.00278) 覆盖酒店、医院、餐饮、出租车、火车等多域任务对话。
- [Schema-Guided Dialogue (SGD)](https://github.com/google-research-datasets/dstc8-schema-guided-dialogue) 覆盖银行、日历、航班、酒店、支付、租车和服务预约等 API 型对话。
- [Taskmaster-1](https://arxiv.org/abs/1909.05358) 提供预约、出行、点单等自然多轮对话的复杂度参考。
- [BANKING77](https://github.com/PolyAI-LDN/task-specific-datasets) 提醒金融客服需要区分细粒度意图与安全边界。

## 运行

默认实跑会临时启动一个隔离 FastAPI 服务（独立 SQLite、音频和双角色 Wiki），仍调用本机配置的真实模型；结束后隔离数据自动删除。因此不会污染正在使用的 Demo 会话和记忆。

```bash
# 先离线确认题库完备性：24 场景 × 5 模式，且都带连续记忆链
backend/.venv/bin/python evaluation/scripts/validate_catalog.py

# 跑一个场景，生成 JSON 分数和离线图表
backend/.venv/bin/python evaluation/scripts/run_live.py \
  --suite travel-flight-booking

# 跑全部 24 个场景（耗时和模型调用数较大）
backend/.venv/bin/python evaluation/scripts/run_live.py --all
```

若确实要评测已运行的共享服务，必须显式确认会创建会话、触发真实回答及记忆整理（当前中心 Wiki 不会被执行器替换，通常仍只适合临时 Demo 数据）：

```bash
backend/.venv/bin/python evaluation/scripts/run_live.py \
  --suite travel-flight-booking \
  --base-url http://127.0.0.1:8818 --shared-service
```

默认结果写到 `evaluation/reports/<timestamp>.json` 和同名 `.html`；可改用 `--report-dir`。脚本不会删除现有会话或 Wiki。

若仅调整了 `catalog.json` 的确定性判分规则，而不需要重新调用模型，可对已捕获的 JSON 证据重算并重新生成图表：

```bash
backend/.venv/bin/python evaluation/scripts/rescore_report.py \
  evaluation/reports/dual-channel-eval-<timestamp>.json
```

## 分数和图表口径

实跑报告给出 `automatic_score / 100`，它只统计可复现的运行证据：

| 维度 | 分值 | 自动证据 |
| --- | ---: | --- |
| 路由与状态 | 30 | 正常题生成完成；阻断题无 Answer/无生成；无失败状态 |
| 拆题完整性 | 25 | 每消息/爆发消息的子问题数与关键意图命中 |
| Hint 采纳 | 20 | 答复命中场景定义的可观察提示要点；Talker 投影不含私有 hint |
| 答复可用性 | 15 | 有非空答复，且命中最低服务要点；不把建议伪装成已完成预订/退款等外部操作 |
| 连续记忆 | 10 | 正向反馈后，下一会话的追问能复用确认信息，且中心 Wiki 出现持久化证据 |

自动分不是“语言质量的真值”。每条题还带有 `manual_rubric`，用于人工或另接 LLM-as-judge 时评估事实正确性、同理心、提问澄清、可执行性和是否真正遵循 Hint。HTML 报告用总分、按模式、按场景群和逐题明细四种图表呈现效果，并明确显示 `blocked UX` 与 `memory persistence` 两项薄弱点。

对明确没有外部预订/查询工具的题，`forbidden_answer_patterns` 还会自动扣除“声称已替用户购票、出票、下单等”或“编造实时航班号”等不可验证操作性表述；其余事实性、同理心和服务措辞仍保留给人工量表复核。

## 历史草稿审核协议的基线

评测集严格按用户目标设定期望，但不把当前实现的限制隐藏在分数里：

- 旧协议的 `direct` 使用空 hint 批准，源于原 [规格](../specs/001-dual-channel-voice-agent/spec.md)；这不是当前自动回答模式的要求。
- `hinter_block` 将问题变为 `rejected`，不生成实质答案，发送固定 `Sorry, I dont know.` 并记录到 Wiki。自动运行器会模拟草稿生成后的 Hinter 确认发布；仍检查公开投影。
- Wiki 整理由同一模型异步完成，失败不会回滚答案。跨会话记忆链正是用来暴露它是否真正写入中心 `context.md` 的，而不是把同会话的历史误当长期记忆。

## 新增题目的规则

旧协议保持一个场景包恰好包含五种 `mode` 和一个 `memory_chain`；新增自动回答回归不受此约束，写入 `regressions/user-feedback.json`，并提供输入步骤、明确期望、测试引用和真实验收要点。问题必须自包含，使用虚构姓名、订单号等个人或业务信息；不得要求模型真的下单、调取真实病历、读取密码或进行资金操作。对高风险题写清楚可给出的安全替代路径。在线天气题应核对实时来源与更新时间；公开披露/财报题使用固定历史基准和官方原始来源，区分期间、单位、报表口径与计算值；公司 APP 正向题仅允许使用 `fixtures/company-app-synthetic.json` 的合成数据和专用测试连接器，未连接时应报告不可访问。运行 `validate_catalog.py` 只检查题库结构及测试引用，不会调用模型或实跑评测。

## 评测驱动的 prompt 自进化

技能入口：[eval-driven-self-evolving](skills/eval-driven-self-evolving/SKILL.md)。让当前 agent 阅读该技能，执行“基线评测 → 分析 → 独立 prompt 修改 → 重启隔离服务 → 复测”的循环。默认最多三个优化轮次，同一检查点连续三轮未解会冻结并在报告中说明。

只允许修改已绑定的独立 prompt，当前为 `backend/app/agent/prompts/memory.md`。核心 prompt 目前嵌在 Python 中，相关问题只报告；不得修改源码、题库、评分器或实际 `context.md` / Wiki 内容。

```bash
backend/.venv/bin/python evaluation/skills/eval-driven-self-evolving/scripts/evolve.py init --all
# 按 SKILL.md 对输出的运行目录执行 evaluate / record / apply / evaluate / finish

# 使用统一模板离线查看已有报告（不会重新调用模型）
backend/.venv/bin/python evaluation/skills/eval-driven-self-evolving/scripts/evolve.py render \
  --report evaluation/reports/dual-channel-eval-20261021T192412Z-rescored.json \
  --output evaluation/reports/evolution-preview.html
```

新运行的 `evaluation/reports/evolution/<run-id>/index.html` 合并评测与自进化记录，固定展示有效分数、轮次趋势、逐题通过状态、原题/期望/完整返回、修改 diff、回退及三轮未解原因。旧报告只有前 1000 字摘录时会明确标注；新的自进化评测采集完整快照。原有评测脚本与历史报告格式保持兼容。


当前答复呈现（response_flow_version=3）：公开 messages/answers 按 Talker 输入返回稳定的 `reply-<message-id>` 汇总答复，`answer_parts` 包含逐项结果。后台传输尝试仍使用各自 response_id；附和、内部重试不作为独立公开气泡。初次等待全部分支结果，纠正则保留完整旧版本至新版完成，再原位替换。新版验收应检查卡片 ID、部分状态和实际音频传输，不把内部 attempt 数量当作公开气泡数量。历史实跑报告属于其当时的协议版本，不代表本次新交互已实跑。
