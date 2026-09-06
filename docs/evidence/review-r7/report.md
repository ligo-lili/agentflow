# Review Improvement R7 — Optional Real-Provider Smoke Path

日期:2026-09-06
依据:`docs/review-improvement-plan.md` R7(可选;不得削弱离线可复现性)

## 0. Goal

证明运行时的 `ModelProvider` Protocol 能干净地映射到一个真实的
OpenAI 兼容端点——同时保持:离线套件仍是默认且无需 API key;适配器需要
显式环境配置;不把任何 SDK 类型泄漏进核心模型。

## 1. Implementation

- `packages/runtime/openai_provider.py`:`OpenAICompatProvider`
  (`/chat/completions` 适配器,基于 httpx,惰性导入)。
  - `from_env()` 只读显式环境变量 `AGENTFLOW_BASE_URL` / `AGENTFLOW_API_KEY`
    / `AGENTFLOW_MODEL`(可选 `AGENTFLOW_TIMEOUT_SECONDS`,默认 30s);
    缺失时抛 `OpenAIProviderConfigError` 并点名全部缺失键。
  - 核心模型 ↔ 线格映射:`ModelRequest` → `{"model","messages","tools",
    "temperature","max_tokens"}`;响应 → `ModelResponse` + `TokenUsage`
    (usage 数据逐字段保留)+ `ToolCallRequest`(arguments JSON 解析,
    非法即类型化错误)。
  - 凭据与错误体永不外泄:`api_key` 私有保存、从不记录;HTTP ≥400 只报
    状态码(错误体可能回显请求数据);传输错误经 `redact_diagnostic` 脱敏;
    超时映射为 `OpenAIProviderTimeoutError`。
  - 线格说明:运行时转录把 assistant 的 tool_calls 隐式存于后续
    `role="tool"` 消息(带 `tool_call_id`/`name`);序列化在前一条
    assistant 消息上合成 `tool_calls`(`arguments="{}"`,原始参数不在转录
    中,模型下次调用会重新生成);孤立的 tool 消息会显式报错而非伪造。
- `pyproject.toml`:可选 extra `openai-compat = ["httpx>=0.28,<0.29"]`;
  mypy 对 httpx 增加 ignore_missing_imports 容错(非 dev 环境缺失时不阻塞)。
- `tests/test_openai_provider.py`:11 个**离线** mock HTTP 契约测试
  (`httpx.MockTransport`,无网络、无 key)。
- 顺带修复 R7 测试暴露的真实脱敏漏洞:`authorization=Bearer <token>` 中
  `token` 值带认证 scheme 时,旧正则只吞掉 scheme、泄漏真实密钥;
  `packages/runtime/diagnostics.py` 现将 scheme 与密钥一并吞掉,并新增
  独立 `Bearer <长token>` 规则(仅限 16+ 位密钥形字符串,避免误伤普通文本)。

## 2. Verification Commands

```bash
python -m pytest tests/test_openai_provider.py tests/test_redaction.py -q -p no:cacheprovider
python -m ruff check .
python -m mypy packages
```

## 3. Results(2026-09-06 实测)

```text
11 passed                          # mock 契约测试(normal/tool-call/error/timeout/env/unavailable)
189 passed                         # 全套件,仍无网络、无 API key
All checks passed!                 # ruff
Success: no issues found in 39 source files   # mypy --strict
```

Mock 测试覆盖矩阵(验收要求逐项):

| 场景 | 断言 |
|---|---|
| normal response | 线格 payload(model/messages/Authorization 头)、核心模型映射、usage 逐字段保留 |
| tool call | 响应侧 arguments JSON→dict;请求侧 tools spec 序列化;转录中 tool 观察合成 assistant.tool_calls + role=tool 消息 |
| provider error | HTTP 500 → `OpenAIProviderError`,响应体与 key 均不出现在错误消息 |
| transport error | `ConnectError` → 类型化错误,诊断脱敏(含 Bearer scheme 漏洞回归) |
| timeout | `ReadTimeout` → `OpenAIProviderTimeoutError`(“30s HTTP deadline”),key 不外泄 |
| env 配置 | 缺失键逐一点名;超时非数字 → 配置错误;完整 env → 构造成功 |
| 无 SDK 类型泄漏 | 响应图深检:仅核心模型字段 |
| httpx 缺失 | `OpenAIProviderUnavailableError` |

## 4. Manual smoke procedure(需真实端点;不在默认测试中)

环境准备(值保密,不要提交):

```powershell
pip install -e ".[openai-compat]"
$env:AGENTFLOW_BASE_URL = "https://<your-endpoint>/v1"
$env:AGENTFLOW_API_KEY  = "<your-key>"
$env:AGENTFLOW_MODEL    = "<your-model>"
```

最小冒烟脚本(交互式执行,不入库):

```python
from packages.core.provider import ModelMessage, ModelRequest
from packages.runtime.openai_provider import OpenAICompatProvider

provider = OpenAICompatProvider.from_env()
response = provider.invoke(
    ModelRequest(
        model=provider.model,
        messages=(ModelMessage(role="user", content="Reply with exactly: OK"),),
    )
)
print(response.finish_reason, response.message.content, response.usage)
```

预期:`stop OK TokenUsage(prompt_tokens=…, completion_tokens=…)`。再观察:
- 断网/错 base_url → `OpenAIProviderError`(诊断已脱敏,无 key);
- 错 key → `provider returned HTTP 401`(无响应体回显);
- 将 `AGENTFLOW_TIMEOUT_SECONDS=0.001` → `OpenAIProviderTimeoutError`。

烟测记录本沙箱未执行(无网络、无凭据)——这是设计上的手动步骤;
mock 契约测试为自动化证据。

## 5. Files Changed

- 新增:`packages/runtime/openai_provider.py`、`tests/test_openai_provider.py`、
  本报告。
- 修改:`packages/runtime/diagnostics.py`(Bearer scheme 脱敏漏洞)、
  `tests/test_redaction.py`(回归测试)、`pyproject.toml`(extra + mypy
  override)、`.env.example`(超时变量)、`packages/runtime/__init__.py`(导出)。

## 6. Known Limitations

- 原始工具调用参数不在运行时转录中,续传请求以 `arguments="{}"` 合成
  (模型会重新生成参数);严格校验参数内容的端点可能拒绝该形状。
- 未实现流式(stream)——MVP 的 Protocol 即单次 invoke。
- 真实端点冒烟依赖用户手动执行(沙箱无网络/凭据),自动化证据为 mock
  契约测试。

## 7. Follow-up Needed

无(R7 完成)。剩余:R9–R11(作品集打磨)与第 7 节验收门。
