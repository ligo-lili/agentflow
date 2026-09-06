# Phase 2 证据报告 — T3 Prompt/Context 快照与预算

日期：2026-09-05
环境：Windows 10.0.26200 x64，Python 3.13.13。

## Goal（目标）

让"模型看到了什么"成为可持久化、可比较的一等对象：固定 9 段顺序的
`PromptSnapshot`、每次模型调用前的 `ContextSnapshot`、带组件分解与输出
预留的 token 预算，并接入运行时使上述行为真实发生。

## Implementation（实现）

`packages/context/`（T3 主战场）：

- `estimator.py` — `TokenEstimator` Protocol；`DeterministicEstimator`
  （`deterministic-v1`：`count = 0 if empty else max(1, ceil(len/4))`，按
  Python 字符计数，跨平台确定）；`TiktokenEstimator`（`tiktoken-cl100k_base`，
  包或编码文件不可用时抛类型化 `EstimatorUnavailableError`）；
  `create_estimator("deterministic"|"tiktoken"|"auto")`，auto 回退确定性
  估计器且回退可观测（快照中记录实际名称）。
- `budget.py` — `BudgetConfig`（校验 reserved < max）、
  `TokenBudgetManager.evaluate(component_tokens) -> BudgetReport`：
  `limit = max_context_tokens - reserved_output_tokens`，fits/reason 显式
  （`within_budget total=N limit=M` / `over_budget total=N limit=M overage=K`），
  **组件分解之和恒等于 total**。
- `prompt.py` — `PromptSections`（9 段结构化输入，None/空即省略）、
  `PromptBuilder.build()`：按 `PROMPT_SECTION_ORDER` 过滤排序、逐段计数、
  总和=total、estimator 名记录、可选落 `SnapshotStore`。
- `manager.py` — `ContextManager.build()`：每条消息按 `role: content` 计数、
  按 role 组件分解（和=total）、预算评估、产出 `ContextSnapshot`
  （compaction_state="none"，metadata.step）+ `ContextBuild`（snapshot+report）、
  可选落 store（按 sequence 顺序）。

运行时最小集成（对 T1 文件的加法改动，默认参数关闭时行为与 Phase 1 完全
一致）：

- `loop.py`：新增可选 `prompt_builder`/`context_manager`；接入后每次 run
  发出 `PromptBuilt`（1 次），每次模型调用前发出 `ContextBuilt`
  （payload 含 snapshot_id/total_tokens/fits/reason）。
- `session.py`：透传这两个可选参数。

`examples/context_growth_demo.py` — 3 次工具调用使上下文逐步增长，逐步打印
ContextBuilt 的 token 总量、预算判定与落库快照的组件分解。

## Files Changed（文件变更）

新增：`packages/context/{estimator,budget,prompt,manager}.py`、
`examples/context_growth_demo.py`、`tests/test_estimator_budget.py`、
`tests/test_prompt_context.py`、本报告。
修改：`packages/context/__init__.py`（T3 导出）、
`packages/runtime/loop.py`、`packages/runtime/session.py`（最小加法集成，
见 Deviations）。
未触碰：core、observability、evals、experiments、根配置。

## Verification Commands（验证命令）

```bash
python -m pytest -q
python -m ruff check .
python -m mypy packages
python examples/context_growth_demo.py
```

## Actual Test Output（真实测试输出）

`python -m pytest -q`（T3 新增 17 个；T0/T1/T2 的 52 个全部继续通过）：

```text
.....................................................................    [100%]
69 passed in 0.54s
```

`python -m ruff check .`：

```text
All checks passed!
```

`python -m mypy packages`：

```text
Success: no issues found in 29 source files
```

## Actual Demo Output（真实 Demo 输出）

`python examples/context_growth_demo.py`（两次运行输出一致）：

```text
=== AgentFlow T3 demo: context growth under budget ===
estimator: deterministic-v1  budget: 4096-256=3840 input tokens

-- context built before each model call --
  step=1 total_tokens=  28 fits=True (within_budget total=28 limit=3840)
  step=2 total_tokens=  80 fits=True (within_budget total=80 limit=3840)
  step=3 total_tokens= 207 fits=True (within_budget total=207 limit=3840)
  step=4 total_tokens= 559 fits=True (within_budget total=559 limit=3840)

-- persisted snapshots --
  …-ctx-0000: total=  28 components={'system': 12, 'user': 16}
  …-ctx-0001: total=  80 components={'system': 12, 'user': 16, 'assistant': 3, 'tool': 49}
  …-ctx-0002: total= 207 components={'system': 12, 'user': 16, 'assistant': 6, 'tool': 173}
  …-ctx-0003: total= 559 components={'system': 12, 'user': 16, 'assistant': 9, 'tool': 522}

result: finished, steps=4, answer='Report digested.'
```

## Results（结果）

T3 验收逐项核对：

| 验收项 | 结果 | 证据 |
|---|---|---|
| 固定 9 段顺序与 PromptSnapshot | 通过 | `test_prompt_builder_preserves_the_frozen_section_order`（9 段全序=契约顺序）；空段过滤保序测试 |
| 每次模型调用前 ContextSnapshot | 通过 | `test_wired_session_emits_prompt_and_context_events_in_exact_order`（事件精确序列 SessionStarted→PromptBuilt→(ContextBuilt→LLMCallStarted→LLMCallFinished)…→AgentFinished）+ demo 每步 1 快照 |
| Token 预算：estimator 名、输出预留、组件分解 | 通过 | `test_budget_report_breakdown_sums_to_total`、`test_context_manager_breakdown_sums_to_total…`（分解和==total 不变量） |
| Breakdown sum equals total tokens | 通过 | 上述两测试 + demo 输出逐快照可加性可见 |

（Phase 2 其余验收项——阈值触发、压缩后适配预算、双策略确定性、semantic_state
保留字段——属 T4，见 compaction-report。）

## Known Limitations（已知局限）

- 预算判定只有 within/over 两态；压缩触发阈值与 CompactionEngine 属 T4。
- 循环集成的 PromptSnapshot 以 system/task/recent_messages 三段为输入；
  memory/skills/runtime_context 等段在 T3 尚无运行时数据源，由
  `PromptSections` 直接构造即可使用（单测已覆盖 9 段全量）。
- 组件分解按 role 分组（system/user/assistant/tool）；更细粒度的组件
  （逐段）体现在 PromptSnapshot.token_counts。
- tiktoken 首次加载编码文件需要网络/缓存；本环境已成功加载并缓存。离线
  环境自动回退确定性估计器（名称可观测）。

## Deviations（与任务包的差异）

- 对 `packages/runtime/loop.py` 与 `session.py` 做了最小加法集成（两个可选
  参数 + 两处 emit）。原因：架构契约"ContextManager emits a ContextSnapshot
  before each model call"只能在 Loop 内实现，属 T3 验收不可分割点；变更纯
  增量、默认 None 时与 Phase 1 行为逐字节一致（T1/T2 的 52 个既有测试全部
  通过，另有 `test_unwired_session_keeps_phase_1_event_shape` 显式守护）。
  按跨任务规则在此报备；如需回退可直接删除两处注入点。
- 无其他偏差。
