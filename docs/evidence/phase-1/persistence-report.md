# Phase 1 证据报告 — T2 持久化（Persistence）

日期：2026-09-05
环境：Windows 10.0.26200 x64，Python 3.13.13。

## Goal（目标）

提供 SQLite 持久化：Events、Snapshots、Session 三类 Store 实现 T0 Protocol，
会话、事件与 JSON payload 跨进程重启往返，事件顺序按 `sequence` 保持，
缺失 session id 产生类型化 not-found 错误。

## Implementation（实现）

`packages/observability/sqlite.py`（新增，遵循架构图"observability 拥有
SQLite"的归属）：

- `SqliteEventStore` — 事件仅追加；`event_id` 为主键、`(session_id, sequence)`
  唯一；完整事件以 JSON（`data` 列）存储保证字段保真；读取按 `sequence`
  排序；重复 event_id 抛 `StoreError`；`session_ids()` 按 首 sequence 列出
  全部会话 id。
- `SqliteSnapshotStore` — Prompt/Context 快照按 `(snapshot_id, session_id,
  sequence, data JSON)` 存储，读取按 sequence 排序。
- `SqliteSessionStore` — 会话元数据全列存储；同 id 覆盖；
  `get_session` 缺失返回 `None`（与 Protocol 语义一致），
  `require_session`/`require_session_events` 缺失抛类型化
  `SessionNotFoundError`（`AgentFlowError` 子类）。
- `SqlitePersistence` — 纯事件消费者（ADR-002）：订阅 EventBus，
  `SessionStarted` 物化 SessionRecord（status=running），
  `AgentFinished`/`AgentFailed` 更新状态为 finished/failed，每个事件
  append 到事件 Store。不触碰 Agent Loop 内部状态，T1 代码零改动。
- 所有 sqlite3 错误统一包装为 `StoreError`（携带上下文），不静默。
- 初始化：父目录自动创建，建表 `CREATE TABLE IF NOT EXISTS`，
  默认路径 `DEFAULT_DB_PATH = .agentflow/agentflow.db`。
- `examples/persistence_reload.py` — 重启往返 demo：父进程准备全新数据库后
  以 `--write` 参数启动**真实子进程**执行一次确定性会话并退出；父进程随后
  用全新 Store 实例从磁盘重读会话记录与全部事件。
- `packages/observability/__init__.py` 仅追加上述导出。

## Files Changed（文件变更）

新增：`packages/observability/sqlite.py`、`examples/persistence_reload.py`、
`tests/test_sqlite_persistence.py`、本报告。
修改：`packages/observability/__init__.py`（追加导出）。
未触碰：`packages/core/**`、`packages/runtime/**`、内存 Store、根配置。

## Verification Commands（验证命令）

```bash
python -m pytest -q
python -m ruff check .
python -m mypy packages
python examples/persistence_reload.py
```

## Actual Test Output（真实测试输出）

`python -m pytest -q`（T2 新增 10 个：Protocol 符合、路径创建、往返保真、
保序、去重、会话覆盖往返、类型化 not-found、快照往返、投影器 finished/failed）：

```text
....................................................                     [100%]
52 passed in 0.31s
```

`python -m ruff check .`：

```text
All checks passed!
```

`python -m mypy packages`：

```text
Success: no issues found in 25 source files
```

## Actual Demo Output（真实 Demo 输出）

`python examples/persistence_reload.py`（连续两次运行输出一致）：

```text
=== AgentFlow Phase 1 demo: SQLite persistence and reload ===
database: .agentflow\agentflow.db
[child ] wrote 8 events for session 148bdcb2d8a5492a90a94bd2e38247e7
[child ] run status: finished, answer: The report contains 18 words.
[parent] child process exited; reopening the database from scratch
[parent] reloaded session: task='Count the words of the AgentFlow report text.' status=finished
[parent] reloaded 8 events in sequence order:
  seq=00 SessionStarted
  seq=01 LLMCallStarted
  seq=02 LLMCallFinished
  seq=03 ToolCallStarted
  seq=04 ToolCallFinished
  seq=05 LLMCallStarted
  seq=06 LLMCallFinished
  seq=07 AgentFinished
[parent] round-trip complete: the child is gone, the data stayed.
```

## Results（结果）

Phase 1 持久化验收逐项核对：

| 验收项 | 结果 | 证据 |
|---|---|---|
| SQLite 在 `.agentflow/agentflow.db` 初始化 | 通过 | demo 输出 + `test_database_file_is_created_at_the_given_path`（父目录自动创建） |
| Sessions、events、JSON payload 重启后往返 | 通过 | `test_events_roundtrip_after_reopen_with_payload_fidelity`（嵌套 payload 逐字段相等）、`test_session_roundtrip…`、`test_snapshots_roundtrip_after_reopen`；demo 为真实双进程 |
| 事件顺序按 `sequence` 保持 | 通过 | `test_event_order_is_preserved_by_sequence_not_insertion_order`（乱序写入 3,0,2,1 → 读出 0..3） |
| 缺失 session id 产生类型化 not-found 错误 | 通过 | `test_missing_session_raises_typed_not_found_error`（`SessionNotFoundError` 且 `isinstance(..., AgentFlowError)`） |

## Known Limitations（已知局限）

- 快照表已就绪但尚无生产者：Prompt/Context 快照由 T3 产出后即可持久化。
- 无并发写优化（MVP 单进程串行写）；`check_same_thread` 默认值未改动，
  跨线程使用不在 MVP 范围。
- 事件按会话查询已支持；跨会话检索/时间线查询属 T5（Replay/Query API）。
- `require_session_events` 以"会话无事件"判定缺失；SessionRecord 与事件
  数量的一致性由投影器顺序保证。

## Deviations（与任务包的差异）

- 新增 `SqlitePersistence` 投影器与 `session_ids()`：验收只要求 Store 往返，
  但"会话记录入 SQLite"需要生产者；按 ADR-002 以事件消费者方式实现（T1
  零改动），并给 T5 预留会话枚举接口。
- `require_session`/`require_session_events` 为 SQLite Store 的扩展方法而未
  加入 T0 Protocol：T0 Protocol 定义 `get_session -> SessionRecord | None`，
  与"缺失抛错"语义并存需调用方显式选择；改动 core 属跨任务修改，故以扩展
  方法交付并在此记录。接口建议：后续可将 `require_*` 提升为 Protocol 一部分，
  届时内存 Store 同步实现。
- 新增 `examples/persistence_reload.py`：Phase 1 demo 清单仅有
  `simple_agent.py`，但持久化验收需要可执行的往返演示；该 demo 采用双进程
  设计以证明数据确实来自磁盘而非进程内存。
