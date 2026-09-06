# Phase 2 证据报告 — T4 压缩策略（Compaction）

日期：2026-09-05
环境：Windows 10.0.26200 x64，Python 3.13.13。

## Goal（目标）

按 ADR-004 交付两种确定性压缩策略（`keep_recent_summary` 与
`semantic_state`）：预算阈值触发且理由显式；压缩后适配预算；移除消息 id、
保留状态、触发原因全程记录；压缩结果被后续模型调用真正使用。

## Implementation（实现）

- `packages/context/compaction.py`（新增）：
  - `CompactionConfig`（strategy / threshold_ratio=0.8 / keep_last_messages）、
    `CompactionDecision`、`CompactionResult`（strategy、trigger_reason、
    threshold、limit、before/after_tokens、removed_ids、kept_ids、压缩后
    messages、summary_message、preserved_state、fits_budget）。
  - `CompactionEngine.should_compact(report)`：`threshold =
    int(limit × ratio)`；仅当 `total > threshold` 触发，理由含全部数字
    （`threshold_exceeded total=… threshold=… limit=… strategy=…`）。
  - `keep_recent_summary`：保留全部非生成 system 消息 + 最近 N 条非系统
    消息，其余替换为固定模板摘要（角色计数、最早任务、最近工具结果 80 字符
    preview，`preview()` 为文档化截断规则）。
  - `semantic_state`：全部非系统消息替换为结构化状态消息，六个字段按文档化
    规则提取并保留：goal（首条 user）、current_state（最后一条非空
    assistant）、decisions（更早的非空 assistant）、artifacts（成功工具结果
    的去重值，首见序）、tool_findings（`name=artifact#k` 引用）、
    pending_tasks（首条之后的 user 消息）。
  - 取代规则：上一轮压缩生成的 system 消息（`compaction_summary` /
    `semantic_state`）被新一轮压缩移除，由新摘要取代，不累积。
  - 消息 id 为压缩时刻的位置 id（`msg-0000`…），在单个结果内标识被移除
    消息；全程零随机性。
- `packages/context/fixture.py`（新增）：canonical fixture（11 条消息、
  228 tokens，含工具调用与上下文增长），两策略与 T7 评估共用同一输入。
- `packages/context/manager.py`：`ContextManager` 接入可选
  `compaction_engine`；触发时以压缩后消息构建快照，
  `compaction_state=strategy`，`metadata["compaction"]` 记录策略与前后
  token。
- `packages/runtime/loop.py`（最小加法，延续 T3 已报备的注入点）：压缩触发
  时按序发出 `ContextCompactionStarted` → `ContextCompactionFinished` →
  `ContextBuilt`，并**采纳压缩后 transcript** 用于本次模型调用——快照记录
  的消息与请求携带的消息逐条一致。
- `examples/compaction_compare.py`：同一 fixture 两策略对比，全确定输出。

## Files Changed（文件变更）

新增：`packages/context/compaction.py`、`packages/context/fixture.py`、
`examples/compaction_compare.py`、`tests/test_compaction.py`（11 个测试）、
本报告。
修改：`packages/context/manager.py`（可选 engine 接线）、
`packages/context/__init__.py`（导出）、`packages/runtime/loop.py`
（压缩事件发射 + 消息采纳，约 25 行）。
未触碰：core、observability、evals、experiments、根配置。

## Verification Commands（验证命令）

```bash
python -m pytest -q
python -m ruff check .
python -m mypy packages
python examples/compaction_compare.py
python examples/context_growth_demo.py
```

## Actual Test Output（真实测试输出）

`python -m pytest -q`（T4 新增 10 个；T0–T3 的 69 个全部回归通过）：

```text
........................................................................ [ 91%]
.......                                                                  [100%]
79 passed in 0.42s
```

`python -m ruff check .`：

```text
All checks passed!
```

`python -m mypy packages`：

```text
Success: no issues found in 31 source files
```

## Actual Demo Output（真实 Demo 输出）

`python examples/compaction_compare.py`（输出完全确定）：

```text
=== AgentFlow T4 demo: compaction strategy comparison ===
fixture: 11 messages, 228 tokens (estimator deterministic-v1, budget 250-50=200)

=== strategy: keep_recent_summary ===
decision:  should_compact=True (threshold_exceeded total=228 threshold=160 limit=200 strategy=keep_recent_summary)
tokens:    before=228 after=139 threshold=160 limit=200
removed:   7 messages ['msg-0001', ..., 'msg-0007']
kept:      ['msg-0000', 'msg-0008', 'msg-0009', 'msg-0010']
summary:   [Context summary] 7 earlier messages removed (assistant=3, tool=2, user=2); …
fits:      True

=== strategy: semantic_state ===
decision:  should_compact=True (threshold_exceeded total=228 threshold=160 limit=200 strategy=semantic_state)
tokens:    before=228 after=187 threshold=160 limit=200
removed:   10 messages ['msg-0001', …, 'msg-0010']
kept:      ['msg-0000']
summary:   [Conversation state] (+6 more lines)
fits:      True
  goal: Summarize the Q3 report and list risks.
  current_state: Drafting the mitigation plan.
  decisions: I will pull the report first.; Revenue is 1200 with churn at 3.2%.; …
  artifacts: report lines: revenue=1200 churn=3.2 …
  tool_findings: fetch_report=artifact#0; scan_risks=artifact#1; fetch_report=artifact#0
  pending_tasks: Now scan for delivery risks.; Draft the mitigation plan next.
```

集成测试进一步验证（pytest 内）：活动会话中 600+ token 工具结果越阈后，
事件序列为 `…ToolCallFinished → ContextCompactionStarted →
ContextCompactionFinished → ContextBuilt → LLMCallStarted…`，且快照
`messages == 下一次模型请求的 messages`（压缩结果被真正采纳）。

## Results（结果）

T4（Phase 2 压缩部分）验收逐项核对：

| 验收项 | 结果 | 证据 |
|---|---|---|
| 阈值触发带显式理由 | 通过 | `test_threshold_decision_within_and_exceeded`（两种 reason 含 total/threshold/limit 数字）；demo 输出 |
| 压缩后适配配置预算 | 通过 | `test_keep_recent_summary_keeps_system_plus_tail…`（fits=True）、`test_semantic_state_replaces…`（fits=True）；demo 两策略 fits=True；诚实反例 `test_nothing_to_remove…`（无法收缩时 fits=False） |
| 两策略在 canonical fixture 上确定性 | 通过 | `test_both_strategies_are_deterministic_on_the_canonical_fixture`（双跑逐字段相等）；demo 两次运行输出一致 |
| goal/current_state/decisions/artifacts/tool_findings/pending_tasks 保留为字段 | 通过 | `test_semantic_state_preserves_all_six_fields`（六字段逐一断言提取规则） |

## Known Limitations（已知局限）

- 压缩的最小化目标优先级低于"适配预算"：当上下文由单个巨大工具结果主导
  时（集成测试的 1500 字符场景），首次压缩可能不缩小（420 vs 402），只保证
  fits 判定诚实——这是记录在案的权衡，不为数字好看而虚报。
- 消息 id 为压缩时刻的位置 id，不跨压缩稳定；跨压缩的持久消息 id 需要运行时
  transcript 跟踪，留待 replay（T5）需要时引入。
- 确定性摘要/提取是规则式模板，不调用模型；语义质量以可解释、可复现优先。
- 触发只看 token 阈值；基于消息数或角色的触发不在 MVP 范围。

## Deviations（与任务包的差异）

- `packages/runtime/loop.py` 在 T3 已报备的集成点上加约 25 行（两个压缩
  事件 + 采纳压缩后消息 + `_observe_context` 重命名）：压缩若不被后续调用
  使用则毫无意义，属 T4 验收的隐含必要行为；默认路径（无 context_manager）
  不变，79 个测试全部回归通过。
- 摘要工具结果采用 80 字符 preview、semantic_state 采用 artifact 引用去重：
  初版模板会因完整工具值抵消压缩收益甚至放大上下文，按"压缩必须有效"修正，
  规则全部文档化并有精确断言。
