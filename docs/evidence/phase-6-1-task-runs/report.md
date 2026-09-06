# Phase 6.1 — Real-Provider Task Runs (T9)

日期:2026-09-06
依据:`docs/roadmap/phase-6-production.md` T9(已批准的 Phase 6 计划,
形态:单机自部署服务;工具接入:服务端配置文件)。

## 0. Goal

让 `POST /api/runs` 接受**自定义任务**,在**服务器配置的真实模型**上执行,
可调用**运营者声明的注册工具**——同时保持:离线 scenario 模式逐字节不变,
全部离线测试无需网络与 key,坏配置在启动时失败而非请求中途。

## 1. Implementation

- **工具参数 schema**:`ToolRuntime.specs()` 读取工具的
  `parameters_schema`(duck-typed,非 mapping → `ToolRuntimeError`);无该
  属性的工具保持空 schema,完全向后兼容。真实模型由此能发出有意义的
  工具调用(此前永远发空参数表)。
- **Provider 工厂** `packages/runtime/provider_factory.py`:
  `create_provider(env)` 按 `AGENTFLOW_PROVIDER` 选择;`openai-compat` →
  `OpenAICompatProvider.from_env`(缺 key 的错误翻译为
  `ProviderConfigError`,消息点名缺失键);unset/`fake`/未知 → 类型化错误
  ——任意任务没有隐式默认 provider,scripted fake 只属于 canned scenario。
- **工具模块加载器** `packages/runtime/tool_loader.py`:
  `AGENTFLOW_TOOLS_MODULE`(点路径或 `.py` 文件)→ 模块级
  `TOOLS: Sequence[Tool]`,启动时严格校验(名字唯一非空、description、
  run 可调用、schema 为 mapping)。加载即执行该模块——**运营者配置的
  受信代码**,设计边界写入部署文档,绝不接受客户端提交的工具代码。
  示例:`examples/agentflow_tools.py`(word_count / text_stats 离线,
  http_get 显式标注为网络工具)。
- **API task 模式**(`apps/api/{main,schemas,config}.py`):
  `RunRequest` 扩展为 `{scenario?, task?, tools?, system_prompt?, max_steps?}`,
  模型校验器强制 scenario ⊕ task(tools/system_prompt/max_steps 仅限 task
  模式),违例 422。task 路径:注入或工厂构建的 provider(未配置 → 409
  `PROVIDER_NOT_CONFIGURED`)、按名字解析注册工具(未知 → 422 并列出
  available)、`AgentLoopConfig` 带 env 超时(默认 60s/30s)、预算 env
  (默认 4000/200)、semantic_state 压缩默认启用。新 `apps/api/config.py`
  启动时一次性读取全部 env(含终于生效的 `AGENTFLOW_DB_PATH`),非法值
  启动即失败。
- **忠实工具调用续传**(真实端到端中发现的落地级缺陷,本轮修复):
  此前转录不保存模型发出的工具调用,续传请求按 R7 线格规则合成
  `arguments="{}"`——真实模型看不到自己上一轮的参数,实测表现为**同一
  工具被反复重调,第 5 次调用返回非 JSON 参数而被类型化拒绝**。修复:
  `ModelMessage` 新增可选 `tool_calls`(`ToolCallRequest` 前移),loop 把
  模型实际请求存入 assistant 消息,适配器线格序列化忠实回放;旧转录
  (无该字段)仍走合成路径。估算契约不变(计数按 `{role}: {content}`)。
- **Inspector**:侧栏新增 Task run 表单(task + 逗号分隔 tools),成功
  选中会话,失败按封套展示原因(409/422)。
- `packages/runtime/__init__.py` 导出新接口;`.env.example` 重写为分组
  的完整变量表。

## 2. Verification Commands

```bash
python -m pytest -q -p no:cacheprovider
python -m ruff check .
python -m mypy packages apps
python -m hatchling build -t wheel && python scripts/check_wheel.py dist
python examples/task_run_api.py   # 手动:真实端点 + .env 凭据
```

## 3. Results(2026-09-06 实测,Windows 10.0.26200 x64,Python 3.13.13)

```text
218 passed(198 + specs/加载器/工厂/API task 模式 新增 20,适配 2)
All checks passed!(ruff)
Success: no issues found in 45 source files(mypy --strict,packages + apps)
wheel ok: dist\agentflow-0.1.0-py3-none-any.whl (44 files, all 16 required modules present)
```

**真实端到端**(`python examples/task_run_api.py`,真实 OpenAI 兼容端点,
端点与 key 不出现于本报告;修复后实测):

```text
provider: OpenAICompatProvider; tools: ['word_count', 'text_stats', 'http_get']
HTTP: 200
scenario: task | status: finished | steps: 2
answer: Here are the stats for the sentence **'AgentFlow runs real tasks now.'**:
- **Characters:** 30
- **Words:** 5
- **Lines:** 1
replay integrity: True
tool: text_stats | ok: True | value: {'characters': 30, 'words': 5, 'lines': 1}
```

事件流(节选):`SessionStarted → PromptBuilt → ContextBuilt →
LLMCallFinished(finish_reason=tool_calls) → ToolCallFinished(ok=True) →
ContextBuilt → LLMCallFinished(finish_reason=stop) → AgentFinished`——
一次工具调用后模型看到自己真实的调用参数,正常收尾。

修复前对照(同任务,合成 `"{}"` 续传实测):同一工具被连续重调 4 次
(模型看不到自己传过什么),第 5 次 `AgentFailed
phase=provider error='OpenAIProviderError: tool call arguments are not
valid JSON'`——失败被类型化记录、replay integrity 仍为 valid,行为符合
契约,但任务不可用;修复后 2 步完成。

## 4. Files Changed

- 新增:`packages/runtime/provider_factory.py`、
  `packages/runtime/tool_loader.py`、`apps/api/config.py`、
  `examples/agentflow_tools.py`、`examples/task_run_api.py`、
  `tests/test_provider_factory.py`、`tests/test_tool_loader.py`、本报告。
- 修改:`packages/core/provider.py`(ModelMessage.tool_calls)、
  `packages/runtime/{tools,loop,openai_provider,__init__}.py`、
  `apps/api/{main,schemas}.py`、`apps/web/templates/inspector.html`、
  `tests/{test_api,test_openai_provider}.py`、`.env.example`、
  `docs/architecture/{api,runtime}.md`、`docs/roadmap/{README,phase-6-production}.md`、
  `docs/evidence/README.md`。

## 5. Known Limitations

- task 模式仍为**请求内同步执行**——真实端点 30s+ 的调用会占住 HTTP 请求
  (P6.2 后台化解决);模型返回非 JSON 工具参数仍被严格拒绝(类型化、
  可重放,属契约而非缺陷)。
- 预算判定用 deterministic-v1 估算(工程代理,校准偏差已有实证报告)。
- 工具模块在 API 进程内执行:信任边界 = 服务器运营者(部署文档明示)。

## 6. Follow-up Needed

无(P6.1 完成)。下一阶段:T10 后台运行。
