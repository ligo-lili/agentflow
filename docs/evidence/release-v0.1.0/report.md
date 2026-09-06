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
  - 缺配置/缺 httpx → 打印缺失键与安装说明,退出码 2;冒烟失败退出码 1;
    通过退出码 0。
  - 凭据安全:api_key 仅私有保存;哑 key 为静态值,非真实 key 的派生串;
    适配器错误只报状态码或脱敏诊断,脚本不打印任何 env 值。
- 新增 `tests/test_smoke_script.py`(5 项,离线):缺配置行为与指引、
  happy path 输出与 key 不泄漏、provider 失败退出码与 key 不泄漏、
  故障注入脚手架构造参数与全部类型化失败、未知参数拒绝。
  真实端点调用本身仍属手动步骤,不进入默认测试矩阵(离线第一原则)。
- `README.md`:修正漂移——`make test` 注释 176 → 189(当前 194,见下),
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
194 passed                                          # pytest(189 + 冒烟脚本 5)
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

本报告数字为本发布时点全新运行,取代 final-report 第 3 节的时点数字
(189 passed / 95.31%);最终口径以本报告为准。

## 4. Files Changed

- 新增:`scripts/smoke_openai.py`、`tests/test_smoke_script.py`、本报告。
- 修改:`README.md`(数字漂移)、`docs/evidence/README.md`(索引登记本报告)。

## 5. Known Limitations

- 真实端点冒烟仍未执行(沙箱无网络/凭据);`--fault-checks` 的 401 与
  不可路由两类检查依赖真实网络行为(DNS 失败/拒绝响应的形态因端点而异)。
- 脚本未纳入 mypy --strict 范围(配置仅覆盖 `packages`),由 ruff 与
  5 项离线测试覆盖,与 `scripts/check_wheel.py` 同等对待。

## 6. Follow-up Needed

- 有凭据时执行:`python scripts/smoke_openai.py --fault-checks`,把输出
  追加到本报告第 3 节。
- 推送后在 GitHub Actions 确认 Ubuntu+Windows × 3.11/3.13 矩阵与 wheel
  作业绿灯(沙箱无 gh CLI,无法本地观测)。
