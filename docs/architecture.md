# 架构设计文档

## 采集链路

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
        │ MITM Proxy @ 127.0.0.1:8080      │
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
        │ → detection_log.json                                   │
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
        │ → 追加发文信息到 detection_log.json                  │
        └────────────────────────────────────────────────────────┘
```

## 核心文件职责

| 文件 | 职责 | 状态 |
|------|------|------|
| **main_automation.py** | 主流程控制，浏览器操作，申请号循环遍历 | 活跃 |
| **detection_logger.py** | 日志记录，数据序列化（JSON），导出（Excel） | 活跃 |
| **patent_mitm_scraper.py** | MITM 代理插件，拦截 API 响应，JSON 解析 | 活跃 |
| **collect_fwxx.py** | 补采脚本，用于补采漏掉的发文信息 | 活跃 |
| **patent_data_cache.py** | 内存缓存模块（已弃用，改用文件系统） | 已弃用 |
| **retry_failed_applications.py** | 失败重试脚本 | 活跃 |
| **export_public_search.py** | 公开查询导出辅助 | 可选 |

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
├── results/
│   ├── detection_log.json   # ⭐ 主日志文件（结构化记录）
│   └── patents_data.xlsx    # ⭐ 最终报表（Excel）
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

retry_failed_applications.py
├── detection_logger.py
└── main_automation.py (import 某些函数)
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
