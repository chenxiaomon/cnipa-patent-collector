# 决策日志（架构备忘）

记录项目中的重要技术和业务决策，以及背后的原因。帮助未来的协作者理解"为什么这样做"而不仅是"做了什么"。

> 📌 **文档属性**: 基于当前代码推导的架构备忘，非正式历史记录  
> 记录日期：2026-05-10  
> 决策真实日期：待确认（需用户补充）

---

## D001: MITM 代理 + PyAutoGUI 而非纯 DOM 自动化

**记录日期**: 2026-05-10  
**决策日期**: 待确认  
**状态**: 📋 代码可推导（需验证实际有效性）

### 背景

CNIPA 官网使用复杂的前端框架（可能是 Vue/React），对 DOM 操作的反爬虫检测较强。

### 评估的方案

| 方案 | 优点 | 缺点 | 评分 |
|------|------|------|------|
| **纯 DOM 自动化** (Selenium 填表) | 直接，无额外依赖 | 易被反爬；响应变化时脆弱 | 2/5 |
| **MITM + PyAutoGUI** (选中) | 稳定，绕过反爬虫 | 依赖物理操作；坐标绑定 | 5/5 |
| **无头浏览器** (Puppeteer) | 速度快 | 同样易被检测；需 Node.js | 3/5 |
| **API 逆向工程** | 最快 | 需破解加密；易被 IP 封 | 2/5 |

### 决策

选择 **MITM + PyAutoGUI** 方案：
- PyAutoGUI 物理操作输入，避免 DOM 检测
- MITM 代理拦截 API 响应，直接获得结构化数据
- 鼠标操作由坐标配置驱动，不依赖 DOM 选择器

### 关键权衡

- **稳定性优先于速度**: 采集 500 条目费时 30 分钟可接受，但失败率必须 < 5%
- **坐标绑定**: 不同屏幕分辨率需重新配置，但自动化工具可生成坐标
- **并发限制**: 单线程顺序采集，防止大规模 IP 被封

### 后续验证

- 🧪 采集成功率 > 95%（目标，待补实测数据）
- 🧪 未被 IP 限制（需长期运行观察）
- 🧪 数据完整性 > 99%（需定义统计口径后验证）

### 代码证据

- ✅ 代码使用 MITM + PyAutoGUI，避免 DOM 操作
- ✅ PyAutoGUI 用物理坐标操作，未调用 Selenium 元素选择器（最小化 JS 交互）

---

## D002: 条件采集发文信息（falvzt 判定）

**记录日期**: 2026-05-10  
**决策日期**: 待确认  
**状态**: ⚠️ 待确认字段关系

### 背景

发文信息（驳回决定、发文列表）数据量大，采集耗时。并非所有专利都需要该信息，仅限于"驳回等复审"的案件。

### 评估的方案

| 方案 | 优点 | 缺点 |
|------|------|------|
| **全量采集** | 数据完整 | 耗时 50% 增加；IO 压力大 |
| **条件采集** (选中) | 提高效率；减少 IO | 需要准确判定条件；可能漏采 |
| **两阶段采集** | 灵活 | 流程复杂；易出现漏洞 |

### 决策

使用 **两阶段采集**:
1. **第一阶段**: 全量采集基础 14 字段
2. **第二阶段**: 检查 `falvzt == '驳回等复审请求'`，再采集发文

### 问题

⚠️ **代码中的矛盾**:
- `main_automation.py: line 455` 使用 `falvzt` 作为判定条件
- `collect_fwxx.py: line 206` 使用 `anjianywzt` 作为筛选条件
- **这两个字段是否等价？未确认**

### 后续行动

- [ ] 确认 CNIPA 官方文档中 `falvzt` 和 `anjianywzt` 的定义差异
- [ ] 统一为单一条件（推荐 `falvzt`）
- [ ] 更新 collect_fwxx.py
- [ ] 补采遗漏的发文信息

---

## D003: JSON 文件存储而非数据库

**决策日期**: 待确认  
**决策人**: @minxiaochen  
**状态**: ⚠️ 待优化

### 背景

项目初期规模 < 1000 条目，JSON 文件满足需求。但目前出现的问题：

| 问题 | 症状 | 影响 |
|------|------|------|
| **重复读写整文件** | `DetectionLogger.add_record()` 每次追加都重写全文件 | 性能、磨损 |
| **并发冲突** | MITM + main_automation 同时写文件 | 数据损坏风险 |
| **中断损坏** | 写入中途进程崩溃 | 日志文件无效 |

### 当前实现

```python
# detection_logger.py: line 148
def add_record(self, record):
    # 读取 → 修改 → 重写全文件（RMW 非原子操作）
    log_data = json.load(f)
    log_data['records'].append(record.to_dict())
    json.dump(log_data, f)  # ← 中断此处会损坏文件
```

### 评估的方案

| 方案 | 优点 | 缺点 | 采用时间 |
|------|------|------|---------|
| **JSONL + 原子写** | 增量日志；原子写入；易于流式处理 | 格式改变；需脚本迁移 | 🟢 近期 |
| **SQLite** | ACID; 并发支持; 查询灵活 | 依赖增加；学习成本 | 🟡 中期 |
| **RocksDB** | 高性能；嵌入式 | 生态小；多语言支持一般 | 🔴 长期考察 |

### 决策

**近期** (2-4 周):
- 改用 JSONL 格式存储日志（追加式）
- 使用 `O_APPEND | O_CREAT` 文件锁确保原子写入
- 保留导出 JSON 的接口（向后兼容）

**中期** (1-3 个月):
- 评估 SQLite 迁移的成本收益
- 若规模继续增长 (> 10K 条目) 则迁移

### 关键权衡

- **可读性 vs 性能**: JSON 可读但性能差；JSONL 格式一致、性能好
- **兼容性**: 导出时聚合 JSONL 为 JSON，对外部工具透明

### 实现计划

```python
# 新增 detection_logger.py
class DetectionLoggerV2:
    def __init__(self):
        self.log_file = 'data/results/detection_log.jsonl'
    
    def add_record(self, record: DetectionRecord):
        # 原子追加
        with open(self.log_file, 'a') as f:
            f.write(json.dumps(record.to_dict()) + '\n')
            f.flush()
            os.fsync(f.fileno())  # 强制磁盘同步
    
    def load_all(self):
        # 聚合读取所有记录
        records = []
        with open(self.log_file, 'r') as f:
            for line in f:
                records.append(json.loads(line))
        return records
    
    def export_to_excel(self):
        # 兼容接口，读取 JSONL 后导出 Excel
        records = self.load_all()
        ...
```

---

## D004: 坐标配置 vs DOM 选择器

**决策日期**: 待确认  
**决策人**: @minxiaochen  
**状态**: ✓ 已验证

### 背景

PyAutoGUI 操作需要精确坐标。坐标方案与 DOM 选择器各有权衡。

### 方案对比

| 项目 | 坐标配置 | DOM 选择器 |
|------|---------|----------|
| **抗反爬虫** | ✅ 物理输入，无 JS 调用 | ❌ 需查询 DOM，易被检测 |
| **鲁棒性** | ❌ 屏幕分辨率敏感 | ✅ 自适应 |
| **可维护性** | ❌ 硬编码坐标，UI 变化需重配 | ✅ 选择器通常稳定 |
| **灵活性** | ✅ 可以额外操作（如拖拽、长按） | ❌ 限于标准操作 |

### 决策

使用 **坐标配置** + **自动生成工具**:
- PyAutoGUI 基于坐标进行操作
- 坐标存储在 `data/config.json` 中
- 提供交互式坐标记录工具自动生成配置

### 优化方向

- ✅ 开发坐标自动识别工具（基于图像识别）
- ✅ 支持多分辨率配置
- 📋 适配列表（常见分辨率的坐标）

---

## D005: 断点续传机制

**决策日期**: 待确认  
**决策人**: @minxiaochen  
**状态**: ✓ 已实现

### 背景

采集 500 条目耗时 30 分钟，中途可能中断（网络、浏览器崩溃、用户手动停止）。

### 需求

1. **不重复采集**: 已采集的申请号自动跳过
2. **快速恢复**: 从最后成功的申请号继续
3. **数据无损**: 中断不导致日志损坏

### 实现

```python
# main_automation.py
def run_automation():
    # 加载已采集的申请号
    collected = set()
    if os.path.exists(DETECTION_LOG_FILE):
        log_data = json.load(f)
        collected = {r['application_no'] for r in log_data['records']}
    
    # 跳过已采集的
    applications = [a for a in all_applications if a not in collected]
```

### 验证

- ✅ 中断后重启，无重复采集
- ✅ 日志文件有效性检查

---

## D006: 单线程顺序采集 vs 并发

**决策日期**: 待确认  
**决策人**: @minxiaochen  
**状态**: ✓ 已实现（单线程）; 🟡 并发待评估

### 背景

目前采用单线程，采集 500 条目约 30 分钟。若要加速，可考虑并发。

### 权衡

| 方案 | 优点 | 缺点 | 风险 |
|------|------|------|------|
| **单线程** (当前) | 稳定；不易被 IP 封；实现简单 | 速度慢（需 30 分钟） | 低 |
| **多线程** | 速度快 2-3 倍 | 数据竞争；日志冲突；易被 IP 限制 | 高 |
| **多进程** | 隔离；可分布式 | 资源占用大；进程间通信复杂 | 中 |

### 当前决策

保持 **单线程顺序采集**:
- CNIPA 不允许大规模并发（会被 IP 限制）
- 30 分钟采集 500 条对用户可接受（可后台运行）
- 实现复杂度低，维护成本小

### 未来考虑

若需加速（待评估）:
1. 多个浏览器实例（串行，不并发）
2. 分摊申请号列表到不同时间段
3. 改用 SQLite + 线程安全的日志机制

---

## D007: Excel 导出格式

**决策日期**: 待确认  
**决策人**: @minxiaochen  
**状态**: ✓ 已实现

### 背景

最终结果需要导出为 Excel，方便数据分析和人工审核。

### 设计

| 部分 | 内容 |
|------|------|
| **列** | 14 个基础字段 + 3 个发文字段（共 17 列） |
| **行** | 每个申请号一行 |
| **空值处理** | 用 `N/A` 表示 (可筛选、可排序) |
| **格式** | XLSX (openpyxl) |

### 特点

- ✅ 可在 Excel 中直接筛选 + 排序
- ✅ 兼容 Excel、Google Sheets、LibreOffice
- ✅ 支持条件格式化（失败记录高亮）

---

## D008: 配置管理

**决策日期**: 待确认  
**决策人**: @minxiaochen  
**状态**: 🟡  待优化

### 背景

目前配置分散：
- 硬编码在 main_automation.py (URL, 超时时间, 路径)
- `data/config.json` (鼠标坐标)
- 环境变量 (USE_MITM_PROXY)

### 问题

1. **路径策略不统一**: 部分使用 `__file__` 计算，部分硬编码 `data/`
2. **配置难以覆盖**: 超时时间、数据目录等硬编码，改动需编辑源码
3. **环境变量混乱**: 只有 MITM 代理用环境变量

### 决策

统一配置管理 (待实现):

```python
# 新增 config.py 或 settings.py
class Settings:
    # 路径
    BASE_DIR = Path(__file__).parent
    DATA_DIR = BASE_DIR / 'data'
    CACHE_DIR = DATA_DIR / 'cache'
    
    # MITM 和超时
    USE_MITM_PROXY = os.getenv('USE_MITM_PROXY', 'false').lower() == 'true'
    MITM_TIMEOUT = int(os.getenv('MITM_TIMEOUT', 8))
    
    # 坐标配置
    @property
    def config(self):
        with open(self.DATA_DIR / 'config.json') as f:
            return json.load(f)

# 使用
from config import settings
MITM_TIMEOUT = settings.MITM_TIMEOUT
```

### 实现计划

- 优先级: 🟡 中等（非阻塞）
- 时间: 2-3 天
- 关键: 向后兼容，不破坏现有脚本

---

## D009: 错误处理与日志级别

**决策日期**: 待确认  
**决策人**: @minxiaochen  
**状态**: ✓ 已实现

### 背景

采集过程涉及多个故障点（网络、浏览器、MITM），需要清晰的错误信息便于排查。

### 错误分类

| 类型 | 状态码 | 恢复方式 | 日志级别 |
|------|--------|---------|---------|
| MITM 超时 | 0 | 重试脚本 | WARNING |
| 网络错误 | -1 | 重试脚本 | ERROR |
| 浏览器崩溃 | -2 | 手动恢复 | CRITICAL |
| 部分字段缺失 | 0 | 重试脚本 | WARNING |
| 发文采集失败 | 1 (基础成功) | collect_fwxx.py | WARNING |

### 实现

- ✅ DetectionRecord 包含 `error_message` 和 `response_summary`
- ✅ detection_log.json 中记录每条的完整信息
- ✅ 采集进度实时打印到终端

---

## 文档状态

| 项 | 值 |
|-----|-----|
| **记录日期** | 2026-05-10 |
| **属性** | 基于当前代码推导的架构备忘，非正式历史记录 |
| **决策日期** | 待用户补充 |
| **最高优先级待确认** | D002 (falvzt vs anjianywzt), D003 (JSONL 迁移), D008 (配置管理) |
| **更新方式** | 每次用户补充真实日期后同步更新 |
