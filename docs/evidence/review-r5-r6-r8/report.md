# Review Improvements R5+R6+R8 — Token Auditability, Scenario Suite, Release Verification

日期:2026-09-06
依据:`docs/review-improvement-plan.md`(P1 项,按其第 4 节推荐顺序:R1–R4 稳定后执行 R5/R6/R8)
范围:**仅 R5、R6、R8**。R7(可选真实 Provider)与 R9–R11 未开始。

## 0. Goal

R5:让 token 度量完全可审计(序列化口径成文、tiktoken 元数据、多语言夹具、跨比较的 estimator 一致性)。
R6:把 A/B 评估从单一 canonical 夹具扩展为三场景版本化套件,含语义存活断言、原始指标优先的报告与权重敏感性对比。
R8:发布验证质量——Windows CI、80% 覆盖率下限、兼容依赖组(消除已知弃用警告)、构建/导入冒烟。

## 1. Implementation

### R5 — token 度量可审计

- **计数口径成文**(`docs/architecture/context-engine.md` + `estimator.py` 模块文档):
  消息按 `"{role}: {content}"` 逐条计数;工具观察以 `{"ok","value","error"}`
  JSON 文本计入;工具 spec(发给 provider 的名称/描述)不计入;结构化状态
  (压缩摘要/state 消息)按其渲染文本计入。
- **tiktoken 元数据**:`TiktokenEstimator(encoding, model)` 记录 encoding 与
  model 出处(`metadata` 属性;显式给 model 时名称含 model 以免混淆);
  `DeterministicEstimator.metadata` 记录公式与计数单位
  (`counting_unit=python_characters`)。`ContextManager` 将其复制进快照
  `metadata["estimator_metadata"]`(duck-typed 可选扩展,Protocol 不变)。
- **版本化审计夹具**(`packages/context/fixture.py` 的 `AUDIT_TEXTS`,
  `AUDIT_FIXTURE_VERSION = 1`):空串 / ASCII / 中文 / JSON / 工具 schema。
  测试断言公式恒等(`len` 按字符,跨 Python 版本/平台构造上相同)与字面
  值锚点(0/18/7/19/39),任何公式漂移在任一受支持版本上都会失败。
- **比较拒绝不一致**:`ComparisonReport.estimator` 披露度量身份;
  `compare_strategy_reports` 对混合 estimator 的比较抛
  `EstimatorMismatchError`(计数是工程代理,不是计费 token);
  `run_benchmark` 支持注入 estimator。

### R6 — 三场景版本化评估套件(`packages/experiments/suite.py` 新增)

- `SCENARIO_SUITE_VERSION = "1"`,三个确定性场景(两种策略跑**完全相同**的
  夹具,仅策略不同):
  1. `irrelevant_large_output` — 巨大无关工具输出;断言压缩触发、goal 存活、
     无关内容不泄入答案、最终答案不变。两种策略按契约都逐字保留该内容,
     **零 token 恢复被诚实记录**(`compaction_efficiency = 0`),不掩盖。
  2. `critical_early_decision` — 早期关键决策必须在压缩后存活;
     `semantic_state` 经结构化状态保留(通过);`keep_recent_summary` 按其
     文档契约(仅保留最早任务 + 最新工具结果)**诚实失败**,作为 failure
     case 报告而非隐藏——这就是场景差异化的 A/B 信号。
  3. `repeated_failed_tool` — 连续三次工具失败;断言失败被如实记录、
     失败结果不进入 artifacts、会话仍完成。
- **报告顺序**:每场景原始指标(`EvaluationReport` 的原始字段)在前,
  综合分最后;聚合(`StrategyAggregate`)先列五个原始指标的均值再列综合分。
- **权重依据**(`docs/architecture/evaluation.md`):逐权重说明存在理由;
  敏感性对比使用另一组文档化权重 `reliability-heavy-v1`
  (0.40/0.25/0.20/0.10/0.05,和为 1)对同一聚合重打分,报告两组 winner
  与 delta。`composite_score` 支持显式权重覆盖(键必须完整、和必须为 1)。
- 重复运行**字节级稳定**(固定 session id、共享确定性 estimator);
  无 LLM Judge、无网络。

### R8 — 发布验证质量

- **CI**(`.github/workflows/ci.yml`):矩阵加入 `windows-latest`
  (Python 3.11/3.13 × Ubuntu/Windows 四组合);测试步骤带
  `--cov=packages --cov-report=term-missing`;新增 `build` 作业:构建 wheel
  并运行 `scripts/check_wheel.py` 验证 16 个必需模块在制品中。
- **覆盖率下限**:pyproject `[tool.coverage.report] fail_under = 80`
  (实测 96.06%),`make coverage` 本地同口径。
- **依赖兼容组**:pyproject 明确测试过的组合
  (fastapi 0.141.1 + starlette 1.6.0 + httpx 0.28.1 + pydantic 2.13.2,
  范围钉定使其解析到该组合)。
- **已知弃用警告**:starlette 1.6 的 TestClient 要求 `httpx2`;本离线验证
  环境无法安装该包(无网络),故 pyproject 以定向 `filterwarnings` 忽略
  该唯一已知警告,并注明移除条件(httpx2 可安装时)。标准测试运行现无
  任何弃用警告。
- **布局冒烟**:`tests/test_package_layout.py` 遍历导入 `packages` 与
  `apps` 的全部模块(>25 个),防止布局/导入回归。

## 2. Verification Commands

```bash
python -m pytest -q -p no:cacheprovider
python -m pytest -q -p no:cacheprovider --cov=packages
python -m ruff check .
python -m mypy packages
python -m hatchling build -t wheel && python scripts/check_wheel.py dist
python examples/compaction_compare.py   # 及其余 4 个示例
```

## 3. Results(2026-09-06 实测)

```text
176 passed                                     # pytest,无任何警告(弃用警告已消除)
TOTAL                                          # coverage: 1804 stmts, 71 miss
Required test coverage of 80.0% reached. Total coverage: 96.06%
All checks passed!                             # ruff
Success: no issues found in 38 source files    # mypy --strict
wheel ok: dist\agentflow-0.1.0-py3-none-any.whl (41 files, all 16 required modules present)
```

新增测试:R5 审计 9 · R6 套件 11 · 权重覆盖校验 1 · 布局冒烟 2(其余为
既有断言更新)。合计 176(上一单元 152)。

### R5 审计夹具锚点(deterministic-v1 实测)

```text
empty=0  ascii=18  chinese=7  json=19  tool_schema=39   # chars: 0/70/28/76/156
```

### R6 套件实测(示例输出摘录)

```text
critical_early_decision / keep_recent_summary: score=0.7340
    [decision_survives_compaction=FAIL final_answer_intact=ok]
critical_early_decision / semantic_state:      score=0.7416
    [decision_survives_compaction=ok final_answer_intact=ok]
aggregate keep_recent_summary: raw(task=1.00 tool=0.667 ctx=0.309 step=1.000 comp=0.410) → composite 0.7361
aggregate semantic_state:      raw(task=1.00 tool=0.667 ctx=0.323 step=1.000 comp=0.416) → composite 0.7395
FAILURE: critical_early_decision/keep_recent_summary: decision_survives_compaction
weights[documented-v1]:       winner=semantic_state delta=+0.0034
weights[reliability-heavy-v1]: winner=semantic_state delta=+0.0031
```

字面值说明:套件两次运行 JSON 完全一致(`byte-stable: True`);结论对两组
权重均成立(delta +0.0034 / +0.0031),证明单权重结论未掩盖权衡。

## 4. Files Changed

- 新增:`packages/experiments/suite.py`、`scripts/check_wheel.py`、
  `tests/test_estimator_audit.py`、`tests/test_scenario_suite.py`、
  `tests/test_package_layout.py`、本报告。
- 修改:`packages/context/{estimator,manager,fixture,__init__}.py`、
  `packages/evals/{metrics,__init__}.py`、
  `packages/experiments/{compare,__init__}.py`、`pyproject.toml`、`Makefile`、
  `.github/workflows/ci.yml`、`examples/compaction_compare.py`、
  `tests/test_evaluation.py`、`tests/test_prompt_context.py`
  (metadata 契约更新)、`docs/architecture/{context-engine,evaluation}.md`、
  `docs/evidence/README.md`。

## 5. Known Limitations

- `httpx2` 无法在离线验证环境安装;对 starlette 的 TestClient 弃用警告
  采用了带移除条件的定向过滤,而非依赖升级。
- `irrelevant_large_output` 场景下两种策略都逐字保留大输出(近期尾部/
  artifacts 契约),压缩恢复率为 0 ——这是夹具形状的诚实结果,已在
  evaluation.md 与套件文档中说明。
- CI 的 Windows/构建作业以配置交付,本地以 Windows 实测(全套命令)与
  wheel 构建/检查为执行证据;与 R1–R4 报告相同,沙箱内无法直接观测
  GitHub Actions 运行。
- `run_suite` 的聚合综合分是"均值后加权",不是"逐场景得分均值"(规则已
  文档化)。

## 6. Follow-up Needed

- R7(可选 OpenAI 兼容适配器冒烟路径)——仅在时间充裕时,不得削弱离线
  可复现性。
- R9–R10(Inspector 打磨与文档叙事)、R11(包布局决策:维持现状)。
- 全部完成后按计划第 7 节验收门清点,并从干净运行重新生成
  `final-report.md`。
