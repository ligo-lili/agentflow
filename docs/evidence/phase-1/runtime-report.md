# Phase 1 证据报告 — T1 运行时（Runtime）

日期：2026-09-05
环境：Windows 10.0.26200 x64，Python 3.13.13。

## Goal（目标）

实现确定性的单 Agent 运行时：会话创建、有界 Agent Loop、工具执行、脚本化
Fake Provider；全部生命周期事件按单调 sequence 发出；Provider 异常产生带
脱敏诊断的 `AgentFailed`；工具成功与失败均以事件表示。

## Implementation（实现）

- `packages/runtime/provider.py` — `FakeModelProvider`：按脚本顺序消费
  `ModelResponse` 或抛出注入异常；脚本耗尽抛类型化 `ScriptExhaustedError`；
  记录每次收到的 `ModelRequest`（供测试断言 transcript）。完全离线、无需
  API key。
- `packages/runtime/tools.py` — `ToolRuntime`：按名注册/查找工具，重名注册抛
  `ToolRuntimeError`；未知工具返回显式失败的 `ToolResult`；工具异常被转换为
  `ok=False` 结果并经文本脱敏——从不静默吞错，也绝不使 Loop 崩溃。
- `packages/runtime/recorder.py` — `EventRecorder`：运行时唯一的事件发射点，
  负责 sequence 分配（每会话从 0 单调递增）、事件 id 派生（
  `{session_id}-{seq:04d}`）、payload 脱敏、经 `EventBus` 发布。
- `packages/runtime/loop.py` — `AgentLoop`（`docs/architecture/runtime.md` 契约）：
  组装消息 → `LLMCallStarted` → 模型调用 → `LLMCallFinished` → 执行请求的
  工具（`ToolCallStarted`/`ToolCallFinished`）→ 观察结果回填 → 循环；以
  `AgentFinished` 或 `AgentFailed` 结束；`max_steps` 硬性步数上限；Provider
  异常即终止并发出 `AgentFailed(phase="provider")`；步数超限发
  `AgentFailed(phase="step_limit")`。
- `packages/runtime/session.py` — `AgentSession`：生成 session/trace 标识、把
  EventStore 订阅到 EventBus（事件持久化只经事件边界）、发出
  `SessionStarted`、驱动 Loop、暴露 `events()`（从 store 按 sequence 重读）；
  同一会话禁止二次 `run()`（`SessionAlreadyRunError`）。
- `packages/runtime/diagnostics.py` — `redact_diagnostic`：对自由文本诊断做
  模式级脱敏（`api_key=…`、`sk-…` 令牌等），弥补键值脱敏无法处理字符串内嵌
  秘密的空档。
- `examples/simple_agent.py` — Phase 1 demo：离线任务 + word_count 工具 +
  脚本化 Provider；打印事件轨迹与最终答案；事件从 store 重读。
- 消费边界：事件消费者只接触 `EventBus`/`EventStore`（ADR-002）；demo 与
  测试均未读取 Loop 内部状态。

## Files Changed（文件变更）

新增：`packages/runtime/provider.py`、`tools.py`、`recorder.py`、`loop.py`、
`session.py`、`diagnostics.py`、`examples/simple_agent.py`、
`tests/test_fake_provider.py`、`tests/test_agent_runtime.py`、本报告。
修改：`packages/runtime/__init__.py`（导出运行时公开接口）。
未触碰：`packages/core/**`、`packages/observability/**`、根配置与 docs。

## Verification Commands（验证命令）

```bash
python -m pytest -q
python -m ruff check .
python -m mypy packages
python examples/simple_agent.py
```

## Actual Test Output（真实测试输出）

`python -m pytest -q`：

```text
..........................................                               [100%]
42 passed in 0.18s
```

（其中 T1 新增 16 个：test_fake_provider.py 5 个，test_agent_runtime.py 11 个；
T0 的 26 个契约测试继续通过。）

`python -m ruff check .`：

```text
All checks passed!
```

`python -m mypy packages`：

```text
Success: no issues found in 24 source files
```

## Actual Demo Output（真实 Demo 输出）

`python examples/simple_agent.py`（连续运行两次，答案与事件数完全一致）：

```text
=== AgentFlow Phase 1 demo: simple agent ===
session: e57a4edd…  task: Count the words of the report

-- event trace (from the event store, in sequence order) --
  seq=00 SessionStarted
  seq=01 LLMCallStarted
  seq=02 LLMCallFinished (finish_reason=tool_calls)
  seq=03 ToolCallStarted
  seq=04 ToolCallFinished (ok=True value=18)
  seq=05 LLMCallStarted
  seq=06 LLMCallFinished (finish_reason=stop)
  seq=07 AgentFinished (answer='The report contains 18 words.')

-- result --
status: finished
answer: The report contains 18 words.
steps:  2 (LLM calls)
events: 8 persisted
```

## Results（结果）

Phase 1 运行时验收逐项核对：

| 验收项 | 结果 | 证据 |
|---|---|---|
| 会话创建 + 有界 Agent Loop | 通过 | `test_happy_path…`、`test_step_limit…`（max_steps 硬上限） |
| Fake Provider 脚本化工具调用 + 最终答案 | 通过 | `test_fake_provider.py`、demo 输出 |
| 工具成功与失败均以事件表示 | 通过 | `test_tool_exception…`（ok=False 事件）、`test_unknown_tool…` |
| 必需生命周期事件按单调 sequence 出现 | 通过 | happy path 测试断言 8 个事件的类型与 sequence 0..7 |
| Provider 异常 → 带脱敏诊断的 AgentFailed | 通过 | `test_provider_exception…`（结果与全部事件 payload 均无秘密） |

## Known Limitations（已知局限）

- Prompt/Context 组装目前是 Loop 内的最小 transcript（system + user + 观察结果）；
  T3 将以 PromptBuilder/ContextManager 接管并发出 PromptSnapshot/ContextSnapshot。
  这是预留接缝，不是最终形态。
- 事件持久化当前为内存 Store（进程内）；SQLite 持久化与重启重载属于 T2。
- 未实现单次模型调用的显式超时（MVP 默认 Fake Provider 即时返回，无阻塞面）；
  步数上限即本阶段的"有界"语义。引入真实 Provider 适配器时需补充超时。
- `redact_diagnostic` 覆盖 `key=value` 与 `sk-…` 形态；任意形态的秘密字符串
  不在覆盖范围。
- OpenAI 兼容 Provider 为可选项（roadmap 未列为本阶段交付），未实现。

## Deviations（与任务包的差异）

- 新增 `packages/runtime/diagnostics.py`（文本级诊断脱敏）：验收要求
  "Provider exceptions produce AgentFailed with a redacted diagnostic"，而 T0
  的 `redact_payload` 只按 payload 键脱敏，无法处理异常消息字符串内嵌的秘密。
  按跨任务修改规则，未改动 `packages/core/redaction.py`（T0 交付物），而是在
  T1 自有文件内实现。接口建议：后续任务可将 `redact_diagnostic` 并入
  `packages.core.redaction` 作为契约的一部分，届时 runtime 改为再导出。
- Loop 成功路径的 `AgentFinished` 由 Loop 发出（Session 只发
  `SessionStarted`），与架构文档"repeat, then emit AgentFinished or
  AgentFailed"的表述保持一致。
