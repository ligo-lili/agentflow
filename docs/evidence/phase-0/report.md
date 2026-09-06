# Phase 0 证据报告（T0 — Foundation）

日期：2026-09-05
环境：Windows 10.0.26200 x64，Python 3.13.13（miniconda3），未初始化 git 仓库。

## Goal（目标）

建立可安装、强类型、可测试的仓库骨架，并冻结公开契约：
创建 runtime、context、memory、observability、evals、experiments 六个包骨架；
为 events、provider、tools、snapshots、stores 提供 Pydantic 模型与 Protocol 接口；
交付运行 test + lint 的 CI；以及本证据报告。

## Implementation（实现）

- `packages/core/` — 冻结契约：
  - `events.py`：`AgentEventType`（11 种必需生命周期事件）、`AgentEvent`
    （frozen Pydantic 模型：`event_id`、`session_id`、`trace_id`、`sequence`、
    UTC `timestamp`、`event_type`、`payload`、`schema_version="1.0"`）。
  - `provider.py`：`ModelMessage`、`ToolSpec`、`ModelRequest`、`ToolCallRequest`、
    `TokenUsage`、`ModelResponse`、`ModelProvider` Protocol（runtime-checkable）。
  - `tools.py`：`Tool`、`ToolContext`、`ToolResult`（runtime-checkable Protocol）。
  - `snapshots.py`：`PromptSection` + 冻结的 `PROMPT_SECTION_ORDER`（9 个固定段落）、
    `PromptSnapshot`、`ContextSnapshot` —— 全部记录 `estimator` 名称。
  - `stores.py`：`EventStore`、`SnapshotStore`、`SessionStore` Protocol +
    `SessionRecord`。
  - `redaction.py`：递归、大小写不敏感的 payload 秘密字段脱敏。
  - `errors.py` / `ids.py`：类型化错误层次；id 与时间助手。
- `packages/observability/` — `EventBus`（per-session sequence 单调校验、同步
  fan-out、错误向上传播）与内存参考 Store（`InMemoryEventStore` 仅追加并检测
  重复、`InMemorySnapshotStore`、`InMemorySessionStore`）。
- `packages/runtime`、`context`、`memory`、`evals`、`experiments` — 类型化包
  骨架，仅再导出各自契约（实现分别落在 T1/T3/T4/T7）。
- `tests/` — 6 个离线确定性测试模块 + 共享 `helpers.py`（固定 UTC 时间戳）。
  无网络、无 API key、不依赖墙钟时间。
- `.github/workflows/ci.yml` — CI 在 Python 3.11 与 3.13 上运行 install、
  pytest、ruff、mypy。
- `pyproject.toml`、`Makefile`、`.env.example`、`.gitignore`、`AGENTS.md`、
  product/architecture/ADR 文档为既有文件，未做修改。

## Files Changed（文件变更）

新增：

```text
packages/__init__.py
packages/core/{__init__,errors,ids,events,redaction,provider,tools,snapshots,stores}.py
packages/runtime/__init__.py
packages/context/__init__.py
packages/memory/__init__.py
packages/observability/{__init__,eventbus,inmemory}.py
packages/evals/__init__.py
packages/experiments/__init__.py
tests/{__init__,helpers}.py
tests/test_events.py
tests/test_provider_tools.py
tests/test_snapshots.py
tests/test_stores_bus.py
tests/test_redaction.py
.github/workflows/ci.yml
docs/evidence/phase-0/report.md
```

修改：无（既有根配置与文档均未改动）。

## Verification Commands（验证命令）

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
python -m mypy packages
```

环境说明：本机代理拦截 TLS，直接 `pip install` 在 pip 的 truststore SSL 层
抛出 `RecursionError`。安装通过文档化回退命令完成：
`python -m pip install -e ".[dev]" --no-build-isolation --trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org`。
这是环境层面的临时绕过，不是项目变更；在正常网络下验收命令按原文执行。

## Actual Test Output（真实测试输出）

`python -m pip install -e ".[dev]" ...`（回退参数，节选）：

```text
Successfully built agentflow
Successfully installed agentflow-0.1.0 coverage-7.16.0 fastapi-0.141.1 jinja2-3.1.6
mypy-1.20.2 pytest-8.4.2 pytest-cov-5.0.0 ruff-0.16.6 starlette-1.6.0 uvicorn-0.52.4 ...
```

`python -m pytest -q`：

```text
..........................                                               [100%]
26 passed in 0.29s
```

`python -m ruff check .`：

```text
All checks passed!
```

`python -m mypy packages`：

```text
Success: no issues found in 18 source files
```

## Actual Demo Output（真实 Demo 输出）

Phase 0 没有 CLI 示例（`examples/` 由 T1 引入）。以契约冒烟运行演练了
事件 → 脱敏 → EventBus → EventStore → 重载 链路：

```text
events in store: ['SessionStarted', 'AgentFinished']
redacted payload: {'api_key': '[REDACTED]', 'step': 0}
request serializes: {"model":"fake","messages":[{"role":"user","content":"hi","name":null,"tool_call_id":null}],"tools":[],"temperature":0.0,"max_output_tokens":null}
```

## Repository Tree（仓库树，仅源码文件）

```text
E:\AGENTFLOW
|   .env.example  AGENTS.md  Makefile  pyproject.toml  README.md  .gitignore
+---.github\workflows\ci.yml
+---docs
|   +---adr          ADR-001..004
|   +---architecture context-engine.md  evaluation.md  event-model.md  overview.md  runtime.md
|   +---evidence     README.md  phase-0\report.md  (phase-1..4 目录为空)
|   +---product      scope.md  target-user.md  vision.md
|   \---roadmap      phase-0..5  README.md
+---packages
|   |   __init__.py
|   +---core         __init__.py errors.py ids.py events.py redaction.py provider.py tools.py snapshots.py stores.py
|   +---runtime      __init__.py
|   +---context      __init__.py
|   +---memory       __init__.py
|   +---observability __init__.py eventbus.py inmemory.py
|   +---evals        __init__.py
|   \---experiments  __init__.py
\---tests            __init__.py helpers.py test_events.py test_provider_tools.py test_snapshots.py test_stores_bus.py test_redaction.py
```

## Results（结果）

四项 Phase 0 验收检查在本机全部通过：

| 检查 | 结果 |
|---|---|
| `pip install -e ".[dev]"` | 通过（经文档化 `--trusted-host` 回退） |
| `pytest -q` | 26 passed |
| `ruff check .` | all checks passed |
| `mypy packages` | no issues in 18 source files |

## Known Limitations（已知局限）

- 仅契约：尚无 `AgentLoop`/`AgentSession`（T1）、SQLite Store（T2）、
  Prompt/Context 引擎与压缩（T3/T4）、API/Inspector（T6）、评估（T7）。
- `EventBus` 为同步、进程内实现，属 MVP 设计决定。
- 内存 Store 是供测试/Demo 的参考实现；持久化 SQLite Store 在 T2 中以相同
  Protocol 交付。
- CI workflow 已编写但本机无法执行（无 git remote）；实际执行证据为四条
  本地验收命令。
- 本机代理拦截导致 `pip install` 需要 TLS 回退；见 Verification Commands。

## Deviations（与任务包的差异）

- 在交付物清单之外增加了 `packages/core/redaction.py`（payload 秘密脱敏）：
  `docs/architecture/event-model.md` 要求 "secrets must be redacted before
  persistence"，故该契约随事件模型一并交付。
- 增加 `tests/helpers.py` 共享模块，提供确定性时间戳。
