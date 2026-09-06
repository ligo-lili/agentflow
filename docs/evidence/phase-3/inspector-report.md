# Phase 3 证据报告 — T6 FastAPI API 与 Web Inspector

日期：2026-09-05
环境：Windows 10.0.26200 x64，Python 3.13.13，fastapi 0.141.1 / uvicorn 0.52.4 /
jinja2 3.1.6 / httpx 0.28.1（TestClient 依赖）。

## Goal（目标）

交付 Phase 3 规定的完整 API 面与本地 Web Inspector：页面可创建离线运行，
展示 Session Timeline、Prompt Sections、Context Breakdown 与快照历史、
Tool Calls、Compaction before/after、Replay 与 Evaluation Summary；Replay
不调用 Provider 或 Tool。

## Implementation（实现）

- `apps/api/main.py`（新增）：
  - `create_app(db_path?) -> FastAPI`（工厂，默认
    `.agentflow/agentflow.db`）；lifespan 打开/关闭三个 SQLite Store；
    模块级 `app` 供 `uvicorn apps.api.main:app` 使用（`make web`）。
  - 端点全表：`POST /api/runs`（`scenario=simple|compaction`，Fake Provider
    离线确定性运行，经标准运行时接线持久化）、`GET /api/sessions`、
    `GET /api/sessions/{id}`、`/events`（T5 Timeline）、
    `/prompt-snapshots`、`/context-snapshots`、`/replay`（T5 SessionReplayer，
    纯读）、`/evaluation`、`GET /`（Inspector 页，Jinja 渲染）。
  - 错误映射：未知 session → 404（`detail` 含诊断）；`ReplayError` → 409；
    `StoreError` → 500；非法 scenario → 422。
  - `/evaluation` 在 T7 落地前返回 **501** 与明确诊断（评分引擎属 T7），
    不伪造结果。
- `apps/web/templates/inspector.html`（新增）：Jinja 外壳 + 内联 CSS +
  原生 JS（零外部依赖、离线可用）。左侧创建运行（两个场景按钮）与会话
  列表；右侧八个面板逐项渲染 API 数据：时间线表格、Prompt Sections
  （逐段 token 占比条 + 内容折叠）、Context Breakdown 与快照历史（预算
  fits 标记、压缩状态标签、组件分解）、Tool Calls、Compaction before/after
  （含 preserved_state JSON）、Replay 步骤表、Evaluation Summary（501 →
  黄色提示条）。JS 中不使用 Jinja 定界符，避免模板冲突。
- `packages/observability/sqlite.py`（跨任务最小修复，见 Deviations）：
  连接改为 `check_same_thread=False` 并为每个 Store 实例加 `threading.Lock`
  串行化全部访问——FastAPI 在线程池中运行同步端点，T2 的线程亲和连接在
  API 场景下必然触发 `sqlite3.ProgrammingError`。
- `tests/test_api.py`（新增 12 个）：TestClient 全端点覆盖。

## Files Changed（文件变更）

新增：`apps/__init__.py`、`apps/api/{__init__,main}.py`、
`apps/web/templates/inspector.html`、`tests/test_api.py`、本报告。
修改：`packages/observability/sqlite.py`（线程安全修复）。
未触碰：core、runtime、context、timeline/replay、根配置与 Makefile
（`make web` 沿用既有定义）。

## Verification Commands（验证命令）

```bash
python -m pytest -q
python -m ruff check .
python -m mypy packages
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8765   # make web
python .agentflow/tmp_api_smoke.py                                 # 冒烟脚本（已运行后删除）
```

## Actual Test Output（真实测试输出）

`python -m pytest -q`（T6 新增 12 个；T0–T5 的 90 个全部回归通过）：

```text
........................................................................ [ 70%]
..............................                                           [100%]
102 passed, 1 warning in 1.32s
```

（唯一 warning 来自 starlette TestClient 对 httpx 版本的弃用提示，属依赖
库告警，不影响结果。）

`python -m ruff check .`：

```text
All checks passed!
```

`python -m mypy packages`：

```text
Success: no issues found in 33 source files
```

## Actual Demo Output（真实 Demo 输出）

真实 uvicorn 服务（后台启动于 127.0.0.1:8765）+ urllib 逐端点冒烟：

```text
GET /api/sessions            -> 200
GET /                        -> 200 (13643 bytes of HTML)
POST /api/runs (compaction)  -> 200 {'scenario': 'compaction', 'status': 'finished', 'steps': 2, 'event_count': 13}
GET /api/sessions/{id}       -> 200 {'task': 'Digest the long report.', 'status': 'finished', 'event_count': 13}
GET .../events               -> 200 (13 timeline entries)
GET .../prompt-snapshots     -> 200 (1 snapshot)
GET .../context-snapshots    -> 200 (2 snapshots)
GET .../replay               -> 200 {'status': 'finished', 'steps': 2, 'compactions': 1, 'answer': 'Report digested.'}
GET .../evaluation           -> 501 {'detail': 'evaluation is implemented in task T7 …'}
GET /api/sessions/unknown/.. -> 404 {'detail': "session 'does-not-exist' not found"}
GET /api/sessions after 2nd  -> 200 (3 sessions listed)
```

（列表为 3 是因为默认数据库 `.agentflow/agentflow.db` 中还留有此前 T2 demo
的持久会话——SQLite 持久化跨进程生效的直接证据。）

## Results（结果）

T6（Phase 3 API 与 Inspector 部分）验收逐项核对：

| 验收项 | 结果 | 证据 |
|---|---|---|
| 8 个 API 端点按规范实现 | 通过 | `test_post_run…`、`test_sessions_list…`、`test_events_endpoint…`、`test_prompt/context_snapshots…`、`test_replay_endpoint…`、`test_evaluation_endpoint…` + 冒烟输出 |
| 页面可创建离线运行 | 通过 | `POST /api/runs` 两种场景 200 且落库（冒烟 + TestClient）；页面按钮调用同一端点 |
| 页面展示 Timeline / Prompt Sections / Context Breakdown / 快照历史 / Tool Calls / Compaction before-after / Replay / Evaluation Summary | 通过 | 模板含全部八个面板（`test_inspector_page_renders_all_required_panels` 逐串断言）；数据渲染逻辑对接真实 API 形状（test_api 断言字段） |
| Replay must not call a provider or tool | 通过 | `/replay` 仅调用 T5 `SessionReplayer`（只读 Store）；`test_replay_endpoint_rebuilds_without_execution` 两次 GET 结果逐字节相等 |
| 未知 session → 类型化 404 | 通过 | 6 个读端点统一 404（`test_unknown_session_maps_to_404_on_every_read_endpoint`）+ 冒烟输出 |

## Known Limitations（已知局限）

- `/evaluation` 返回 501：评分引擎属 T7，落地后该端点直接接入。
- 离线运行场景固定为两种脚本（simple / compaction）；自定义任务与工具的
  注册 API 不在 MVP 范围。
- Inspector 为单页原生 JS，无路由/刷新保持（MVP 级别）；无鉴权
  （scope.md 明确 out of scope）。
- SQLite 连接 `check_same_thread=False` + 进程内锁：单进程内线程安全；
  多进程并发写不在 MVP 范围。

## Deviations（与任务包的差异）

- 修改 `packages/observability/sqlite.py`（T2 交付物）：API 层线程池暴露了
  T2 连接的线程亲和限制。按跨任务规则报备——原因：T6 的 HTTP 端点必须能
  在 FastAPI 的 worker 线程访问 Store；接口冲突：无（存储行为不变，仅线程
  语义）；最小变更：`check_same_thread=False` + 每实例 Lock；T2 全部 10 个
  持久化测试回归通过。替代方案（每请求新建连接）会破坏"Bus→投影器"的
  持久化接线，未采用。
- `POST /api/runs` 的请求体（`scenario`）为 API 层定义：Phase 3 只要求
  "可创建离线运行"，两种确定性场景（基础/压缩）使 Inspector 全部面板
  （尤其 Compaction before/after）有真实数据可展示。
