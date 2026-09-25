# Phase 6.3 — Deployment: Auth, CORS, Health, Docker (T11)

日期:2026-09-06
依据:`docs/roadmap/phase-6-production.md` T11。Phase 6 收官。

## 0. Goal

把单机服务做成可部署形态:可选 Bearer 鉴权、可选 CORS、永不鉴权的
healthz、单容器 Docker 部署(SQLite 落卷、非 root、healthcheck),部署
变量与信任边界成文。

## 1. Implementation

- **鉴权** `AGENTFLOW_AUTH_TOKEN`(可选):设置后 `/api/*` 要求
  `Authorization: Bearer <token>`,`hmac.compare_digest` 常量时间比较,
  401 走统一错误封套(`UNAUTHORIZED`);未设置 = 无鉴权(本机用法不变)。
  `GET /healthz` 与 Inspector 页面不要求 token——浏览器首次 401 时提示
  输入一次并存 localStorage,之后随请求携带。
- **CORS** `AGENTFLOW_CORS_ORIGINS`(逗号分隔,可选):FastAPI
  CORSMiddleware;未设置 = 仅同源。
- **`GET /healthz`**:liveness 探针,永不鉴权(Docker HEALTHCHECK 用)。
- **Dockerfile**:`python:3.13-slim`、依赖层与源码分离、非 root 用户
  (agentflow)、`/data` 卷(`AGENTFLOW_DB_PATH=/data/agentflow.db`)、
  `HEALTHCHECK` 打 healthz、安装 `.[openai-compat]`(真实 provider 需要
  httpx);`.dockerignore` 排除开发资产、保留示例工具模块。
- **docker-compose.yml**:`env_file: .env`(不入库)+ named volume +
  restart 策略。
- **`docs/architecture/deployment.md`**:完整环境变量表(13 个)、信任
  边界(工具模块 = 运营者受信代码 / 单 token 非多租户 / SQLite 单进程
  / 备份 = 复制文件)、health 与日志说明。
- `create_app` 增加 `env` 参数(配置注入点,测试用);README 状态节改写
  为"单机就绪、边界明确",新增 Deploy 节。

## 2. Verification Commands

```bash
python -m pytest -q -p no:cacheprovider
python -m ruff check .
python -m mypy packages apps
docker build -t agentflow:0.2.0 .
# 部署级冒烟(真实端点凭据在 .env,值不出现在本报告):
docker run -d --name agentflow-smoke -p 18000:8000 --env-file .env \
  -e AGENTFLOW_PROVIDER=openai-compat \
  -e AGENTFLOW_TOOLS_MODULE=examples/agentflow_tools.py agentflow:0.2.0
python -m pytest tests/test_deployment.py -q -p no:cacheprovider
```

## 3. Results(2026-09-06 实测,Windows 10.0.26200 x64,Python 3.13.13)

### 3.1 套件

```text
226 passed, 2 skipped            # 全套件(新增部署 6 项)
All checks passed!(ruff)
Success: no issues found in 46 source files(mypy --strict)
```

部署测试矩阵(全部离线):healthz 免鉴权、无/错/畸形 token → 401 封套
(不回显 token)、正确 token → 200、未配置鉴权时无 header 可用、
CORS 允许源回显 allow-origin / 未列源不回显。

### 3.2 Docker 构建(本机 Docker 29.7.2)

- 基础镜像经镜像源拉取(docker.io 直连被网络阻断,`docker.m.daocloud.io`
  拉取后本地 retag;未改动用户守护进程配置——网络受限环境的操作注记)。
- 初版构建暴露两个真实问题并修复:① 非 root 用户切换后 chown /data 失败
  (顺序错误,chown 提前到 USER 之前);② 基础安装无 httpx,配置真实
  provider 时启动即 fail-fast(镜像改装 `.[openai-compat]`;容器对
  `AGENTFLOW_TOOLS_MODULE` 指向不存在文件同样启动即失败——坏配置绝不
  悄悄上线,符合设计)。
- 最终构建成功:`agentflow:0.2.0`。

### 3.3 部署级真实冒烟(容器 → 真实端点 → 202 → 轮询 → 重放)

```text
healthz: 200 {'status': 'ok'}
POST /api/runs: 202 {'session_id': '6c515c5a…', 'scenario': 'task', 'status': 'running', …}
final status: finished
replay integrity: True
answer: 7
tool: word_count | ok: True | value: 7
```

任务:"Use the word_count tool on 'AgentFlow deploys as a single container
now.' and answer with just that number." —— 真实模型调用真实工具,答案
7 正确;全程 202/轮询协议,凭据与端点不出现在输出。

## 4. Files Changed

- 新增:`Dockerfile`、`docker-compose.yml`、`.dockerignore`、
  `docs/architecture/deployment.md`、`tests/test_deployment.py`、本报告。
- 修改:`apps/api/config.py`(auth_token/cors_origins)、
  `apps/api/main.py`(中间件/healthz/env 参数)、
  `apps/web/templates/inspector.html`(token 提示与携带)、
  `README.md`(Deploy 节、状态节、文档地图)、`.env.example`(部署变量)、
  `docs/evidence/README.md`。

## 5. Known Limitations

- 单 bearer token、无每用户身份;SQLite 单写进程;无流式——均为记录在
  案的非目标(roadmap phase-6)。
- Docker Hub 直连被本机网络阻断时,基础镜像需经镜像源获取(部署文档
  未写死该步骤;网络正常环境 `docker compose up --build` 直接可用)。
- compose 未做 TLS/反代:暴露 8000 端口明文,公网部署应由用户前置反代
  (nginx/caddy/云 LB)——与单机定位一致,成文于 deployment.md。

## 6. Follow-up Needed

无(Phase 6 全部完成)。候选后续(需新决策):Postgres 后端、流式
时间线、英文 README。
