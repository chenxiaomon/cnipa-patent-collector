# 架构设计文档

## 采集模式总览

| 模式 | 入口 | 人工参与 | 主要数据流 | 输出 |
|------|------|----------|------------|------|
| 模式 A：按申请号自动采集 | `main_automation.py` | 登录/验证码、坐标配置 | 申请号列表 → CNIPA 查询 → MITM 缓存 → SQLite | `patents.db`, `patents_data.xlsx` |
| 模式 B：Phase 0 手动采集 | `start_browser_for_phase0.py` + `import_from_cache.py` | 手动按申请人搜索和翻页 | 手动浏览 → `patent_cache.json` → SQLite upsert | `patents.db` 新增记录 |
| 模式 C：公开查询采集 | `launch_browser_with_proxy.py` / `auto_paginate.py` + `export_public_search.py` | 手动输入查询条件，可手动或半自动翻页 | publicSearch 响应 → `raw_responses/` → 导出文件 | `public_search_results.xlsx/json` |

## 模式 A：按申请号自动采集链路

```
┌─────────────────────────────────────────────────────────────────┐
│ 启动                                                              │
│ USE_MITM_PROXY=true python main_automation.py                    │
└────────────────────┬────────────────────────────────────────────┘
                     │
        ┌────────────▼──────────────┐
        │ 1. 读取申请号列表          │
        │ data/search_list.txt      │
        └────────────┬──────────────┘
                     │
        ┌────────────▼──────────────────────┐
        │ 2. 初始化浏览器 + 代理             │
        │ undetected_chromedriver          │
        │ MITM Proxy @ 127.0.0.1:8082      │
        └────────────┬──────────────────────┘
                     │
        ┌────────────▼──────────────────────────────────────────┐
        │ 3. 遍历申请号 + 查询 CNIPA                            │
        │ a) PyAutoGUI 点击搜索框                               │
        │ b) 输入申请号                                          │
        │ c) 等待 MITM 拦截 API 响应（超时 8s）                │
        │ d) patent_mitm_scraper.py 解析响应                   │
        │ e) 缓存到 patent_cache.json                           │
        └────────────┬──────────────────────────────────────────┘
                     │
        ┌────────────▼──────────────────────────────────────────┐
        │ 4. 记录基础数据到运行库                                │
        │ detection_logger.add_record()                          │
        │ → PatentsDB → patents.db                               │
        └────────────┬──────────────────────────────────────────┘
                     │
        ┌────────────▼──────────────────────────────────────────┐
        │ 5. 导出最终报表                                        │
        │ detection_logger.export_to_excel()                     │
        │ → patents_data.xlsx                                    │
        └────────────┬──────────────────────────────────────────┘
                     │
        ┌────────────▼──────────────────────────────────────────┐
        │ 6a（可选、独立）补采发文信息                            │
        │ python collect_fwxx.py                                 │
        │ 筛选: anjianywzt == '驳回等复审请求' && fwxx_list==null │
        │ → update_fields() 更新 patents.db                    │
        └────────────────────────────────────────────────────────┘
        ┌────────────────────────────────────────────────────────┐
        │ 6b（可选、独立）补采费用信息                            │
        │ python collect_fees.py                                 │
        │ 筛选: 驳回案件 && 任一必需费用列表为 null               │
        │ → update_fields() 更新 patents.db                    │
        └────────────────────────────────────────────────────────┘
```

6a 和 6b 分别生成计划、判断完成并写入各自字段，不要求按固定先后顺序执行。

## 模式 B：Phase 0 手动采集链路

适用于“先按申请人/关键词手动找一批专利，再导入运行库”的场景。

```
python start_mitm_proxy.py
    ↓
python start_browser_for_phase0.py
    ↓
用户手动登录 CNIPA、输入申请人、点击查询、逐页浏览
    ↓
patent_mitm_scraper.py 拦截 API 响应
    ↓
data/patent_cache.json
    ↓
python import_from_cache.py
    ↓
patents.db（按申请号 upsert）
```

注意：Phase 0 导入的是基础字段，不负责发文或费用信息；后续分别由 `collect_fwxx.py` 和 `collect_fees.py` 处理。

## 模式 C：公开查询手动/半自动采集链路

适用于使用 CNIPA `publicSearch` 页面按公开查询条件采集搜索结果。它与主日志链路相对独立，主要产出公开查询结果文件。

```
python start_mitm_public_search.py
    ↓
python launch_browser_with_proxy.py
    ↓
用户手动输入查询条件并点击查询
    ↓
手动点击下一页，或运行 auto_paginate.py 半自动翻页
    ↓
mitm_addon_public_search.py 保存原始响应
    ↓
data/raw_responses/ + data/raw_searches/
    ↓
python export_public_search.py
    ↓
data/results/public_search_results.xlsx/json
```

## 核心文件职责

| 文件 | 职责 | 状态 |
|------|------|------|
| **main_automation.py** | 主流程控制，浏览器操作，申请号循环遍历 | 活跃 |
| **db_manager.py** | SQLite 运行时唯一真相源、聚合查询、增量导入导出 | 活跃 |
| **detection_logger.py** | 通过 PatentsDB 写入，导出 Excel/JSON/JSONL 备份 | 活跃 |
| **patent_mitm_scraper.py** | MITM 代理插件，拦截 API 响应，JSON 解析 | 活跃 |
| **desktop_collection_lock.py** | 发文与费用详情采集共用的跨进程桌面文件锁 | 活跃 |
| **collect_fwxx.py** | 补采脚本，用于补采漏掉的发文信息 | 活跃 |
| **collect_fees.py** | 独立费用补采脚本，用于补采应缴、滞纳、已缴和收据发文信息 | 活跃 |
| **main_automation.py --update-list** | 失败重试/强制更新指定申请号列表 | 活跃 |
| **export_public_search.py** | 公开查询导出辅助 | 可选 |
| **start_browser_for_phase0.py** | Phase 0 手动采集：启动带代理浏览器，用户手动搜索/翻页 | 可用 |
| **import_from_cache.py** | Phase 0 手动采集：将 `patent_cache.json` 导入 SQLite | 可用 |
| **start_mitm_public_search.py** | 公开查询采集：启动 publicSearch 专用 MITM 插件 | 可用 |
| **launch_browser_with_proxy.py** | 公开查询采集：启动带代理 publicSearch 浏览器 | 可用 |
| **auto_paginate.py** | 公开查询采集：自动/半自动翻页 | 可用 |
| **mitm_addon_public_search.py** | 公开查询采集：拦截 publicSearch API 并保存原始响应 | 可用 |

## 数据文件结构

### 输入

```
data/
├── search_list.txt          # 申请号列表（一行一个）
├── config.json              # 搜索页鼠标坐标配置
└── config_fwxx.json         # 详情页、发文和费用鼠标坐标配置（两个补采任务共用）
```

### 输出与缓存

```
data/
├── patents.db              # 运行时唯一真相源（本机文件，不进 Git）
├── machine_role.txt        # 本机角色 master/replica（不进 Git）
├── patent_cache.json        # MITM 拦截的原始 API 响应（临时，可删除）
├── patent_fwxx_cache.json   # 发文信息缓存（临时，可删除）
├── patent_fee_cache.json    # 费用信息缓存（临时，可删除）
├── raw_responses/           # 公开查询原始响应
├── raw_searches/            # 公开查询 JSONL 记录
├── results/
│   ├── detection_log.jsonl  # Git 备份与跨机传输载体（运行时禁止直接读取）
│   ├── detection_log.json   # 兼容导出/备份 JSON
│   ├── patents_data.xlsx    # ⭐ 最终报表（Excel）
│   └── public_search_results.xlsx/json
└── detection_log.json       # 兼容路径（某些脚本可能使用）
```

## 数据流详解

### 1. 基础字段采集（申请号 + 13 个专利字段）

**来源**: MITM 拦截 `https://cpquery.cponline.cnipa.gov.cn/` 的 API 响应

**字段列表**:
- 申请号
- `famingzlsqgbg` - 发明公布号
- `shouquanggh` - 授权公告号
- `zhuanlimc` - 专利名称
- `shenqingrxm` - 申请人
- `zhuanlilx` - 专利类型
- `shenqingr` - 申请日
- `gongkaiggh` - 公开公告号
- **`falvzt`** - 法律状态 ⚠️ (实测数据：全为 `--`，不可用，改为 anjianywzt)
- `gongkaiggr` - 公开公告日
- `shouquanggr` - 授权公告日
- `zhufenlh` - 主分类号
- `anjianbh` - 案件编号
- **`anjianywzt`** - 案件业务状态 ⭐ (采集发文判定条件，以此为准)

### 2. 发文信息采集（条件触发）

**触发条件**: `anjianywzt == '驳回等复审请求'`（以 anjianywzt 为准）

**采集方式**: 点击"发文信息"标签，MITM 拦截对应 API

**字段列表** (3 字段):
- `fwxx_list` - 完整发文列表（结构化）
- `bhsjtzs_xiazaisj` - 驳回决定的下载时间
- `bhsjtzs_data` - 驳回决定详细信息

**说明**: collect_fwxx.py 使用 `anjianywzt == '驳回等复审请求'` 作为筛选条件（已验证为准，falvzt 全为 `--` 不可用）

**待采集/完成口径**: 自动计划只看 `fwxx_list`；`NULL` 为待采集，非 `NULL`（包括 `[]`）为完成。费用字段不会影响发文计划。

### 3. 费用信息采集（独立任务）

**入口**: `collect_fees.py`

**采集方式**: 打开详情页后只点击“费用信息”，MITM 拦截 `/api/view/gn/fyxx`；不会点击或解析发文信息。

**字段列表** (4 个结构化列表 + 采集时间):
- `payable_fee_records` - 应缴费信息，对应 `data.yingjiaofei.svYingjfList`
- `late_fee_schedule_records` - 应缴滞纳金时间阶梯，对应 `data.zhinajin.svZnjList`
- `paid_fee_records` - 已缴费信息，对应 `data.yijiaofei.svYijfList`
- `fee_receipt_dispatch_records` - 收据发文信息，对应 `data.shoujufawen.svSjfwList`
- `fee_snapshot_at` - 应缴费栏目成功采集时的 UTC 时间

**待采集/完成口径**: 自动计划限定 `anjianywzt == '驳回等复审请求'`。`payable_fee_records`、`paid_fee_records`、`fee_receipt_dispatch_records` 是三个必需栏目，任一为 `NULL` 即待采集；三者均非 `NULL` 即完成，`[]` 也算接口明确返回并完成。正常缴费案件可能不返回 `late_fee_schedule_records`，因此该字段不作为完成条件。

费用写入只更新费用字段和 `fee_snapshot_at`，不刷新基础案件状态使用的通用 `timestamp`。票据代码、票据号码和收据号按字符串保存。

Excel 将原始四表与分析表分开：应缴表中的未来年度费用不会被算作当前到期；滞纳金表的多行是互斥时间档，只选择分析日所在的一档，禁止求和。

### 4. 桌面浏览器互斥

`collect_fwxx.py` 和 `collect_fees.py` 虽然数据职责独立，但都通过 PyAutoGUI 控制同一桌面浏览器，并共用详情页坐标与当前申请号标记。两个入口在整个采集周期内通过 `desktop_collection_lock.py` 获取同一个非阻塞操作系统文件锁；因此无论由 Dashboard 还是命令行启动，第二个发文/费用进程都会在控制浏览器前收到桌面占用错误，进程退出时自动释放锁。

Dashboard 仍在任务层对处于 `running` 或 `stopping` 的桌面任务做额外冲突检查。跨进程文件锁只约束发文与费用详情采集；直接运行其他未接入该锁的 CLI 桌面脚本时，仍须避免同时控制桌面。

## 依赖关系

```
main_automation.py
├── detection_logger.py          (日志记录)
├── patent_mitm_scraper.py       (MITM 拦截解析)
└── PyAutoGUI                    (物理操作)

collect_fwxx.py
├── detection_logger.py
└── undetected_chromedriver      (浏览器控制)

collect_fees.py
├── detection_logger.py
└── undetected_chromedriver      (浏览器控制)

main_automation.py --update-list
├── detection_logger.py
└── data/retry_failed.txt / 自定义申请号列表

Phase 0 手动采集
├── start_mitm_proxy.py
├── start_browser_for_phase0.py
├── import_from_cache.py
├── patent_mitm_scraper.py
└── detection_logger.py

公开查询采集
├── start_mitm_public_search.py
├── launch_browser_with_proxy.py
├── auto_paginate.py
├── mitm_addon_public_search.py
└── export_public_search.py
```

## 技术栈

| 组件 | 用途 | 版本 |
|------|------|------|
| **Python** | 核心运行环境 | 3.8+ |
| **undetected-chromedriver** | 无检测浏览器驱动 | latest |
| **Selenium** | 浏览器交互 | 4.x |
| **PyAutoGUI** | 物理鼠标键盘操作 | 0.9.53+ |
| **mitmproxy** | HTTP 拦截代理 | 9.x+ |
| **pandas** | 数据处理 | 1.3+ |
| **openpyxl** | Excel 导出 | 3.x |

---

*最后更新*: 2026-07-27
*架构审查*: 已验证
