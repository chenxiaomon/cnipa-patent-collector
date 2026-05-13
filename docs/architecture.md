# 架构设计文档

## 采集模式总览

| 模式 | 入口 | 人工参与 | 主要数据流 | 输出 |
|------|------|----------|------------|------|
| 模式 A：按申请号自动采集 | `main_automation.py` | 登录/验证码、坐标配置 | 申请号列表 → CNIPA 查询 → MITM 缓存 → 主日志 | `detection_log.jsonl`, `patents_data.xlsx` |
| 模式 B：Phase 0 手动采集 | `start_browser_for_phase0.py` + `import_from_cache.py` | 手动按申请人搜索和翻页 | 手动浏览 → `patent_cache.json` → 导入主日志 | 主日志新增记录 |
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
        │ 4. 记录基础数据到日志                                  │
        │ detection_logger.add_record()                          │
        │ → detection_log.jsonl                                  │
        └────────────┬──────────────────────────────────────────┘
                     │
        ┌────────────▼──────────────────────────────────────────┐
        │ 5. 导出最终报表                                        │
        │ detection_logger.export_to_excel()                     │
        │ → patents_data.xlsx                                    │
        └────────────┬──────────────────────────────────────────┘
                     │
        ┌────────────▼──────────────────────────────────────────┐
        │ 6（可选）补采发文信息                                  │
        │ python collect_fwxx.py                                 │
        │ 筛选: anjianywzt == '驳回等复审请求' && fwxx_list==null │
        │ → 追加发文信息到 detection_log.jsonl                 │
        └────────────────────────────────────────────────────────┘
```

## 模式 B：Phase 0 手动采集链路

适用于“先按申请人/关键词手动找一批专利，再导入主日志”的场景。

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
detection_log.jsonl（去重后追加）
```

注意：Phase 0 导入的是基础字段，不负责发文信息；如需发文，后续仍由 `collect_fwxx.py` 处理。

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
| **detection_logger.py** | 日志记录，JSONL 追加写入，导出 Excel/JSON | 活跃 |
| **patent_mitm_scraper.py** | MITM 代理插件，拦截 API 响应，JSON 解析 | 活跃 |
| **collect_fwxx.py** | 补采脚本，用于补采漏掉的发文信息 | 活跃 |
| **patent_data_cache.py** | 内存缓存模块（已弃用，改用文件系统） | 已弃用 |
| **main_automation.py --update-list** | 失败重试/强制更新指定申请号列表 | 活跃 |
| **export_public_search.py** | 公开查询导出辅助 | 可选 |
| **start_browser_for_phase0.py** | Phase 0 手动采集：启动带代理浏览器，用户手动搜索/翻页 | 可用 |
| **import_from_cache.py** | Phase 0 手动采集：将 `patent_cache.json` 导入主日志 | 可用 |
| **start_mitm_public_search.py** | 公开查询采集：启动 publicSearch 专用 MITM 插件 | 可用 |
| **launch_browser_with_proxy.py** | 公开查询采集：启动带代理 publicSearch 浏览器 | 可用 |
| **auto_paginate.py** | 公开查询采集：自动/半自动翻页 | 可用 |
| **mitm_addon_public_search.py** | 公开查询采集：拦截 publicSearch API 并保存原始响应 | 可用 |

## 数据文件结构

### 输入

```
data/
├── search_list.txt          # 申请号列表（一行一个）
└── config.json              # 鼠标坐标配置（由 PyAutoGUI 坐标记录工具生成）
```

### 输出与缓存

```
data/
├── patent_cache.json        # MITM 拦截的原始 API 响应（临时，可删除）
├── patent_fwxx_cache.json   # 发文信息缓存（临时，可删除）
├── raw_responses/           # 公开查询原始响应
├── raw_searches/            # 公开查询 JSONL 记录
├── results/
│   ├── detection_log.jsonl  # ⭐ 主日志文件（JSONL 追加写入）
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

## 依赖关系

```
main_automation.py
├── detection_logger.py          (日志记录)
├── patent_mitm_scraper.py       (MITM 拦截解析)
└── PyAutoGUI                    (物理操作)

collect_fwxx.py
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

*最后更新*: 2026-05-10  
*架构审查*: 已验证
