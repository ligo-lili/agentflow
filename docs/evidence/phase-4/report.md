# Phase 4 证据报告 — T7 评估与 A/B 实验（Evaluation & Experiment）

日期：2026-09-05
环境：Windows 10.0.26200 x64，Python 3.13.13。无网络、无 LLM Judge、无 API key。

## Goal（目标）

按 `docs/architecture/evaluation.md` 实现规则式轨迹评估：七项指标、命名
归一化函数、文档化综合分；在确定性 canonical 基准上对两种压缩策略做 A/B
对比并给出胜者；把真实评分接入 `/api/sessions/{id}/evaluation`。

## Fixture（基准夹具）

`packages/experiments/compare.py` 的 canonical 基准——两个策略收到**完全
相同**的输入：

- 任务：`"Digest the long report."`；系统提示 `"You are AgentFlow."`。
- Provider 脚本（`FakeModelProvider`，逐字节固定）：工具调用 → 工具调用 →
  最终答案 `"Benchmark complete."`（3 次 LLM 调用）。
- 工具：`long_tool` 返回固定 1500 字符观察值（驱动上下文增长）。
- 预算：`max_context_tokens=500, reserved_output_tokens=50`（limit 450，
  阈值 0.8×450=360）；`keep_last_messages=1`；估计器 `deterministic-v1`；
  `max_steps=4`。
- 每次运行包含工具调用、上下文增长与 2 次压缩（覆盖评估契约全部要求）。
- 唯一变量：`strategy`（`keep_recent_summary` / `semantic_state`）。

## Metric Definitions（指标定义）

| 指标 | 定义 |
|---|---|
| `task_completed` | 0/1；命名默认规则 `default_task_completed`：会话 finished 且最终答案非空 |
| `tool_call_count` | 全部 `ToolCallStarted` 事件数（来自重放步的 tool_calls） |
| `successful_tool_calls` | `ok is True` 的工具调用数 |
| `tool_success_rate` | `tool_success_rate(total, successful)`：successful/total；**零工具调用 → 1.0（不被惩罚）** |
| `execution_steps` | LLM 调用数 + 工具调用数 |
| `peak_context_tokens` | 快照 `total_tokens` 最大值（压缩后快照记录的是模型实际看到的压缩后上下文） |
| `final_context_tokens` | 最后一个快照的 `total_tokens` |
| `compaction_count` | 已完成的压缩次数 |

## Normalization（归一化——全部为命名函数并有测试）

| 函数 | 规则 |
|---|---|
| `clamp01(v)` | 钳制到 [0,1] |
| `tool_success_rate(total, ok)` | ok/total；total≤0 → **1.0** |
| `step_efficiency(steps, baseline)` | clamp01(baseline/steps)；steps≤0 → 1.0；基准 `CANONICAL_BASELINE_STEPS=5`（3 LLM + 2 工具，正确运行不可能更少） |
| `context_efficiency(peak, limit)` | clamp01(1 − peak/limit)；peak=0 或 limit 未知 → 1.0 |
| `compaction_recovery(before, after)` | clamp01((before−after)/before)；before≤0 → 0；**压缩反而变大记 0，不虚报** |
| `compaction_efficiency(recoveries)` | 均值；**零压缩 → 1.0（中性，不因未需要压缩而受罚）** |
| `composite_score(...)` | **0.35×task + 0.20×tool + 0.20×context + 0.15×step + 0.10×compaction**；权重常量 `SCORE_WEIGHTS`，测试断言其和为 1 |

评估器 `Evaluator.evaluate(SessionReplay)` 只消费重放（事件+快照读回），
不触碰运行时。预算 limit 从快照 `budget_limit` 解析（回退配置/4096）。

## Implementation（实现）

- `packages/evals/metrics.py`、`packages/evals/evaluator.py`（新增）：
  上述命名函数、`EvaluationConfig`（baseline/budget）、`EvaluationReport`
  （frozen，15 字段）、`Evaluator`、`default_task_completed`。
- `packages/experiments/compare.py`（新增）：`build_benchmark_session`
  （唯一变量=策略）、`run_benchmark(strategy)`（内存 Store 内跑完整会话 →
  SessionReplayer 重放 → 评估）、`compare_strategies()` →
  `ComparisonReport(entries, winner, score_delta)`。
- `apps/api/main.py`：`/api/sessions/{id}/evaluation` 由 T6 的 501 占位
  替换为真实 `EvaluationReport`（仍是纯只读）。
- `apps/web/templates/inspector.html`：Evaluation Summary 面板渲染分数 +
  全指标表。
- `examples/compaction_compare.py`：在 T4 token 级对比后追加 A/B 基准评估
  表（两策略 × 全部指标 × SCORE）与 winner/delta（Phase 4 验收指定）。

## Files Changed（文件变更）

新增：`packages/evals/{metrics,evaluator}.py`、
`packages/experiments/compare.py`、`tests/test_evaluation.py`（12 个）、
本报告。
修改：`packages/evals/__init__.py`、`packages/experiments/__init__.py`
（导出）、`apps/api/main.py`（evaluation 端点实化）、
`apps/web/templates/inspector.html`（评估面板）、
`tests/test_api.py`（evaluation 测试 501→200）、
`examples/compaction_compare.py`（追加评估段）、
`packages/context/manager.py`（一致性修复，见 Deviations）。

## Verification Commands（验证命令）

```bash
python -m pytest -q
python -m ruff check .
python -m mypy packages
python examples/compaction_compare.py
```

## Actual Test Output（真实测试输出）

`python -m pytest -q`（T7 新增 12 个 + API 测试更新；T0–T6 的 102 个回归通过）：

```text
........................................................................ [ 62%]
...........................................                              [100%]
115 passed, 1 warning in 1.56s
```

`python -m ruff check .` → `All checks passed!`
`python -m mypy packages` → `Success: no issues found in 36 source files`

## Actual Demo Output（真实 Demo 输出）

`python examples/compaction_compare.py` 的 A/B 段（连续两次运行输出一致）：

```text
=== A/B benchmark: evaluation over full sessions (same fixture) ===
    strategy        task   tool_rate       steps        peak       final     compact     ctx_eff    step_eff    comp_eff       SCORE
  keep_recen           1        1.00           5         445         445           2       0.011        1.00       0.229      0.7251
  semantic_s           1        1.00           5         423         417           2       0.060        1.00       0.244      0.7364

winner: semantic_state (score delta +0.0113)
```

## Results（结果）

| 验收项 | 结果 | 证据 |
|---|---|---|
| demo 打印两策略、全部必需指标、分数与胜者 | 通过 | 上方 demo 输出（11 列指标表 + winner + delta） |
| 命名归一化与零分母行为 | 通过 | `test_tool_success_rate_handles_zero_denominator`、`test_step_efficiency…`、`test_context_efficiency…`、`test_compaction_recovery_and_efficiency` |
| 基准含工具调用/增长/≥1 压缩，两策略同 fixture | 通过 | `test_benchmark_fixture_includes_growth_and_compaction_for_both_strategies`、`test_benchmark_session_uses_only_strategy_as_variable` |
| 结果可复现 | 通过 | `test_benchmark_is_deterministic_per_strategy`（双跑逐字段相等）、`test_compare_strategies_ranks_by_score_with_delta`；demo 两次运行一致 |
| 无网络、无 LLM Judge | 通过 | 全链路 Fake Provider + 规则评分；测试离线 |

## Interpretation（结果解读）

- **胜者：`semantic_state`（0.7364 vs 0.7251，Δ=+0.0113）**。两策略完成
  任务与工具表现完全一致（同一脚本），差距全部来自上下文侧：
  `semantic_state` 的峰值更低（423 vs 445）且压缩恢复率更高（0.244 vs
  0.229）——它把重复的 1500 字符工具观察去重为 artifact 引用，而
  `keep_recent_summary` 保留最近消息（含完整工具结果）并把旧内容压缩为
  固定摘要。
- `context_efficiency` 两者都很低（0.01–0.06）：夹具故意让上下文超过预算
  以强制压缩，因此峰值贴着 limit；这是夹具特性而非策略缺陷。
- 分数差异小（约 1.1 个百分点）符合预期：其余 60% 权重（task/step/tool）
  在同 fixture 下被设计为恒等，A/B 信号集中在上下文与压缩效率。

## Known Biases and Limitations（已知偏差与局限）

- **估计器偏差**：`deterministic-v1` 按 4 字符/token 计数，与真实分词差异
  可能改变绝对 token 数（相对比较在同估计器内仍成立）。
- **反应式压缩**：压缩在越阈后触发，无法降低触发它的那次构建的压缩前
  峰值；快照记录的是压缩后模型实际看到的内容（before 记入 metadata）。
- **同脚本 A/B**：task/tool/step 指标被设计为恒等，比较信号集中在
  context/compaction 效率；对"跨任务能力"无推断力。
- `task_completed` 的默认规则是状态+答案非空，不判断答案语义正确性
  （规则式评估的边界，无 LLM Judge）。
- 单一 fixture：结论不外推到其他任务形态。

## Reproduction（复现命令）

```bash
python -m pip install -e ".[dev]"
python -m pytest tests/test_evaluation.py -q        # 评估与 A/B 全部断言
python examples/compaction_compare.py               # 本报告的 demo 输出
# API 侧：POST /api/runs 后 GET /api/sessions/{id}/evaluation 返回同一评分
```

## Deviations（与任务包的差异）

- 修改 `packages/context/manager.py`（T4 交付物）：发现压缩触发时快照
  `total_tokens` 记录压缩前总数、而快照 `messages` 为压缩后内容的不一致
  （模型实际看到的是后者，评估必须基于后者）。最小修复：压缩后按有效消息
  重算快照的 component_tokens/total_tokens，压缩前判定保留在
  `metadata["compaction"].before_tokens` 与 `report`。T4 的 10 个压缩测试
  与 T5 的 11 个重放测试全部回归通过。按跨任务规则在此报备。
- `examples/compaction_compare.py`（T4 demo）追加 A/B 段：Phase 4 验收
  明确指定该命令须输出指标/分数/胜者。
- `tests/test_api.py` 的 evaluation 测试由"断言 501"改为"断言 200 + 评分"：
  T7 使 T6 的占位端点成为真实实现。
