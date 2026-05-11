# CNIPA Patent Collector - 专利数据采集系统

自动化从中国知识产权局（CNIPA）采集专利数据的系统。采集 **申请号 + 13 个基础字段 + 3 个发文字段**（条件采集），支持断点续传。**本次采集成功率 99.96%（9418/9422）**。

## 🚀 快速开始（30 秒）

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
| **[docs/decision-log.md](docs/decision-log.md)** | 技术决策及原因（MITM 为什么？条件采集为什么？） | 🟠 理解设计意图、讨论优化 |
| **[docs/ai-context.md](docs/ai-context.md)** | 当前目标、约束、风险、下一步（给 AI 的 1 屏纲要） | 🤖 AI/Harness 协作 |
| **[docs/worklog.md](docs/worklog.md)** | 每次改动记录（日期、目标、结果、下一步） | 📝 了解项目演进 |

---

## 📊 采集数据

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
MITM 代理 (127.0.0.1:8080) 拦截 API 响应
    ↓
detection_logger.py 记录 JSON
    ↓
patents_data.xlsx (Excel 报表) + detection_log.json (完整日志)
```

---

## ⚙️ 系统特点

✅ **稳定** - MITM + PyAutoGUI，规避反爬虫检测  
✅ **完整** - 申请号 + 13 个专利字段 + 3 个发文字段  
✅ **可靠** - 断点续传，浏览器启动自动重试，支持手动重试和补采  
✅ **可扩展** - 模块化设计，易于维护和修改  
✅ **透明** - 详细文档，决策记录，协作日志  

---

## 🔧 核心文件职责

| 文件 | 职责 | 状态 |
|------|------|------|
| `main_automation.py` | 主流程：浏览器控制、申请号循环、数据采集 | ✅ 活跃 |
| `detection_logger.py` | 日志记录：JSON 序列化、Excel 导出 | ✅ 活跃 |
| `patent_mitm_scraper.py` | MITM 插件：API 拦截、字段解析 | ✅ 活跃 |
| `collect_fwxx.py` | 补采脚本：补采漏掉的发文信息 | ✅ 活跃 |
| `retry_failed_applications.py` | 重试脚本：重新采集失败的申请号 | ✅ 活跃 |

**详见**: [docs/architecture.md](docs/architecture.md#核心文件职责)

---

## 📍 数据位置

```
data/
├── search_list.txt           # 输入：申请号列表
├── config.json               # 配置：鼠标坐标
├── patent_cache.json         # 临时：MITM 缓存（可删除）
└── results/
    ├── detection_log.json    # ⭐ 输出：采集日志（JSON）
    └── patents_data.xlsx     # ⭐ 输出：最终报表（Excel）
```

---

## ⚠️ 已知问题 & 优化计划

| 问题 | 优先级 | 状态 | 计划 |
|------|--------|------|------|
| **falvzt 不可用（全为 `--`）** | P0 | ✅ 已确认 | 以 anjianywzt 为准（实测 9422 条采集数据验证） |
| **JSON 文件 RMW 非原子写入** | P0 | 🔴 未修复 | 改 JSONL + 文件锁（2 周） |
| **代码重复（浏览器/输入逻辑）** | P1 | 🟡 未改 | 模块化提取（3 周） |
| **路径策略不统一** | P1 | 🟡 未改 | 统一 settings.py（1 周） |
| **文档草稿审核** | P1 | 🟡 进行中 | 代码事实验证中，待用户补充业务信息 |

**详见**: [docs/ai-context.md](docs/ai-context.md#当前风险--处理计划)

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
python retry_failed_applications.py
```

### 补采发文信息
```bash
python collect_fwxx.py
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
| **本次采集成功率** | 99.96%（9418 成功 / 9422 总数，status_code=200） |
| **发文采集覆盖** | 98.50%（1382 有发文 / 1403 驳回复审） |
| **数据架构** | 申请号 + 13 个基础字段 + 3 个发文字段（条件采集） |
| **项目文档** | 7 份（project-brief, architecture, domain-rules, runbook, decision-log, ai-context, worklog） |
| **核心代码文件** | 5 个（main_automation, detection_logger, patent_mitm_scraper, collect_fwxx, retry_failed） |

---

**最后更新**: 2026-05-10  
**项目阶段**: 阶段 2 - 工程化优化  
**下次审查**: 2026-05-17  
**维护人**: @minxiaochen
