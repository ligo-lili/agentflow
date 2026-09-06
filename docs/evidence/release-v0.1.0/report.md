# Release v0.1.0 — 发布收尾

日期:2026-09-06
依据:任务计划 T-Release(评审计划发布序列最后一步:"tag a
correctness-focused release candidate")与 `docs/review-improvement-plan.md`
第 4 节。

## 0. Goal

补齐评审改进周期收尾后遗留的三件事,使 v0.1.0 成为可引用的发布版本:

1. 修正 README 与 final-report 之间的数字漂移(测试数、覆盖率);
2. 把 R7 的手动冒烟从"交互式、不入库、不可复现"升级为入库脚本
   `scripts/smoke_openai.py`(显式 env 配置,凭据永不打印,离线行为有测试);
3. 打 `v0.1.0` 标签并提交发布证据。

## 1. Implementation

- 新增 `scripts/smoke_openai.py`:执行 R7 报告第 4 节文档化的手动冒烟流程。
  - 默认:一次真实 `/chat/completions` 调用,打印 `finish_reason`、内容与
    `usage`;`finish_reason != "stop"` 或内容为空视为失败;内容不含 "OK"
    时仅提示(传输与映射已验证,模型服从性不阻塞)。
  - `--fault-checks`:追加三项故障注入——超时
    (`timeout_seconds=0.001` → `OpenAIProviderTimeoutError`)、无效凭据
    (静态哑 key → 预期 HTTP 401 类错误)、不可路由 base_url
    (RFC 2606 `.invalid` 域名 → 传输错误);全部期望类型化、脱敏的失败。
  - `--env-file <path>`(2026-09-06 补充):从显式指定的 dotenv 文件读取
    配置(`KEY=VALUE`、`#` 注释、可选 `export` 前缀、去除包裹引号;畸形行
    跳过并提示),文件值覆盖环境变量。保持"显式配置"原则——不做隐式
    `.env` 自动加载;配合被 gitignore 的 `.env` 使用,密钥无需进入 shell
    历史或会话记录。
  - 缺配置/缺 httpx/文件缺失 → 打印缺失键与安装说明,退出码 2;
    冒烟失败退出码 1;通过退出码 0。
  - 凭据安全:api_key 仅私有保存;哑 key 为静态值,非真实 key 的派生串;
    适配器错误只报状态码或脱敏诊断,脚本不打印任何 env 值或文件内容。
- 新增 `tests/test_smoke_script.py`(9 项,离线):缺配置行为与指引、
  happy path 输出与 key 不泄漏、provider 失败退出码与 key 不泄漏、
  故障注入脚手架构造参数与全部类型化失败、未知参数拒绝、env 文件覆盖
  优先级与引号/export 解析、文件缺失、`--env-file` 缺路径参数、畸形行
  跳过。真实端点调用本身仍属手动步骤,不进入默认测试矩阵(离线第一原则)。
- `README.md`:修正漂移——`make test` 注释 176 → 189(当前 198,见下),
  `make coverage` 注释 96% → 95%(实测 95.31%)。README 快速注释采用
  稳健取整,避免每次加测试都漂移;精确数字以证据报告为准。

## 2. Verification Commands

```bash
python -m pytest -q -p no:cacheprovider --cov=packages --cov-report=term
python -m ruff check .
python -m mypy packages
python -m hatchling build -t wheel && python scripts/check_wheel.py dist
python scripts/smoke_openai.py          # 缺配置路径,期待退出码 2
```

## 3. Results(2026-09-06 实测,Windows 10.0.26200 x64,Python 3.13.13)

```text
198 passed                                          # pytest(189 + 冒烟脚本 9)
TOTAL                                    1940     91    95%
Required test coverage of 80.0% reached. Total coverage: 95.31%
All checks passed!                                  # ruff
Success: no issues found in 39 source files         # mypy --strict
wheel ok: dist\agentflow-0.1.0-py3-none-any.whl (42 files, all 16 required modules present)
```

`python scripts/smoke_openai.py`(无 env 配置)实测输出与退出码:

```text
not configured: missing environment variables: AGENTFLOW_BASE_URL, AGENTFLOW_API_KEY, AGENTFLOW_MODEL (see .env.example)
<安装与配置指引,含 .env.example 与 R7 报告第 4 节引用>
exit code: 2
```

`python scripts/smoke_openai.py --env-file nope.env`(文件不存在)实测:
`env file not found: nope.env`,退出码 2。

本报告数字为本发布时点全新运行,取代 final-report 第 3 节的时点数字
(189 passed / 95.31%);最终口径以本报告为准。

### 3.1 真实端点冒烟(2026-09-06 已执行,关闭 R7 手动步骤)

2026-09-06 本机盘点:无 `AGENTFLOW_*`/API 相关环境变量,用户级持久环境
变量与 E:\ 一级项目目录均无 `.env`;网络连通性正常
(api.deepseek.com HTTP 401 要求鉴权、open.bigmodel.cn HTTP 200,均为
可达证明)。凭据由用户写入(先误写入被跟踪的 `.env.example`,bootstrap
脚本已把值迁移到 gitignored 的 `.env` 并还原模板,值未进入会话记录),
随后执行 `python scripts/smoke_openai.py --env-file .env --fault-checks`,
对某个 OpenAI 兼容端点实测输出:

```text
[1/1] happy path: one real /chat/completions call ...
finish_reason=stop
content='OK'
usage=prompt_tokens=88 completion_tokens=17
happy path OK
[fault 1/3] timeout injection ...
  OK: OpenAIProviderTimeoutError: provider request exceeded its 0.001s HTTP deadline
[fault 2/3] invalid credential (expect HTTP 401) ...
  OK: OpenAIProviderError: provider returned HTTP 401
[fault 3/3] unroutable base URL ...
  OK: OpenAIProviderError: provider request failed: ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1032)
fault checks OK (all failures typed, no key or body leaked)
```

结论:真实端点上 happy path(`stop` + 逐字段 usage)与全部三类故障注入
均符合 R7 报告第 4 节的预期行为;端点与 key 均未出现在任何输出中
(本节只记录用量与错误类别)。取代 `review-r7/report.md` 第 4 节
"烟测未执行"的时点说明。

## 4. Files Changed

- 新增:`scripts/smoke_openai.py`、`tests/test_smoke_script.py`、本报告。
- 修改:`README.md`(数字漂移)、`docs/evidence/README.md`(索引登记本报告)。

## 5. Known Limitations

- `--fault-checks` 的"不可路由 base_url"一项在实测网络中表现为 TLS 握手
  被截断(`SSL: UNEXPECTED_EOF_WHILE_READING`,本地网络有 DNS 代答),而非
  DNS 解析失败;类型化错误(`OpenAIProviderError` + 脱敏诊断)仍符合契约,
  但"非 DNS 失败形态"与 R7 报告的字面预期不同,如实记录。
- 脚本未纳入 mypy --strict 范围(配置仅覆盖 `packages`),由 ruff 与
  9 项离线测试覆盖,与 `scripts/check_wheel.py` 同等对待。

## 6. Follow-up Needed

- [x] 真实端点冒烟:已完成,见第 3.1 节(2026-09-06)。
- [ ] 推送后在 GitHub Actions 确认 Ubuntu+Windows × 3.11/3.13 矩阵与
  wheel 作业绿灯(沙箱无 gh CLI,无法本地观测)。
