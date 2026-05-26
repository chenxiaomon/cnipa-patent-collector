# CNIPA Patent Collector - 专利数据采集系统

自动化从中国知识产权局（CNIPA）采集专利数据的系统。采集 **申请号 + 13 个基础字段 + 3 个发文字段**（条件采集），支持断点续传。**本次采集成功率 99.99%（9421/9422）**。

系统同时支持三种采集模式：按申请号自动采集、Phase 0 手动按申请人采集、公开查询手动/半自动翻页采集。

## 🚀 快速开始（30 秒）

### 可视化控制台

```bash
python web_dashboard.py
```

打开：`http://127.0.0.1:8765`

可在页面中查看采集概览、启动代理、生成动态清单、执行测试采集/按清单更新、查看任务日志。详见 [VISUAL_UI_GUIDE.md](VISUAL_UI_GUIDE.md)。

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
| **[VISUAL_UI_GUIDE.md](VISUAL_UI_GUIDE.md)** | 本地可视化控制台启动和操作流程 | 🟢 日常操作 |
| **[docs/decision-log.md](docs/decision-log.md)** | 技术决策及原因（MITM 为什么？条件采集为什么？） | 🟠 理解设计意图、讨论优化 |
| **[docs/ai-context.md](docs/ai-context.md)** | 当前目标、约束、风险、下一步（给 AI 的 1 屏纲要） | 🤖 AI/Harness 协作 |
| **[docs/worklog.md](docs/worklog.md)** | 每次改动记录（日期、目标、结果、下一步） | 📝 了解项目演进 |

---

## 📊 采集数据

### 采集模式

| 模式 | 适用场景 | 主要入口 | 输出 |
|------|----------|----------|------|
| 自动按申请号采集 | 已有申请号列表，需要批量采集基础字段 | `main_automation.py` | `detection_log.jsonl`, `patents_data.xlsx` |
| Phase 0 手动采集 | 按申请人/关键词在 CNIPA 中手动搜索，补充一批申请号 | `start_browser_for_phase0.py`, `import_from_cache.py` | 导入主日志 |
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
MITM 代理 (127.0.0.1:8082) 拦截 API 响应
    ↓
detection_logger.py 记录 JSONL
    ↓
patents_data.xlsx (Excel 报表) + detection_log.jsonl (完整日志)
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
| `sync.py` | 多机同步：导出 DB → 推送 / 拉取 → 重建 DB，5 个命令（见下方） | ✅ 活跃 |
| `main_automation.py` | 主流程：浏览器控制、申请号循环、数据采集 | ✅ 活跃 |
| `detection_logger.py` | 日志记录：JSONL 追加写入、Excel/JSON 导出 | ✅ 活跃 |
| `patent_mitm_scraper.py` | MITM 插件：API 拦截、字段解析 | ✅ 活跃 |
| `collect_fwxx.py` | 补采脚本：补采漏掉的发文信息 | ✅ 活跃 |
| `main_automation.py --update-list` | 重试/强制更新：重新采集指定申请号列表 | ✅ 活跃 |
| `start_browser_for_phase0.py` | Phase 0：打开带代理浏览器，用户手动按申请人搜索 | ✅ 可用 |
| `import_from_cache.py` | Phase 0：将手动浏览产生的缓存导入主日志 | ✅ 可用 |
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
    ├── detection_log.jsonl   # ⭐ git 备份（由 sync.py push 刷新，多机同步载体）
    ├── patents_data.xlsx     # 输出：最终报表（Excel，.gitignore 排除）
    └── public_search_results.xlsx/json
```

---

## ⚠️ 已知问题 & 优化计划

| 问题 | 优先级 | 状态 | 计划 |
|------|--------|------|------|
| **falvzt 不可用（全为 `--`）** | P0 | ✅ 已确认 | 以 anjianywzt 为准（实测 9422 条采集数据验证） |
| **JSON 文件 RMW 非原子写入** | P0 | ✅ 已完成 | 已升级为 JSONL 追加写入 + fsync；JSON 仅作兼容导出/备份 |
| **数据缺口** | P0 | ✅ 已收口 | 1 条 2025 年申请暂不可采，5 条缺发文接受现状 |
| **代码重复（浏览器/输入逻辑）** | P1 | 🟡 未改 | 模块化提取（3 周） |
| **路径策略不统一** | P1 | 🟡 主要完成 | settings.py 已覆盖核心脚本；少量辅助脚本待迁移 |
| **文档状态同步** | P1 | ✅ 本轮收口 | README 已同步成功率、JSONL 状态、手动采集入口 |

**详见**: [docs/ai-context.md](docs/ai-context.md#当前风险--处理计划)

---

## 🖥️ 多机协作（GitHub 同步）

跨机器运行时，通过 `sync.py` 同步采集进度，避免重复采集。

**数据架构**：`patents.db`（SQLite，本地运行时主存储，`.gitignore` 排除）通过 `detection_log.jsonl`（git 追踪的文本备份）在多机之间流转。

### 新机器一键初始化

```bash
git clone https://github.com/chenxiaomon/cnipa-patent-collector.git
cd cnipa-patent-collector
pip install -r requirements.txt

# 从远端 JSONL 重建本地 DB（导入历史记录，避免重复采集）
python sync.py init
```

### 每次采集前（拉取最新进度）

```bash
python sync.py pull        # git pull + 重建 DB（知道哪些已采）
```

### 每次采集后（推送本次进度）

```bash
python sync.py push        # 导出 DB → 提交 JSONL → git push
```

### 其他 sync 命令

| 命令 | 说明 |
|------|------|
| `python sync.py status` | 查看本地记录数和同步状态 |
| `python sync.py rebuild` | 从现有 JSONL 重建 DB（DB 损坏/迁移恢复用） |

> `sync.py pull/push` 均支持冲突自动合并：以申请号为键，timestamp 较新的记录优先。

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

# 浏览完成后：把 patent_cache.json 导入主日志
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

## 📊 项目统计

| 指标 | 值 |
|------|-----|
| **本次采集成功率** | 99.99%（9421 成功 / 9422 总数，status_code=200） |
| **发文采集覆盖** | 99.64%（1398 有发文 / 1403 驳回复审） |
| **数据一致性** | JSONL/Excel 行数一致，发文列表数一致 |
| **数据架构** | 申请号 + 13 个基础字段 + 3 个发文字段（条件采集） |
| **采集模式** | 自动按申请号采集 + Phase 0 手动采集 + 公开查询采集 |
| **项目文档** | 7 份（project-brief, architecture, domain-rules, runbook, decision-log, ai-context, worklog） |
| **核心代码文件** | 5 个（main_automation, detection_logger, patent_mitm_scraper, collect_fwxx, validate_results） |

---

**最后更新**: 2026-05-13  
**项目阶段**: 阶段 2 - 工程化优化  
**下次审查**: 2026-05-17  
**维护人**: @minxiaochen
