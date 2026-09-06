# Review Improvements R9+R10 — Inspector Polish, Visual Verification, Portfolio Narrative

日期:2026-09-06
依据:`docs/review-improvement-plan.md` P2 项(R9、R10;R11 决策另见
`docs/adr/ADR-005-package-layout.md`)

## 0. Goal

R9:Inspector 打磨与可视化验证——稳定深链接、空/加载/损坏轨迹/API 错误
状态、响应式检查、基于真实本地数据的截图走查。
R10:文档与面试叙事——读写路径架构图、五分钟面试脚本、限制显著化、
"MVP 完成"与"生产就绪"全文区分。

## 1. Implementation

### R9 — Inspector(apps/web/templates/inspector.html,仍为 Jinja + 原生 JS)

- **深链接**:选中的会话写入 URL hash(`#session=<id>`,`history.replaceState`
  不污染历史);页面加载时从 hash 恢复选择;`hashchange` 监听支持前进/后退。
- **状态完善**:
  - 加载:选会话时所有面板先显示 "loading…"。
  - API 错误:每个面板独立渲染类型化错误(`error.message` 封套)。
  - 损坏轨迹:Replay 409 时显示 `trace invalid (<错误码列表>)`,评估面板
    显示 unavailable;工具/压缩面板重置为空态,不残留旧数据。
  - 空/正常路径维持原有空态文案。
- **XSS 加固**:会话任务、时间线摘要、重放内容/工具值/错误等所有
  数据可达文本统一 `escapeHtml` 后再进 innerHTML。
- **响应式**:`@media (max-width: 900px)` 单列布局(侧栏上移、双列网格展开)。
- 未引入 React/框架(遵守计划约束)。

### R10 — 文档与叙事

- `docs/architecture/overview.md`:新增写路径/读路径图——两条路径只在
  append-only 事件日志与快照库交汇,读路径绝不执行 Provider/Tool。
- `docs/product/interview-script.md`:五分钟面试脚本——一个失败运行
  (超时终态 + 完整性拒绝)+ 一个 A/B 结果(三场景套件、失败案例即头条、
  双权重敏感性),附兜底方案(无终端时用截图)。
- `README.md`:新增 "Status: MVP complete — not production-ready" 小节,
  逐条列出刻意的非目标(estimator 代理、单进程、无鉴权、规则式完成判定、
  同步离线 run);文档索引加入面试脚本。
- `docs/adr/ADR-005-package-layout.md`(R11):记录"维持 packages/ 布局"
  的决策与迁移条件。

## 2. Verification(浏览器实测走查,2026-09-06)

本地 `uvicorn apps.api.main:app --port 8123`,真实浏览器(ZCode 内置)执行:

| 步骤 | 结果 |
|---|---|
| 打开页面 | 八面板空态文案正常,会话列表渲染 |
| 点击 "Create offline run (simple)" | 新会话自动选中,timeline/prompt/context/tools/replay/evaluation 全部填充;replay 显示 `integrity valid` 徽章 |
| 点击 "Create offline run (compaction)" | 压缩会话选中,URL 变为 `#session=d9d3af22…`(深链接生效) |
| 用深链接重开页面 | 自动恢复选中会话并填充全部面板 |
| 窄屏 390px | 单列布局,侧栏上移,面板全宽 |
| 注入伪造事件(序列 99 + 混杂 trace)后重载 | Replay 面板:`trace invalid (SEQUENCE_GAP, MIXED_TRACE_IDS) — replay integrity check failed`;Evaluation:`evaluation unavailable — replay integrity check failed` |
| 清理 | 伪造事件已从 DB 删除,服务停止,临时目录清理 |

截图(`screenshots/`,真实本地运行数据):

- `desktop-deeplink-compaction-session.png` — 深链接恢复 + 压缩会话全面板
- `narrow-viewport-390.png` — 390px 响应式布局
- `corrupt-trace-page-top.png` / `corrupt-trace-replay-panel.png` — 损坏轨迹
  的类型化错误状态

## 3. Files Changed

- 修改:`apps/web/templates/inspector.html`、`docs/architecture/overview.md`、
  `README.md`、`docs/evidence/README.md`、`docs/evidence/final-report.md`
  (重新生成)。
- 新增:`docs/product/interview-script.md`、
  `docs/adr/ADR-005-package-layout.md`、本报告与截图。

## 4. Known Limitations

- 深链接用 `history.replaceState` 实现:不产生历史条目,浏览器"后退"不会
  逐会话回退(选择下一版本可改 `pushState`)。
- 损坏轨迹注入是走查用的带外操作(直连 SQLite),产品内无"制造损坏"入口
  ——这本身是完整性契约的体现。
- 截图为 1280×720 / 390×844 两档;未覆盖超宽屏。

## 5. Follow-up Needed

无(R9/R10/R11 完成)。评审计划全部条目(R1–R11)已交付;final-report.md
已按第 7 节验收门从干净运行输出重新生成。
