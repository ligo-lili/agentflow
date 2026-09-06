# Portfolio Polish Round — 求职作品集呈现与可信度增强

日期:2026-09-06
依据:作品集评审(用户批准的改进清单 1–6 项)。

## 0. Goal

工程内容(MVP + R1–R11 + 真实冒烟)已完备;本轮针对招聘评审者的
"前 30 秒观感"与若干可信度细节:

1. LICENSE 缺失;
2. README 无视觉证据(Inspector 截图已有但未展示)、无徽章;
3. interview-script.md 数字陈旧,且缺真实端点冒烟谈资;
4. 小瑕疵:空包 `packages/memory`、`.env.example` 中无代码读取的
   `AGENTFLOW_PROVIDER`、mypy --strict 未覆盖 `apps/`;
5. token 估算器的"工程代理"限制只有文字声明,无真实数据支撑。

## 1. Implementation

- **LICENSE**:新增 MIT(仓库此前无任何许可证文件)。
- **README**:顶部加 CI / Python / License 徽章(CI 已实测绿,见 §3);
  新增 "What it looks like" 一节,展示两张真实 Inspector 截图
  (会话时间线视图、损坏轨迹完整性告警),截图路径指向既有证据目录;
  Quick start 数字更新(198 tests);新增 License 一节。
- **interview-script.md**:189→198、96%→95%;4:00–5:00 段新增真实端点
  冒烟谈资(happy path + 三类故障注入全部类型化脱敏,凭据零泄漏)。
- **小瑕疵清理**:
  - 删除空包 `packages/memory/`(仅含 docstring,无任何引用;Memory 仍是
    产品使命的一部分,但 MVP 范围外——ADR 与 roadmap 已记录范围边界);
  - `.env.example` 移除 `AGENTFLOW_PROVIDER`(无任何代码读取的死变量);
  - mypy --strict 范围从 `packages` 扩展到 `packages + apps`(42 个源文件);
    修复 `apps/api/main.py` 两处 `no-any-return`(store 访问先经类型化局部
    变量);Makefile、ci.yml、pyproject 同步。
- **Estimator 校准**:新增 `scripts/calibrate_estimator.py`(手动证据工具,
  与 `check_wheel.py` 同类:不入默认测试矩阵);固定版本化 prompt 集
  (en/zh 短文、JSON、en/zh 长文、代码片段,`PROMPT_SET_VERSION = "1"`)
  发送到真实端点,把真实 `usage` 与 `deterministic-v1`(按文档化计数契约
  `"{role}: {content}"` 序列化)及 `tiktoken-cl100k_base` 并排对比。
  端点模型名不打印、不入报告。

## 2. Verification Commands

```bash
python -m pytest -q -p no:cacheprovider --cov=packages
python -m ruff check .
python -m mypy packages apps
python -m hatchling build -t wheel && python scripts/check_wheel.py dist
python scripts/calibrate_estimator.py --env-file .env   # 手动,需凭据
```

## 3. Results(2026-09-06 实测,Windows 10.0.26200 x64,Python 3.13.13)

### 3.1 CI 实证(关闭发布报告最后一项 follow-up)

GitHub Actions API 实测:`ligo-lili/agentflow` 全部 7 次运行
conclusion = **success**(含 v0.1.0 标签提交 `b711153` 与后续 `82b76ac`、
`190adc2`),Ubuntu+Windows × Python 3.11/3.13 矩阵 + wheel 构建作业全绿。
`docs/evidence/release-v0.1.0/report.md` 第 6 节对应项已勾选。

### 3.2 套件

```text
198 passed;TOTAL 95.31%(floor 80 强制)
All checks passed!(ruff)
Success: no issues found in 42 source files(mypy --strict,packages + apps)
wheel ok: dist\agentflow-0.1.0-py3-none-any.whl (42 files, all 16 required modules present)
```

### 3.3 Estimator 校准(真实端点,6 个固定 prompt,两次运行模式一致)

```text
prompt set version 1; model=<not printed>
prompt                deterministic-v1    tiktoken-cl100k_base              real usage
en_short                       p=7/c=1                 p=7/c=1        p=88/c=19 [stop]
zh_short                      p=4/c=11               p=16/c=38        p=88/c=33 [stop]
json_payload                 p=19/c=16               p=27/c=19       p=107/c=32 [stop]
en_long                     p=87/c=640              p=67/c=532      p=148/c=662 [stop]
zh_long                     p=32/c=218              p=133/c=837     p=150/c=1689 [stop]
code_snippet                p=46/c=538              p=44/c=522      p=127/c=3512 [stop]

mean absolute error vs real usage (tokens per case):
  deterministic-v1       prompt     85.5
  deterministic-v1       completion 753.8
  tiktoken-cl100k_base   prompt     69.0
  tiktoken-cl100k_base   completion 668.0
```

发现(全部支撑既有文档声明,而非推翻):

1. **Prompt 侧存在 ≈80 token 的固定信封开销**:两个短 prompt 真实值均为
   88(与内容无关),估算器只计量内容,不计量端点模板/隐藏开销——
   小 prompt 下 MAE 主要来自这个常数偏移。增量对比时 deterministic-v1
   反而贴合(json_payload 增量 107−88=19,est=19)。
2. **deterministic-v1 对中文低估约 2 倍**(0.25 token/字符 vs 该端点约
   0.5 token/字符):zh_long 增量 62 vs est 32;这正是公式文档声明
   "deliberately coarse" 的量化体现。
3. **cl100k_base 对英文贴合、对该端点的中文分词高估**(133 vs 增量 62)——
   端点分词器对中文比 cl100k 更高效,"proxy of a proxy" 如文所声明。
4. **Completion 侧存在不可见推理 token**:code_snippet 真实 completion
   3512,而可见内容按 tiktoken 仅 ≈522、两次运行模式一致(前次 3813 vs
   ≈434)——该端点模型把推理 token 计入 completion 而不出现在 content 中,
   任何基于可见内容的估算器在此结构性偏低。这是"估算值 ≠ 计费 token"
   契约最具体的一次实证。
5. **真实模型有运行间方差**(同 prompt 两轮 completion 分别为 406→662),
   校准结论是方向性的,不是精确系数。

## 4. Files Changed

- 新增:`LICENSE`、`scripts/calibrate_estimator.py`、本报告。
- 修改:`README.md`(徽章/截图/数字/License 节)、
  `docs/product/interview-script.md`(数字 + 冒烟谈资)、
  `.env.example`(移除死变量)、`apps/api/main.py`(两处 no-any-return)、
  `pyproject.toml`、`Makefile`、`.github/workflows/ci.yml`(mypy 范围)、
  `docs/evidence/README.md`(索引)、
  `docs/evidence/release-v0.1.0/report.md`(follow-up 勾选)。
- 删除:`packages/memory/`(空包)。

## 5. Known Limitations

- 校准样本小(6 个固定 prompt、单端点、无工具调用),结论为方向性;
  脚本为手动证据工具,不在默认测试矩阵(与 check_wheel.py 同等)。
- mypy 覆盖 apps 后,`apps/web/templates` 等非 Python 资产仍不适用类型
  检查(由 Inspector 测试与截图走查覆盖)。

## 6. Follow-up Needed

- GitHub Release 页面发布、About/topics、个人页 pin:需要仓库所有者在
  GitHub 网页操作(沙箱无 gh CLI)。
- 可选:英文/双语 README(取决于目标岗位市场)。
