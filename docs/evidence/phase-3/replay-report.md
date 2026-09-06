# Phase 3 证据报告 — T5 时间线与重放（Timeline & Replay）

日期：2026-09-05
环境：Windows 10.0.26200 x64，Python 3.13.13。

## Goal（目标）

提供只读查询面（Observatory）：把事件日志变成可读时间线，并仅凭事件日志与
快照 Store 重建整个会话——每步模型看到的上下文、模型响应、工具调用与结果、
压缩前后与保留状态、最终答案——**全程不触碰 Provider 或 Tool，不重执行
任何东西**，回答"Agent 为什么这样行为"。

## Implementation（实现）

- `packages/observability/timeline.py`（新增）：
  - `SessionTimelineBuilder.build(session_id) -> SessionTimeline`：逐事件
    生成 `TimelineEntry`（sequence、event_type、step、一行摘要、payload），
    按类型聚合计数；未知 session 抛类型化 `UnknownSessionError`。
  - 摘要规则按事件类型映射（任务/finish_reason/工具 ok 与值/压缩前后
    token/最终答案），长值以 60 字符 preview 截断，避免时间线被大 payload
    淹没。
- `packages/observability/replay.py`（新增）：
  - `SessionReplayer(event_store, snapshot_store?, session_store?)
    .load(session_id) -> SessionReplay`：纯事件流状态机——
    `SessionStarted` 提供身份与任务；`PromptBuilt`/`ContextBuilt` 以
    `snapshot_id` 关联 SnapshotStore 中的快照对象；`LLMCall*` 以 `step`
    配对；`ToolCallStarted/Finished` 以 `call_id` 配对；压缩事件按
    "先于其所在步的 ContextBuilt"顺序挂接到对应步；终态事件给出
    status/final_answer/error。
  - 序列连续性校验：日志缺口抛 `ReplayError`（防篡改/丢事件静默）。
  - `SessionReplay`/`ReplayStep`/`ToolCallReplay`/`CompactionReplay` 全部
    frozen Pydantic；两次 load 结果逐字段相等（重放是日志的纯函数）。
  - 无 SnapshotStore 时优雅降级：仍从事件重建 steps/响应/工具/压缩，
    快照对象为 None。
- `examples/replay_demo.py`：两阶段 demo——阶段 1 运行会话（含压缩）；
  阶段 1 结束后持久化连接关闭、**Provider/Tool/Session 全部出域**；阶段 2
  以全新 SQLite Store 实例重开数据库，仅凭事件日志与快照打印时间线、
  每步上下文、压缩与最终答案。
- `packages/observability/__init__.py` 追加导出。未修改任何既有模块。

## Files Changed（文件变更）

新增：`packages/observability/timeline.py`、
`packages/observability/replay.py`、`examples/replay_demo.py`、
`tests/test_replay.py`（11 个测试）、本报告。
修改：`packages/observability/__init__.py`（追加导出）。
未触碰：core、runtime、context、sqlite、根配置。

## Verification Commands（验证命令）

```bash
python -m pytest -q
python -m ruff check .
python -m mypy packages
python examples/replay_demo.py
```

## Actual Test Output（真实测试输出）

`python -m pytest -q`（T5 新增 11 个；T0–T4 的 79 个全部回归通过）：

```text
........................................................................ [ 80%]
..................                                                       [100%]
90 passed in 0.50s
```

`python -m ruff check .`：

```text
All checks passed!
```

`python -m mypy packages`：

```text
Success: no issues found in 33 source files
```

## Actual Demo Output（真实 Demo 输出）

`python examples/replay_demo.py`（节选；数值确定）：

```text
[phase 1] ran session 08e028e5… status=finished answer='Report digested.'

[phase 2] replayed 08e028e5… from the event log alone (no provider, no tools in scope)

-- timeline (13 entries) --
  seq=00 SessionStarted: task='Digest the long report.' model=fake-model
  seq=01 PromptBuilt: sections=3 total_tokens=25
  seq=02 ContextBuilt step=1: total_tokens=15 fits=True
  seq=03 LLMCallStarted step=1: model=fake-model messages=2
  seq=04 LLMCallFinished step=1: finish_reason=tool_calls
  seq=05 ToolCallStarted step=1: name=long_tool
  seq=06 ToolCallFinished step=1: name=long_tool ok=True value='xxxxxxxx…'
  seq=07 ContextCompactionStarted: strategy=semantic_state trigger=threshold_exceeded total=405 threshold=360 limit=450 …
  seq=08 ContextCompactionFinished: strategy=semantic_state tokens=405→423 removed=3
  seq=09 ContextBuilt step=2: total_tokens=405 fits=True
  seq=10 LLMCallStarted step=2: model=fake-model messages=2
  seq=11 LLMCallFinished step=2: finish_reason=stop
  seq=12 AgentFinished: answer='Report digested.'

-- replayed steps (what the model saw) --
  step 1: context_tokens=15 fits=True finish_reason=tool_calls
    tool long_tool ok=True value=xxxxxxxxxxxxxxxxxxxxxxxx…
    answer: ''
  step 2: context_tokens=405 fits=True finish_reason=stop
    compaction[semantic_state]: 405→423 tokens, removed=3, fits=True
    answer: 'Report digested.'

status: finished  final answer: 'Report digested.'
task: 'Digest the long report.'  model: fake-model  max_steps: 4

the runtime is gone; everything above came from the event log.
```

## Results（结果）

T5 验收逐项核对（Phase 3 中 T5 相关项）：

| 验收项 | 结果 | 证据 |
|---|---|---|
| 打开会话并检视时间线与快照 | 通过 | `test_timeline_lists_every_event_in_sequence_with_summaries`、`test_replay_matches_the_snapshots_recorded_during_the_run`（重放中的快照与运行期落库快照逐字段相等）；demo 时间线 |
| 不执行 Agent 即重放记录的事件 | 通过 | `test_replay_never_executes_provider_or_tools_across_restart`（SQLite 重开实例、运行时对象出域后重放成功且两次结果相等）；demo 阶段 2 作用域内无 Provider/Tool |
| Replay must not call a provider or tool | 通过 | Replayer 构造只接受 Store；代码路径无 Provider/Tool 依赖（90 个测试全绿且 `provider.calls` 仅来自阶段 1） |

## Known Limitations（已知局限）

- 重放为"状态重建"，不重演事件生成过程（MVP 语义：Replay 不重跑模型与
  工具）；需要"重跑对照"时属 T7 实验范畴。
- 时间线摘要面向人读；富 UI 呈现（步骤详情面板）属 T6 Inspector。
- `ReplayStep.usage` 依赖 Provider 上报（Fake Provider 为脚本值）。
- 序列校验只检测缺口，不检测事件被替换（日志完整性签名不在 MVP 范围）。

## Deviations（与任务包的差异）

- 无功能性偏差。工程选择两处：时间线摘要对长值截断（避免时间线不可读）；
  重放对日志序列缺口抛 `ReplayError`（防静默错配），均为只读查询面的
  可诊断性要求，已测试覆盖。
