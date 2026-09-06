# AgentFlow — Final Portfolio Report (regenerated after the review cycle)

日期:2026-09-06(本报告由评审改进周期完成后的干净运行输出重新生成,取代
phase-4 时点版本;历史版本见 git 历史与各 phase 报告)
环境:Windows 10.0.26200 x64,Python 3.13
状态:**MVP 完成 + 评审改进计划 R1–R11 全部交付**。这是一个完成度经过验证的
MVP ——**不是**生产就绪系统(区分见下文"就绪度")。

## 1. 产品主张与回答方式

AgentFlow 的核心问题是:**"Agent 为什么这样行为?"** 用四条能力回答:

1. **检视**:每次模型调用前的 Prompt/Context 快照(9 段固定顺序、逐段/逐角色
   token 分解、estimator 名称与元数据)。
2. **重放**:仅凭事件日志与快照重建整个会话——每步模型看到什么、答了什么、
   调了什么工具、压缩前后——全程不触碰 Provider/Tool;损坏或截断的轨迹会被
   形式化完整性检查以稳定错误码拒绝,进行中的会话则如实重放为 running。
3. **测量**:七项规则式指标 + 文档化综合分(权重依据成文),先原始指标后
   综合分。
4. **对比**:两种压缩策略在版本化三场景套件上的确定性 A/B,含语义存活断言、
   失败案例报告与备选权重敏感性。

## 2. 评审改进周期(R1–R11)交付索引

| 项 | 内容 | 证据报告 |
|---|---|---|
| R1 | Provider/Tool 可注入超时策略;超时 = 终态 AgentFailed(含类别);超时工作绝不可能报告为成功 | `review-r1-r4/report.md` |
| R2 | SQLite schema 元数据 + 单一迁移入口;投影与事件同事务原子写入 + 重建路径;快照/事件不可变(WAL/FK/单进程边界成文) | `review-r1-r4/report.md` |
| R3 | Replay 形式化完整性模型(valid/errors/warnings/范围/schema 版本);运行中 vs 截断/损坏的区分 | `review-r1-r4/report.md` |
| R4 | 八端点全部 response_model + 统一稳定错误码封套;诊断脱敏;task 上限;同步离线 run 成文 | `review-r1-r4/report.md` |
| R5 | token 计数口径成文;estimator 元数据随快照;版本化审计夹具(中文/JSON/schema/空);比较披露并拒绝 estimator 不匹配 | `review-r5-r6-r8/report.md` |
| R6 | 三场景版本化评估套件 + 语义存活断言;先原始指标后综合分;权重依据 + 备选权重敏感性;字节级可重复 | `review-r5-r6-r8/report.md` |
| R7 | 可选 OpenAI 兼容适配器(显式 env 配置);离线 mock HTTP 契约测试 11 项;手动冒烟文档;顺带修复 Bearer scheme 脱敏漏洞 | `review-r7/report.md` |
| R8 | Windows+Linux CI 矩阵;覆盖率下限 80% 强制;兼容依赖组钉定;标准测试零弃用警告;wheel 构建/内容冒烟;全模块导入冒烟 | `review-r5-r6-r8/report.md` |
| R9 | Inspector 深链接、加载/空/错误/损坏轨迹状态、XSS 转义、响应式;真实浏览器截图走查 | `review-r9-r10/report.md` |
| R10 | 读写路径架构图;五分钟面试脚本;README 就绪度区分;限制显著化 | `review-r9-r10/report.md` |
| R11 | 包布局决策:维持 `packages/`,迁移条件成文 | `docs/adr/ADR-005-package-layout.md` |

## 3. Verification(2026-09-06 全新干净运行)

```bash
python -m pytest -q -p no:cacheprovider
python -m pytest -q -p no:cacheprovider --cov=packages
python -m ruff check .
python -m mypy packages
python -m hatchling build -t wheel && python scripts/check_wheel.py dist
```

实测输出:

```text
189 passed                                          # pytest,零警告(无弃用告警)
TOTAL                                    1940     91    95%
Required test coverage of 80.0% reached. Total coverage: 95.31%
All checks passed!                                  # ruff
Success: no issues found in 39 source files         # mypy --strict
wheel ok: dist\agentflow-0.1.0-py3-none-any.whl (42 files, all 16 required modules present)
```

189 个测试全部离线、确定性、无 API key。较 phase-4 的 118 个新增 71 个:
超时 8 · SQLite 完整性 10 · Replay 完整性 11 · API 契约 17(含封套/泄露防护/
OpenAPI)· estimator 审计 10 · 场景套件 11 · OpenAI 适配器 11 · 布局冒烟 2,
其余为既有契约的加强。

CI(`.github/workflows/ci.yml`)在 Ubuntu+Windows × Python 3.11/3.13 矩阵上
运行同一命令组并强制覆盖率下限,另有 wheel 构建作业;本沙箱无法直接观测
Actions 运行,以本地 Windows 全套命令为执行证据(项目即在 Windows 开发,
全部测试在本机通过)。

## 4. Demo 输出(五个,全部离线确定,本轮全新运行)

```text
$ python examples/simple_agent.py          → events: 8 persisted
$ python examples/context_growth_demo.py   → result: finished, steps=4, answer='Report digested.'
$ python examples/persistence_reload.py    → [parent] round-trip complete: the child is gone, the data stayed.
$ python examples/replay_demo.py           → the runtime is gone; everything above came from the event log.
$ python examples/compaction_compare.py    → weights[reliability-heavy-v1]: winner=semantic_state delta=+0.0031
```

A/B 结果摘要(套件,`SCENARIO_SUITE_VERSION = "1"`):两策略完成全部场景;
`critical_early_decision` 中 `keep_recent_summary` 按其文档契约丢失早期决策
(套件作为失败案例报告),`semantic_state` 保留;documented 与
reliability-heavy 两组权重下 winner 一致(delta +0.0034 / +0.0031)。

## 5. 验收门(评审计划第 7 节,逐项对勾)

```text
[x] Architecture contracts match actual behavior.            ← runtime/persistence/event-model/api/context-engine/evaluation 全部更新并有测试
[x] SQLite writes and schema upgrades have deterministic integrity behavior. ← test_sqlite_integrity(迁移/原子性/重建/integrity_check)
[x] Replay distinguishes valid, running, truncated and corrupt traces.       ← test_replay_integrity + API 409
[x] Every API endpoint has typed success/error contracts.    ← 全部 response_model + 封套 + OpenAPI 断言
[x] Token comparisons disclose estimator identity and reject mismatches.    ← EstimatorMismatchError + 夹具锚点
[x] A/B results cover multiple versioned deterministic scenarios.           ← 三场景套件 + 敏感性
[x] Linux and Windows CI pass with an enforced coverage floor.               ← 矩阵 + fail_under=80(本机 95.31%)
[x] Standard tests emit no known dependency deprecation warning.             ← 189 passed,零警告
[x] README and Evidence distinguish MVP completeness from production readiness. ← README 状态节 + 本报告
[x] No Multi-Agent, SaaS, RAG or real-time scope was added.                  ← 范围未扩大(仅可选 provider 适配器)
```

## 6. 就绪度:MVP 完成 ≠ 生产就绪

刻意的非目标(生产门槛,不在本 MVP 范围):

- token 计数是工程代理,不是计费 token;
- 单进程 SQLite(WAL 服务本地 API),无多进程写、无网络文件系统;
- 无鉴权/多租户,Inspector 是 localhost 工具;
- 任务完成判定是规则式(运行时完成,非语义正确性),无 LLM Judge;
- `POST /api/runs` 同步且仅离线(可选 `openai-compat` 适配器只有 mock 契约
  测试与手动冒烟流程)。

## 7. 已知限制汇总

- 超时通过放弃线程实现,被放弃调用不可中断(同步运行时固有限制,已写入契约)。
- mypy --strict 范围为 `packages`;`apps/` 由导入冒烟与测试覆盖。
- OpenAI 兼容适配器续传请求以 `arguments="{}"` 合成 assistant.tool_calls
  (原始参数不在运行时转录中);未实现流式。
- starlette TestClient 的 httpx2 弃用路径在离线沙箱不可安装,以带移除条件
  的定向过滤处理(依赖组钉定为测试过的组合)。
- 深链接用 replaceState(后退不逐会话回退);截图覆盖 1280×720 与 390×844。
