# CNIPA Patent Collector - 专利数据采集系统

自动化从中国知识产权局（CNIPA）采集专利数据的系统。采集 **申请号 + 13 个基础字段 + 3 个发文字段**（条件采集），支持断点续传。

系统同时支持三种采集模式：按申请号自动采集、Phase 0 手动按申请人采集、公开查询手动/半自动翻页采集。

<!-- AUTO_STATS_START -->
## 当前数据快照

> 由 `update_readme_stats.py` 从 SQLite 自动生成，更新时间：2026-07-11 14:15 UTC。

| 指标 | 数值 |
|------|-----:|
| 唯一申请号 | 31,819 |
| 成功采集 | 26,119 |
| 采集失败 | 5,395 |
| 待采记录 | 305 |
| 已尝试成功率 | 82.88% |
| 驳回等复审 | 2,171 |
| 待补发文 | 2 |
<!-- AUTO_STATS_END -->

## 🚀 快速开始（30 秒）

### 可视化控制台

```bash
uv run python web_dashboard.py --host 127.0.0.1
```

打开：`http://127.0.0.1:8765`

可在页面中查看采集概览、启动代理、生成动态清单、执行测试采集/按清单更新、查看任务日志。详见 [VISUAL_UI_GUIDE.md](docs/guides/VISUAL_UI_GUIDE.md)。

### 命令行方式

```bash
# 1. 准备申请号列表
echo -e "CN201880002233\nCN201880002234" > data/search_list.txt

# 2. 终端 1：启动代理
python start_mitm_proxy.py

# 3. 终端 2：启动采集
USE_MITM_PROXY=true python main_automation.py
```

**详见**: [📖 docs/runbook.md](docs/runbook.md) - 完整操作手册

---

## 📚 文档导航

| 文档 | 用途 | 适合人 |
|------|------|--------|
| **[docs/project-brief.md](docs/project-brief.md)** | 项目目标、范围、成功标准 | 🟢 首次接触、项目经理 |
| **[docs/architecture.md](docs/architecture.md)** | 采集链路、文件职责、数据流 | 🟠 代码改动、新功能开发 |
| **[docs/domain-rules.md](docs/domain-rules.md)** | 字段定义、采集触发条件、失败处理 | 🔴 理解业务规则、排查问题 |
| **[docs/runbook.md](docs/runbook.md)** | 启动命令、故障排查、性能调优 | 🟢 日常运维 |
| **[VISUAL_UI_GUIDE.md](docs/guides/VISUAL_UI_GUIDE.md)** | 本地可视化控制台启动和操作流程 | 🟢 日常操作 |
| **[docs/decision-log.md](docs/decision-log.md)** | 技术决策及原因（MITM 为什么？条件采集为什么？） | 🟠 理解设计意图、讨论优化 |
| **[docs/ai-context.md](docs/ai-context.md)** | 当前目标、约束、风险、下一步（给 AI 的 1 屏纲要） | 🤖 AI/Harness 协作 |
| **[docs/worklog.md](docs/worklog.md)** | 每次改动记录（日期、目标、结果、下一步） | 📝 了解项目演进 |

---

## 📊 采集数据

### 采集模式

| 模式 | 适用场景 | 主要入口 | 输出 |
|------|----------|----------|------|
| 自动按申请号采集 | 已有申请号列表，需要批量采集基础字段 | `main_automation.py` | `patents.db`, `patents_data.xlsx`；批次后刷新 JSONL 备份 |
| Phase 0 手动采集 | 按申请人/关键词在 CNIPA 中手动搜索，补充一批申请号 | `start_browser_for_phase0.py`, `import_from_cache.py` | 导入 SQLite |
| 公开查询采集 | 使用 publicSearch 页面手动或半自动翻页采集搜索结果 | `launch_browser_with_proxy.py`, `auto_paginate.py`, `export_public_search.py` | `public_search_results.xlsx/json` |

### 采集的字段

**基础信息 (申请号 + 13 个专利字段)**
```
申请号 + 以下 13 个字段：
famingzlsqgbg, shouquanggh, zhuanlimc, shenqingrxm, zhuanlilx, 
shenqingr, gongkaiggh, falvzt, gongkaiggr, shouquanggr, 
zhufenlh, anjianbh, anjianywzt
```

**发文信息 (3 字段，条件采集 - 仅当 anjianywzt == '驳回等复审请求')**
```
fwxx_list, bhsjtzs_xiazaisj, bhsjtzs_data
```

### 数据流向

```
search_list.txt (申请号列表)
    ↓
PyAutoGUI (鼠标操作) → CNIPA 网站
    ↓
MITM 代理 (127.0.0.1:8083) 拦截 API 响应
    ↓
PatentsDB 写入 patents.db
    ↓
批次结束导出 patents_data.xlsx + detection_log.jsonl Git 备份
```

---

## ⚙️ 系统特点

✅ **稳定** - MITM + PyAutoGUI，规避反爬虫检测  
✅ **完整** - 申请号 + 13 个专利字段 + 3 个发文字段  
✅ **可靠** - 断点续传，浏览器启动自动重试，支持手动重试和补采  
✅ **灵活** - 支持自动采集、手动按申请人采集、公开查询采集  
✅ **可扩展** - 模块化设计，易于维护和修改  
✅ **透明** - 详细文档，决策记录，协作日志  

---

## 🔧 核心文件职责

| 文件 | 职责 | 状态 |
|------|------|------|
| `web_dashboard.py` | 本地 Web 控制台：采集概览、任务启动、日志查看、清单/配置编辑 | ✅ 活跃 |
| `sync.py` | 本机数据库初始化、恢复和状态查看；旧双向 pull/push 已禁用 | ✅ 活跃 |
| `main_automation.py` | 主流程：浏览器控制、申请号循环、数据采集 | ✅ 活跃 |
| `detection_logger.py` | 通过 PatentsDB 写入，生成 Excel/JSON/JSONL 导出 | ✅ 活跃 |
| `patent_mitm_scraper.py` | MITM 插件：API 拦截、字段解析 | ✅ 活跃 |
| `collect_fwxx.py` | 补采脚本：补采漏掉的发文信息 | ✅ 活跃 |
| `main_automation.py --update-list` | 重试/强制更新：重新采集指定申请号列表 | ✅ 活跃 |
| `start_browser_for_phase0.py` | Phase 0：打开带代理浏览器，用户手动按申请人搜索 | ✅ 可用 |
| `import_from_cache.py` | Phase 0：将手动浏览产生的缓存导入 SQLite | ✅ 可用 |
| `launch_browser_with_proxy.py` | 公开查询：打开带代理的 publicSearch 浏览器 | ✅ 可用 |
| `auto_paginate.py` | 公开查询：半自动翻页 | ✅ 可用 |
| `export_public_search.py` | 公开查询：导出 Excel/JSON | ✅ 可用 |

**详见**: [docs/architecture.md](docs/architecture.md#核心文件职责)

---

## 📍 数据位置

```
data/
├── patents.db                # ⭐ 运行时主存储（SQLite，.gitignore 排除，所有读写经此）
├── search_list.txt           # 输入：申请号列表
├── config.json               # 配置：鼠标坐标（本机专用，.gitignore 排除）
├── patent_cache.json         # 临时：MITM 缓存（可删除）
├── patent_fwxx_cache.json    # 临时：发文 MITM 缓存（可删除）
├── raw_responses/            # 公开查询原始响应
├── raw_searches/             # 公开查询 JSONL 记录
└── results/
    ├── detection_log.jsonl   # Git 备份（由 replica 拉取增量后刷新并提交）
    ├── patents_data.xlsx     # 输出：最终报表（Excel，.gitignore 排除）
    └── public_search_results.xlsx/json
```

---

## ⚠️ 已知问题 & 优化计划

| 问题 | 优先级 | 状态 | 计划 |
|------|--------|------|------|
| **falvzt 不可用（全为 `--`）** | P0 | ✅ 已确认 | 业务判断统一使用 anjianywzt |
| **MITM 超时历史失败** | P0 | 🟡 待部署机重采 | 用 `analyze_failures.py` 分析，当前 0 状态均归类为建议重采 |
| **旧 NULL 状态语义不明** | P0 | 🟡 待部署机迁移 | master 执行 `normalize_pending_status.py --apply` 后统一为 -1（待采） |
| **文档统计腐化** | P1 | ✅ 已解决 | README 数据快照由 `update_readme_stats.py` 从 SQLite 生成 |

**详见**: [docs/ai-context.md](docs/ai-context.md#当前风险--处理计划)

---

## 🖥️ Master / Replica 协作

部署机是唯一数据 master；开发机和 Mac 是 replica。每台机器必须在不入 Git 的 `data/machine_role.txt` 中写入角色。

**数据架构**：master 的 `patents.db` 是生产真相源；replica 通过 Dashboard HTTP 增量接口拉取数据，并把刷新后的 `detection_log.jsonl` 提交到 Git。

### 新机器一键初始化

```bash
git clone https://github.com/chenxiaomon/cnipa-patent-collector.git
cd cnipa-patent-collector
uv sync --frozen --python 3.11

echo replica > data/machine_role.txt

# 从远端 JSONL 重建本地 DB（导入历史记录，避免重复采集）
uv run python sync.py init
```

### Replica 每日单命令回流

```bash
export CNIPA_MASTER_URL=http://部署机IP:8765
uv run python sync_pull_from_master.py
git push
```

脚本以 master 数据库的最后修改时间为游标，覆盖新增记录和已有记录更新；成功后自动合并 SQLite、刷新 JSONL 和 README，并创建 Git 提交。旧游标首次升级会做一次全量重对账，`git push` 保留人工确认。

### 数据库恢复命令

| 命令 | 说明 |
|------|------|
| `uv run python sync.py status` | 查看本地记录数和同步状态 |
| `uv run python sync.py rebuild` | replica 从现有 JSONL 重建 DB |

> master 默认拒绝重建。只有明确承担风险时，才允许同时添加 `--force --i-know-this-is-master`。

---

## 💡 常见任务

### 采集前 5 条测试
```bash
USE_MITM_PROXY=true python main_automation.py --test 5
```

### 从中断点续传
```bash
# main_automation.py 自动识别已采集的申请号，跳过并继续
USE_MITM_PROXY=true python main_automation.py
```

### 重试失败申请号
```bash
USE_MITM_PROXY=true python main_automation.py --update-list data/retry_failed.txt
```

### 补采发文信息
```bash
python collect_fwxx.py
```

### Phase 0 手动按申请人采集
```bash
# 终端 1：启动主 MITM 代理
python start_mitm_proxy.py

# 终端 2：打开带代理浏览器，手动登录、按申请人搜索、翻页
python start_browser_for_phase0.py

# 浏览完成后：把 patent_cache.json 导入 SQLite
python import_from_cache.py
```

### 公开查询手动/半自动采集
```bash
# 终端 1：启动 publicSearch 专用代理
python start_mitm_public_search.py

# 终端 2：打开带代理 publicSearch 页面，手动输入查询条件
python launch_browser_with_proxy.py

# 可选：让脚本自动翻页
python auto_paginate.py --delay 1.5 --max-pages 50

# 采集完成后导出公开查询结果
python export_public_search.py
```

**更多命令**: [docs/runbook.md](docs/runbook.md#常见操作)

---

## 🆘 故障排查

**MITM 超时导致采集失败？** → [docs/runbook.md#-采集超时8s-未收到数据](docs/runbook.md)  
**浏览器创建失败？** → [docs/runbook.md#-浏览器创建失败](docs/runbook.md)  
**文件损坏（JSON 解析错误）？** → [docs/runbook.md#-文件损坏json-解析错误](docs/runbook.md)  

---

## 🤝 协作指南

### 改代码前请先读：
1. [docs/project-brief.md](docs/project-brief.md) - 项目范围和目标
2. [docs/architecture.md](docs/architecture.md) - 采集链路和文件职责
3. [docs/domain-rules.md](docs/domain-rules.md) - 业务规则（特别是 anjianywzt 判定）
4. [docs/decision-log.md](docs/decision-log.md) - 技术决策和约束

### 改代码后请：
- [ ] 测试 5+ 个申请号（确保不破坏主流程）
- [ ] 在 [docs/worklog.md](docs/worklog.md) 追加改动记录
- [ ] 更新受影响的文档

---

## 📊 统计口径

实时数量只维护在本文顶部的自动生成区块；成功率分母为已尝试记录（成功 + 失败），不包含 `status_code=-1` 的待采记录。

---

**最后更新**: 2026-07-11
**项目阶段**: 阶段 3 - 主从部署与无人值守加固
**维护人**: @minxiaochen
