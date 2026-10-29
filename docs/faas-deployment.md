# FaaS 部署与调用评估

## 结论

当前仓库的完整双通道语音 Demo 不建议直接整体部署到 FaaS。原因是服务包含 WebSocket 实时通话、SSE、SQLite/Wiki 本地状态、STT/TTS 模型准备、音频文件与较长生命周期后台任务，这些能力更适合容器化长驻服务。

更合适的方式是二选一：

1. **FaaS 作为轻量调用入口**：FaaS 接收外部请求，转发到已部署的 FastAPI 服务，由 FastAPI 继续处理会话、实时语音和 Agent 决策。
2. **只抽 Agent 决策核上 FaaS**：仅部署 AI Intake、Hinter Gate、记忆检索、答案生成等“局部替换后的图编排决策核”；实时语音、STT/TTS、WebSocket 和会话并发仍由长驻服务承载。

本次重构已把通话决策核收敛到 `app.agent.decision_graph.AgentDecisionGraph`，因此具备第二种方案的拆分基础。

## 不建议整体上 FaaS 的部分

| 模块 | 当前实现 | FaaS 风险 |
| --- | --- | --- |
| WebSocket 实时通话 | `app.api.call_routes` + `CallService` 长连接 | FaaS 触发模型通常不适合长连接和连续 PCM 流 |
| STT/TTS | `RealtimeSpeech`、本地/外部语音模型、音频文件 | 冷启动、模型下载和临时文件生命周期不可控 |
| SQLite/Wiki | 本地 `DUET_DATA_DIR` 持久化 | FaaS 实例本地盘不应作为权威状态 |
| 后台任务 | `SessionService.tasks` 管理异步任务 | FaaS 请求结束后后台任务可靠性不足 |
| Docker 启动准备 | `prepare-voice.py` 最长可等待语音模型准备 | 冷启动成本过高，不适合函数粒度 |

## 推荐方案 A：FaaS 调用现有 FastAPI 服务

适用场景：外部系统希望通过 FaaS 暴露一个受控入口，但不想重写实时服务。

调用链：

```text
调用方 -> FaaS HTTP Trigger -> FastAPI /api/v1/... -> Agent Decision Graph -> 返回结果
```

建议 FaaS 做三件事：

1. 校验调用方鉴权与参数。
2. 调用 FastAPI 的稳定 HTTP 接口，例如 `/healthz`、`/api/v1/sessions`、`/api/v1/messages`。
3. 记录调用日志、超时和失败原因，不承载会话状态。

伪代码：

```python
import os
import httpx

DUET_BASE_URL = os.environ["DUET_BASE_URL"]
DUET_TIMEOUT_SECONDS = float(os.getenv("DUET_TIMEOUT_SECONDS", "20"))

async def handler(event, context):
    payload = event.get("body") or {}
    async with httpx.AsyncClient(timeout=DUET_TIMEOUT_SECONDS) as client:
        token = await client.post(f"{DUET_BASE_URL}/auth/demo", json={"role": "talker"})
        token.raise_for_status()
        jwt = token.json()["token"]
        session = await client.post(
            f"{DUET_BASE_URL}/api/v1/sessions",
            headers={"Authorization": f"Bearer {jwt}"},
            json={},
        )
        session.raise_for_status()
        sid = session.json()["id"]
        message = await client.post(
            f"{DUET_BASE_URL}/api/v1/sessions/{sid}/messages",
            headers={"Authorization": f"Bearer {jwt}"},
            json={"text": payload["text"]},
        )
        message.raise_for_status()
        return {"statusCode": 200, "body": message.json()}
```

注意：如果调用方需要最终回答而不是只提交消息，FaaS 需要轮询会话快照或订阅 SSE；不建议在 FaaS 内等待长时间流式结果。

## 推荐方案 B：只部署 Agent 决策核

适用场景：只希望通过 FaaS 调用“输入一句话，返回路由/审核/是否查资料/是否需要人工”的能力。

本次代码改造后，决策入口为：

```python
from app.agent.call_reasoner import CallReasoner

plans = await reasoner.plan(text, wiki, constraints, history, transcription=transcription)
```

拆成 FaaS 时建议提供一个窄接口：

```json
{
  "text": "caller latest utterance",
  "wiki": "visible memory context",
  "constraints": "private guidance/prohibition constraints",
  "history": [],
  "transcription": {}
}
```

返回：

```json
{
  "questions": [
    {
      "text": "...",
      "action": "answer|lookup|clarify|refuse|limited_answer|await_human",
      "data_scope": "none|public|internal|unknown",
      "reason": "..."
    }
  ]
}
```

这种方式不依赖 WebSocket、音频模型和本地文件写入，但仍需要外部 LLM 凭据、Prompt 配置和工具配置。若要在 FaaS 中使用，需要把 `MODEL_CONFIG_JSON`、`PRIMARY_MODEL`、`CALL_REASONER_MODEL`、`DUET_TOOLS_CONFIG` 等配置迁移到 FaaS 环境变量或配置中心。

## 上线前门禁

- Python Runtime：需要 Python 3.12，依赖来自 `backend/pyproject.toml`。
- 冷启动：若只部署决策核，禁止在函数内加载本地 STT/TTS/Whisper/ONNX 模型。
- 状态：FaaS 内不写 SQLite/Wiki，本地状态仍由长驻 FastAPI 服务或外部存储负责。
- 超时：建议入口超时 20–30 秒；流式回答不放在单次 FaaS 请求内等待。
- 鉴权：FaaS 调 FastAPI 时不要硬编码 token；通过内部网关身份或短期凭证传递。
- 回滚：FaaS 入口可灰度到旧 FastAPI 直连路径；`CALL_REASONER_DECISION_GRAPH=0` 使用顺序执行器运行相同的决策节点。两种执行器共用 `CallReasoner.route_plans` 和 `scope_plans`，统一保留语音语义判断、Wiki/Hinter 原文证据校验、禁令范围判断与多指标合并规则。

## 建议落地顺序

1. 先合入本分支的局部图编排重构，并在长驻服务中完成回归。
2. 为 `AgentDecisionGraph` 增加一个窄 HTTP endpoint 或独立函数 handler，作为 FaaS PoC。
3. 在 FaaS 上只验证文本决策核，不接入实时音频。
4. 通过影子流量对比旧 `CallReasoner.plan` 与新图编排输出，确认 action、data_scope、await_human 比例和错误率。
5. 再决定是否把公开查询/内部工具调用也下沉到 FaaS。默认先不要下沉工具执行，避免凭据和超时边界扩大。
