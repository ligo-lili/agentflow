# Phase 6.2 — Background Runs (T10)

日期:2026-09-06
依据:`docs/roadmap/phase-6-production.md` T10。

## 0. Goal

真实模型单次调用 30s+,请求内同步执行必然占死 HTTP 连接。本轮把全部运行
(含离线 scenario)移到**有界后台队列**:202 接受 → 客户端轮询 → 终态;
队列满 429;进程重启后幽灵 running 会话被清扫为 failed。

## 1. Implementation

- **`apps/api/runqueue.py`**:`RunQueue`(`ThreadPoolExecutor`,
  `AGENTFLOW_RUN_WORKERS` 默认 2)——容量即 worker 数,`submit` 在满载时
  返回 False(无隐形排队);worker 内异常被记录日志、绝不上抛;`close()`
  等待在途运行(接在 lifespan 关闭时,先于 stores 关闭)。
- **202 语义**:`POST /api/runs` 现在**构建**会话(离线路径
  `build_offline_session` / task 路径 `build_task_session`,构建失败仍在
  请求内返回 4xx/5xx),**预创建** running 投影行(`SqliteSessionStore`,
  复用 lifespan 持有的锁保护连接),再提交 worker,返回 202
  `{session_id, scenario, status: "running"}`。轮询端点立即可用。
- **竞态修复(实测发现)**:首轮实现后全量测试暴露 `SESSION_NOT_FOUND`
  ——`_require_session` 只查事件日志,而 worker 发出首个事件前会话"不存在"
  且 `GET /api/sessions/{id}` 404。修复:预创建投影行(202 即存在)+
  `_require_session` 以 session store 优先、事件日志兜底。
- **启动清扫** `SqlitePersistence.fail_stale_running_sessions()`:对每个
  running 投影,在同一事务内追加终止 `AgentFailed`
  (`phase="startup_sweep"`,sequence 连续、保持完整性)并把投影翻为
  failed;已有终止事件的运行不重复处理。lifespan 启动时执行(失败仅记
  日志不阻塞服务)。
- **Inspector**:选中 running 会话时每 1.5s 轮询 detail,终态后自动刷新
  全部面板;task 提交状态行显示 "polling…"。
- **契约破坏(计划内,一次性)**:`POST /api/runs` 200→**202**;
  `RunResponse` 的 answer/steps/event_count 在 202 响应中不再携带
  (轮询 detail/replay 获取)。17 个既有 API 契约测试适配为
  "202 + wait_terminal";新增 429/清扫/202 即存 4 项。

## 2. Verification Commands

```bash
python -m pytest -q -p no:cacheprovider
python -m pytest tests/ -q -p no:cacheprovider   # × 3(竞态回归)
python -m ruff check .
python -m mypy packages apps
```

## 3. Results(2026-09-06 实测,Windows 10.0.26200 x64,Python 3.13.13)

```text
220 passed, 2 skipped            # 全套件(2 skip 为既有 tiktoken 环境条件跳过)
216 passed, 2 skipped            # 连续 3 次全量:无顺序/竞态失败
All checks passed!(ruff)
Success: no issues found in 46 source files(mypy --strict,packages + apps)
```

新测试覆盖:

| 场景 | 断言 |
|---|---|
| 202 即存在 | 202 后立刻 `GET /api/sessions/{id}` = 200(running/finished);轮询至 finished |
| 队列满 | 1 worker + 阻塞运行时,第二次提交 429 `RUN_QUEUE_FULL`,`details.workers == 1` |
| 启动清扫 | 旧进程遗留 running 行 → 新实例启动后 failed,追加 `AgentFailed(seq=1)` 单事务 |
| 清扫幂等 | 终态会话经第二次启动清扫后状态不变 |

## 4. Files Changed

- 新增:`apps/api/runqueue.py`、`tests/test_background_runs.py`、本报告。
- 修改:`apps/api/main.py`(构建/提交/202/清扫/_require_session)、
  `apps/api/config.py`(run_workers)、`apps/api/schemas.py`(RunResponse
  status 默认值语义不变,场景标签不变)、
  `packages/observability/sqlite.py`(fail_stale_running_sessions)、
  `apps/web/templates/inspector.html`(轮询)、
  `tests/{test_api,test_integration}.py`(202 适配)、
  `docs/architecture/api.md`。

## 5. Known Limitations

- 队列容量 = worker 数(默认 2):单机自部署的刻意保守值;无持久化队列,
  进程崩溃时在途运行由启动清扫标记 failed(该会话的部分轨迹仍可重放)。
- 202 响应不再携带结果字段,旧客户端需适配(行为已写入 api.md 与
  OpenAPI)。
- Inspector 轮询间隔固定 1.5s(无 SSE/长轮询——非目标,见 roadmap)。

## 6. Follow-up Needed

无(P6.2 完成)。下一阶段:T11 部署化(Docker/鉴权/healthz)。
