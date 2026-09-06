# AgentFlow MVP — 组合证据报告（Final Portfolio Report）

日期：2026-09-05
环境：Windows 10.0.26200 x64，Python 3.13.13。
状态：**T0–T8 全部完成**，四-week MVP 的全部已规划交付物落地并通过验收。

## 1. 产品主张与回答方式

AgentFlow 的核心问题是：**"Agent 为什么这样行为？"** MVP 用四条能力回答：

1. **检视**：每次模型调用前的 Prompt/Context 快照（9 段固定顺序、逐段/逐角色
   token 分解、estimator 元数据）。
2. **重放**：仅凭事件日志与快照重建整个会话——每步模型看到什么、答了什么、
   调了什么工具、压缩前后——全程不触碰 Provider/Tool。
3. **测量**：七项规则式指标 + 文档化综合分（0.35/0.20/0.20/0.15/0.10）。
4. **对比**：两种压缩策略在完全相同夹具上的确定性 A/B 实验。

## 2. 逐任务交付与证据索引

| 任务 | 交付物 | 证据报告 | 验收结果 |
|---|---|---|---|
| T0 | 类型化契约（events/provider/tools/snapshots/stores）+ 包骨架 + CI | `docs/evidence/phase-0/report.md` | 4/4 |
| T1 | 单 Agent 运行时（AgentSession/AgentLoop/ToolRuntime/FakeModelProvider） | `docs/evidence/phase-1/runtime-report.md` | 5/5 |
| T2 | SQLite 持久化（events/snapshots/sessions）+ 重启往返 | `docs/evidence/phase-1/persistence-report.md` | 4/4 |
| T3 | Prompt/Context 快照 + token 预算（含运行时集成） | `docs/evidence/phase-2/prompt-context-report.md` | 4/4 |
| T4 | 两种确定性压缩策略 + 阈值触发 | `docs/evidence/phase-2/compaction-report.md` | 4/4 |
| T5 | 时间线 + 无重执行重放 | `docs/evidence/phase-3/replay-report.md` | 3/3 |
| T6 | FastAPI 8 端点 + Web Inspector（八面板） | `docs/evidence/phase-3/inspector-report.md` | 5/5 |
| T7 | 规则式评估 + canonical 基准 A/B 对比 | `docs/evidence/phase-4/report.md` | 5/5 |
| T8 | README 对齐 + 全链路集成测试 + 本报告 | 本文档 | 见下 |

每份报告包含真实命令输出（测试/Demo），未以"实现成功"代替证据。

## 3. Verification Commands 与全量结果（最终采集）

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
python -m mypy packages
```

最终实测（本报告撰写时全新运行）：

```text
118 passed, 1 warning in 1.36s        # pytest（唯一 warning 为依赖库弃用提示）
All checks passed!                    # ruff
Success: no issues found in 36 source files   # mypy --strict（packages）
```

测试分布：契约 26 · 运行时 16 · 持久化 10 · 上下文 17 · 压缩 10 ·
重放 11 · API 12 · 评估 12 · 集成 4 = **118**，全部离线、确定性、可重复。

CI（`.github/workflows/ci.yml`）在 Python 3.11/3.13 矩阵上运行同一命令组；
本机为无 git 仓库的沙箱，CI 以配置交付、以本地命令为执行证据。

### CI 修复记录（2026-09-06，首次推送后）

首次推送触发 CI 后，`3.11/ubuntu-latest` 作业失败并取消另一矩阵作业。在干净
venv 中复现出**两个只在干净环境暴露的问题**（本地因恰好装有 httpx/tiktoken
而未暴露）：

1. `starlette.testclient` 强依赖 `httpx`，但 dev 依赖未声明 → pytest 收集
   `test_api.py`/`test_integration.py` 即失败。修复：`pyproject.toml` 的
   `[project.optional-dependencies].dev` 增加 `httpx>=0.27,<2`。
2. mypy strict 找不到可选依赖 `tiktoken`（本地已装）→ `mypy packages` 报
   `import-not-found`。修复：`[[tool.mypy.overrides]] module="tiktoken"
   ignore_missing_imports=true`（tiktoken 为可选 extra，estimator 设计上
   优雅回退）。

另将 CI actions 升级至 node24 版本（checkout@v5 / setup-python@v6）并为
pip 缓存声明 `cache-dependency-path: pyproject.toml`，消除 Node 20 弃用告警。
修复后以干净 venv 完整模拟 CI 四步：`pip install -e ".[dev]"` → pytest
（117 passed + 1 skipped，tiktoken 缺席时按设计跳过）→ ruff → mypy，全部通过。

## 4. Demo 输出（五个，全部离线确定）

### 4.1 `python examples/simple_agent.py`（T1 运行时）

```text
-- event trace (from the event store, in sequence order) --
  seq=00 SessionStarted … seq=04 ToolCallFinished (ok=True value=18)
  … seq=07 AgentFinished (answer='The report contains 18 words.')
status: finished / answer: The report contains 18 words. / steps: 2 / events: 8 persisted
```

### 4.2 `python examples/context_growth_demo.py`（T3 预算）

```text
step=1 total_tokens=  28 fits=True → step=2 80 → step=3 207 → step=4 559（全部 within_budget）
快照组件分解逐快照可见（system/user/assistant/tool）
```

### 4.3 `python examples/persistence_reload.py`（T2 持久化）

```text
[child ] wrote 8 events …（子进程退出）
[parent] child process exited; reopening the database from scratch
[parent] reloaded session: task='…' status=finished
[parent] reloaded 8 events in sequence order: seq=00..07
[parent] round-trip complete: the child is gone, the data stayed.
```

### 4.4 `python examples/replay_demo.py`（T5 重放）

```text
[phase 1] ran session … status=finished answer='Report digested.'
[phase 2] replayed … from the event log alone (no provider, no tools in scope)
-- timeline (13 entries) -- … -- replayed steps (what the model saw) --
  step 2: context_tokens=405 fits=True finish_reason=stop
    compaction[semantic_state]: 405→423 tokens, removed=3, fits=True
status: finished  final answer: 'Report digested.'
```

### 4.5 `python examples/compaction_compare.py`（T4+T7 A/B）

```text
    strategy        task   tool_rate   steps    peak   final  compact  ctx_eff  step_eff  comp_eff   SCORE
  keep_recen           1       1.00       5     445     445        2    0.011      1.00     0.229   0.7251
  semantic_s           1       1.00       5     423     417        2    0.060      1.00     0.244   0.7364
winner: semantic_state (score delta +0.0113)
```

## 5. Success Criteria 对照（README 承诺）

| 成功标准 | 达成方式 |
|---|---|
| 解释 Agent 为什么这样行为 | 事件时间线 + 每步 ContextSnapshot（模型看到的）+ 压缩触发理由 + 工具结果，全部可从 SQLite 重放（4.3/4.4） |
| 检视 prompt/context 生命周期 | PromptSnapshot（9 段固定顺序+逐段计数）与每步 ContextSnapshot（组件分解、fits、压缩状态），Inspector 面板与 API 均可查 |
| 不重跑工具/模型即可重放 | `SessionReplayer` 只读事件+快照；`test_replay_never_executes_provider_or_tools_across_restart` 与 demo 4.4（阶段 2 无 Provider/Tool 在作用域内） |
| 证明两种上下文策略可测量地不同 | 4.5 的 A/B 输出：同夹具下 semantic_state 以 Δ=+0.0113 胜出（峰值更低、恢复率更高），双跑逐字段相等 |

## 6. 架构与边界（实现现状）

- **事件是集成边界**（ADR-002）：运行时唯一写面是 `EventBus` → 订阅者
  （内存/SQLite Store、投影器）；UI/Replay/评估只读事件与快照。
- **快照是一等对象**（ADR-003）：`PromptBuilder`/`ContextManager` 产出不可变
  快照，压缩后快照记录模型实际看到的压缩后消息（before 留在 metadata）。
- **框架无关**（ADR-001）：核心零依赖 LangChain/LangGraph；Provider/Tool 经
  Protocol 注入，默认 FakeModelProvider。
- **规则式评估**：无 LLM Judge；全部归一化为命名函数并测试零分母行为。

## 7. 已知局限（MVP 边界的诚实清单）

1. Provider 适配：默认 Fake；OpenAI 兼容适配器为可选依赖（roadmap 未列入
   四周范围），未实现。
2. 单次模型调用超时未实现（Fake 即时返回）；步数上限即有界语义。
3. 消息 id 为压缩时刻的位置 id，不跨压缩稳定（持久消息 id 待 replay 需求）。
4. 评估的 task_completed 默认规则不判语义正确性；估计器为确定性字符计数。
5. Inspector 为 MVP 单页（无路由保持）；无鉴权（scope 明确排除）。
6. SQLite 线程安全限于单进程；无并发写优化。
7. CI workflow 已配置但本机无 git 仓库未实际执行；本地四命令为执行证据。
8. `phase-5-optional.md`（多 Agent、RAG 等）明确未实现。

## 8. 过程中的跨任务协调记录（全部有测试回归保护）

- T3/T4 对 `packages/runtime/loop.py`、`session.py` 的最小加法集成
  （可选 prompt_builder/context_manager 注入 + 3 个生命周期事件）。
- T1 新增文本级诊断脱敏 `redact_diagnostic`（T0 的键值脱敏不含字符串内嵌秘密）。
- T6 修复 T2 SQLite 连接线程亲和（`check_same_thread=False` + 每实例锁）。
- T7 修复 T4 快照 token 口径（压缩后快照记录模型实际看到的 token）。
每处均报备于对应 evidence 的 Deviations 小节，且全部既有测试保持通过。

## 9. 复现指南

```bash
python -m pip install -e ".[dev]"
python -m pytest -q                    # 118 passed
python -m ruff check . && python -m mypy packages
make demo                              # 或逐个运行 examples/ 下五个 demo
make web                               # http://127.0.0.1:8000 打开 Inspector
```

> 本机代理拦截 TLS 时安装需 `--trusted-host pypi.org --trusted-host files.pythonhosted.org`（详见 phase-0 报告）。

## 10. 结论

四周 MVP 的九个任务全部交付并验收：仓库可安装（`pip install -e`）、强类型
（mypy strict 全绿）、可测试（118 个离线测试）、可演示（5 个确定性 demo）、
有证据（9 份报告、全部含真实输出）。产品主张的四条能力——检视、重放、测量、
对比——均已实现并以本报告第 4/5 节的输出为证。
