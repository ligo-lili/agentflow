# Review Improvements R1–R4 — Correctness Release Candidate

日期:2026-09-06
依据:`docs/review-improvement-plan.md`(P0 项,按其第 4 节推荐顺序执行)
范围:**仅 R1–R4**。R5–R11 未开始,遵循计划第 5 节"一次一个任务"的约束。

## 0. Goal

将评审计划中的四个 P0 发现转化为实现:超时契约对齐(R1)、SQLite 完整性
(R2)、Replay 完整性语义(R3)、稳定的 API DTO 与错误封套(R4)。目标是在
标记"正确性优先的发布候选"之前,让架构契约与实际行为一致。

## 1. Implementation

### R1 — 超时契约(`packages/runtime/timeouts.py` 新增)

- `AgentLoopConfig` 新增 `provider_timeout_seconds` / `tool_timeout_seconds`
  (`None` = 关闭守卫,默认不变)。
- 可注入的 `TimeoutPolicy`(不依赖任何 Provider SDK):受保护调用在 worker
  线程执行,超过截止时间即被放弃;loop 将其转为**终态** `AgentFailed`,
  payload 携带 `phase`、`timeout.kind`、`timeout.target`、
  `timeout.timeout_seconds`。超时工作绝不会被报告为成功。
- 超时的工具调用仍会闭合事件边界(`ToolCallFinished`,`ok=false`),保持
  start/finish 成对;Provider 自身抛出的 `TimeoutError` 不会被误报为策略
  违约(payload 无 `timeout` 键)。
- `SessionStarted` payload 记录两个超时值,配置对 Evidence 可见。

### R2 — SQLite 完整性(`packages/observability/sqlite.py` 重写)

- `schema_metadata` 表 + 单一迁移入口 `apply_migrations`(`SCHEMA_VERSION = 2`),
  全部步骤在一个 immediate 事务内执行;旧库(无元数据表)原地升级,
  v2 增加 `events.event_type` 列并从 JSON 回填。
- `SqlitePersistence`:会话投影与事件行在**同一连接的同一事务**内写入,
  注入的写失败不可能产生"终态会话缺终态事件";新增
  `rebuild_projections()` 确定性重建路径。
- 快照不可变:`INSERT OR REPLACE` 改为 `INSERT`,重复 `snapshot_id` 抛
  `StoreError`(内存存储同步收紧)。
- PRAGMA:`foreign_keys = ON`(已验证)、`journal_mode = WAL`、
  `synchronous = NORMAL`、`busy_timeout = 5000`。并发边界:本地单进程,
  契约写入 `docs/architecture/persistence.md`。

### R3 — Replay 完整性(`packages/observability/replay.py`)

- 新增 `ReplayIntegrity`(`valid` / `errors` / `warnings` / `event_range` /
  `schema_versions`)与带稳定 code 的 `ReplayIssue`;`load()` 在有 error 时
  抛出携带完整报告的 `ReplayError`。
- 校验:连续唯一序列、单一 trace 身份、受支持的 schema 版本
  (`SUPPORTED_SCHEMA_VERSIONS = {"1.0"}`)、SessionStarted 存在、终态事件
  唯一且不冲突、工具/压缩 start-finish 配对(有终态事件时未配对 = error)。
- 区分"仍在运行的合法会话"(连续但无终态 → `valid=true`、
  `status="running"`、`NO_TERMINAL_EVENT` warning)与"被截断/损坏的日志"
  (空洞、重复、混杂 → error)。Replay 保持只读。

### R4 — API 契约(`apps/api/schemas.py` 新增,`apps/api/main.py` 重写)

- 八个端点全部声明 `response_model`(成功模型见
  `docs/architecture/api.md`),OpenAPI 含成功与错误 schema。
- 统一错误封套 `{"error": {"code", "message", "details"}}`,稳定 code:
  `SESSION_NOT_FOUND`(404)、`REPLAY_INVALID`(409,details 携带完整性
  报告)、`VALIDATION_ERROR`(422,只含字段定位,不回显原始输入)、
  `STORE_UNAVAILABLE`(500,日志记录脱敏诊断,客户端只收安全文案)。
- `POST /api/runs` 文档化为同步、离线专用;task 上限 2000 字符,超限 422。
- Inspector 两处 JS 适配新封套(`body.error.message`),其余行为不变。

## 2. Verification Commands

```bash
python -m pytest -q -p no:cacheprovider
python -m ruff check .
python -m mypy packages
python examples/simple_agent.py            # 及其余 4 个示例
```

## 3. Results(2026-09-06 实测)

```text
152 passed, 1 warning in 3.11s        # pytest(新增 34 个;唯一 warning 为
                                      # Starlette/TestClient 依赖弃用提示,属 R8)
All checks passed!                    # ruff
Success: no issues found in 37 source files   # mypy --strict
```

新测试分布:超时 8 · SQLite 完整性 10 · Replay 完整性 11 · API(含封套/
泄露防护/409/422/OpenAPI)17。

五个示例全部通过(`simple_agent` / `context_growth_demo` /
`persistence_reload` / `replay_demo` / `compaction_compare`),行为与改进前
一致(默认配置零行为变化)。

### R1 超时实测输出

```text
status: failed
error: provider call 'fake-model' exceeded its 0.2 second deadline
AgentFailed payload: {'phase': 'provider', 'error': "provider call 'fake-model'
exceeded its 0.2 second deadline", 'steps': 1, 'timeout': {'kind': 'provider',
'target': 'fake-model', 'timeout_seconds': 0.2}}
```

### R2/R3 关键验收

- 旧 schema(无 `schema_metadata`、`events` 无 `event_type` 列)原地升级:
  事件可读、`event_type` 回填、版本号 = 2,重复打开幂等。
- 注入写失败(重复 `event_id`)→ 状态回滚为 `running`,终态事件缺席;
  `rebuild_projections()` 从事件日志恢复正确状态/任务/模型。
- 注入损坏的 replay 数据 → API `409`,`error.code = REPLAY_INVALID`,
  `details.integrity.errors` 含 `SEQUENCE_GAP`;评估端点同样拒绝。
- `StoreError` 注入 → `500`,`error.code = STORE_UNAVAILABLE`,响应文本
  不含路径/原始异常;服务端日志走脱敏。
- 运行中会话(尾部截断)→ `valid=true`、`status="running"` + warning;
  中间事件丢失 → `SEQUENCE_GAP` error。

## 4. Files Changed

- 新增:`packages/runtime/timeouts.py`、`apps/api/schemas.py`、
  `tests/test_timeouts.py`、`tests/test_sqlite_integrity.py`、
  `tests/test_replay_integrity.py`、`docs/architecture/persistence.md`、
  `docs/architecture/api.md`、本报告。
- 修改:`packages/runtime/{loop,session,__init__}.py`、
  `packages/observability/{sqlite,inmemory,replay,__init__}.py`、
  `packages/core/stores.py`、`apps/api/main.py`、
  `apps/web/templates/inspector.html`、
  `tests/test_api.py`、`README.md`、
  `docs/architecture/{runtime,event-model}.md`、`docs/evidence/README.md`、
  `docs/evidence/final-report.md`(仅加指向本报告的说明)。

## 5. Known Limitations

- 标准测试运行仍出现 Starlette/TestClient 的 httpx 弃用警告(依赖版本
  固定属于 R8,未在本任务范围内处理)。
- 超时通过放弃线程实现,被放弃的调用本身无法被中断(同步运行时的固有限
  制,已记录在 runtime 契约中)。
- `mypy --strict` 范围仍为 `packages`;`apps/`(含新 schemas)暂不在
  严格检查内(扩大范围属 R8 的 CI 工作)。
- Windows 本地实测;Linux CI 矩阵由既有 `ci.yml` 承接,Windows 作业属 R8。

## 6. Follow-up Needed

- R5(token 可审计)、R6(评估套件扩展)、R8(CI/覆盖率/依赖钉版,
  含消除上述弃用警告)——按计划第 4 节顺序进行。
- R7(可选真实 Provider 冒烟)与 R9–R11(作品集打磨)随后。
- 全部完成后按计划第 7 节从干净运行输出重新生成 `final-report.md`。
