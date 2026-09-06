# AgentFlow Phase 6 — 可落地使用(单机自部署服务)

目标形态:开发者在一台服务器/自己的电脑上用 Docker 或 uvicorn 跑起来,配置一个 OpenAI 兼容端点 + 一个工具配置文件,然后通过 API/Inspector 提交**自己的任务**,用**真实模型**执行,后台运行、可检视、可重放。SQLite 保留(单机零运维)。

分三个阶段串行执行,每阶段独立验证 + 证据报告(遵循 AGENTS.md 任务纪律)。现有 198 个离线测试全程保持通过,`fake` provider 路径行为不变(离线第一原则不破坏)。

---

## P6.1 真实模型运行路径 + 工具参数 schema

**问题**:真实模型的工具调用需要参数 schema(`ToolRuntime.specs()` 现在永远发空 schema);API 硬编码 FakeModelProvider 和 canned 场景;预算/超时不可配。

**改动**:
1. `packages/core/tools.py` + `packages/runtime/tools.py`:`ToolRuntime.specs()` 读取工具的 `parameters_schema` 属性(duck-typed `getattr`,无此属性的工具维持空 schema——完全向后兼容);`ToolSpec` 已支持该字段,`OpenAICompatProvider` 已支持序列化,无需改。
2. 新增 `packages/runtime/provider_factory.py`(或放 runtime/__init__):`create_provider(env) -> ModelProvider`,按 `AGENTFLOW_PROVIDER` 选择:`fake`(默认,保持现状)/ `openai-compat`(→ `OpenAICompatProvider.from_env`)。
3. 新增 `packages/runtime/tool_loader.py`:加载 `AGENTFLOW_TOOLS_MODULE`(点路径或文件路径)指向的 Python 模块,读取模块级 `TOOLS: Sequence[Tool]`,校验名字唯一、schema 为 mapping;错误类型化并在启动时失败。提供 `examples/agentflow_tools.py` 示例(2–3 个离线安全工具:`word_count`、`text_stats`、`http_get`)。
4. `apps/api/main.py` + `schemas.py`:`RunRequest` 扩展为 `{scenario?, task?, tools?: list[str], system_prompt?, max_steps?}`;校验 scenario 与 task 二选一(同时给/都不给 → 422)。`task` 路径 = 真实运行:provider 从工厂来、工具按名字从注册表选、`AgentLoopConfig` 超时从 env 默认(60s/30s)、`BudgetConfig` 上限 env `AGENTFLOW_MAX_CONTEXT_TOKENS`(默认 4000,现硬编码 500)。`scenario` 路径(fake provider)行为与今天逐字节一致。
5. lifespan 读 `AGENTFLOW_DB_PATH`(.env.example 里的死配置变真)。

**测试**(全部离线):带 schema 的自定义工具跑 fake provider 断言 specs 传递;工具加载器(合法/缺失/非法模块);provider 工厂;API 422 路径;fake 场景回归不变。

## P6.2 后台运行 + 状态轮询

**问题**:运行同步阻塞在 HTTP 请求内,真实模型单次 30s+ 必然超时。

**改动**:
1. 新增 `apps/api/runqueue.py`:`RunQueue`(ThreadPoolExecutor,max_workers=`AGENTFLOW_RUN_WORKERS` 默认 2);`POST /api/runs` 改为 **202** 立即返回 `{session_id, status:"running"}`,工作线程执行完整 run(沿用每 run 独立 EventBus+SqlitePersistence 的模式,线程内自建自关连接,避免共享连接竞争);队列满 → 429。
2. 启动清扫:进程启动时把上次遗留的 `running` 状态会话标记为 `failed`(类型化事件,防止幽灵 running);`rebuild_projections` 已有基础。
3. Inspector:运行中会话每 ~1.5s 轮询 `GET /api/sessions/{id}`,完成后自动刷新时间线;其余交互不变。
4. 文档:api.md 补 202 语义与轮询协议。

**测试**:202 → 轮询到 finished 全生命周期(用 fake provider 即时完成);队列满 429;清扫逻辑;API 现有 17 个契约测试适配 202(唯一一次破坏性 API 变更,记入 evidence)。

## P6.3 部署化:Docker + Token 鉴权 + 健康检查

**改动**:
1. 鉴权:可选 env `AGENTFLOW_AUTH_TOKEN`(设置后 `/api/*` 要求 `Authorization: Bearer <token>`,常量时间比较,401 走统一错误封套;不设置 = 无鉴权,本地用法不变);`GET /healthz` 永不鉴权。Inspector 有 token 时弹一次输入框存 localStorage 并随请求携带。
2. CORS:`AGENTFLOW_CORS_ORIGINS`(逗号分隔,可选)。
3. `Dockerfile`(python:3.13-slim、非 root、/data 卷存 DB、healthcheck)+ `docker-compose.yml`(env_file、端口、卷)+ `.dockerignore`。
4. README 增加 "Deploy" 一节;新增 `docs/architecture/deployment.md`(环境变量总表、SQLite 单机边界、备份=复制 db 文件)。

**测试**:带/不带 token 的 401/200、healthz、CORS 头——全部离线;Docker 构建在验证阶段手动执行一次并记录输出。

## 收尾(每阶段末尾执行)

- 更新 `docs/roadmap/`(新增 phase-6 文件 + README 任务表 T9–T11)、`docs/architecture/`(api/runtime/deployment);
- 每阶段一份 `docs/evidence/phase-6-*/report.md`(真实命令输出),README 状态节从 "not production-ready" 更新为分项表述(单机服务已就绪的项 vs 仍保留的非目标:Postgres/多用户/流式/token 计费精度);
- 全量 pytest + ruff + mypy(packages+apps)+ wheel 冒烟;P6.3 后用你的 `.env` 端点做一次真实端到端冒烟(提交自定义任务 + 自定义工具,后台运行,Inspector 截图)。

**不做**(记入 roadmap 非目标):Postgres 后端、多用户/RBAC、SSE 流式时间线、API 直接提交工具代码。

**风险**:① API 202 变更破坏 Inspector/测试兼容——计划内一次性适配;② 后台线程 + SQLite 单连接锁——每 run 独立连接规避,max_workers 默认 2;③ 工具配置模块 = 服务器所有者显式信任的代码——文档明确写这是设计边界而非漏洞。