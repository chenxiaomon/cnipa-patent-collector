# 业务规则与字段口径

## 字段定义与规范化

### 核心判定字段

#### 1. `falvzt` (法律状态) - ⚠️ 不可用

**定义**: 专利的当前法律状态（由 CNIPA 官方定义）

**实测结论**:
- ❌ **不可用**：9418 条成功采集记录中 100% 为 `--`（空值）
- 虽然字段存在于 API 响应中，但无业务价值
- 不应用于任何业务逻辑判定

---

#### 2. `anjianywzt` (案件业务状态) - ✅ 已确认为准

**定义**: 案件在专利局业务流程中的当前状态

**特点**:
- ✅ 有真实分布：1403 条驳回复审，4859 条等待实审等
- ✅ 用于发文采集判定条件
- ✅ 主流程和补采脚本都使用此字段

**采集规则**:
```python
# 发文补采脚本
if anjianywzt == '驳回等复审请求':
    采集发文信息
```

**说明**:
- 主流程 (main_automation.py) 只采基础字段，不触发发文采集
- 发文信息完全由补采脚本负责，统一使用 anjianywzt 判定
- 当前已验证：1382/1403 (98.50%) 发文覆盖率

---

### 申请号规范化

**标准格式**: `CN[6位年份][1位类型][6位序号]`

**示例**:
- `CN201880002233` - 年份 2018，类型字段，序号 002233
- `CN202380004567` - 年份 2023

**验证规则** (建议):
```python
def normalize_application_no(app_no: str) -> str:
    """
    申请号规范化
    - 去除空格和特殊字符
    - 转换为大写
    - 验证格式（可选：添加校验码检验）
    """
    app_no = app_no.strip().upper().replace(' ', '')
    
    # 验证长度
    if len(app_no) != 14:
        raise ValueError(f"Invalid app_no format: {app_no}")
    
    # 验证前缀
    if not app_no.startswith('CN'):
        raise ValueError(f"Must start with 'CN': {app_no}")
    
    return app_no
```

**当前实现**: 
- `main_automation.py: line 49-59` 中直接读取，未做规范化
- 建议: 统一在读取时规范化，或在公共模块中实现

---

## 采集触发条件

### 基础字段（13 个字段 + anjianywzt）

**触发条件**: 对所有申请号都采集

**处理流程** (main_automation.py):
1. PyAutoGUI 输入申请号
2. MITM 拦截 API 响应
3. 解析 13 个字段 + anjianywzt
4. 记录到 detection_log.json（无论 anjianywzt 值是什么）

**失败处理**:
```python
# main_automation.py: line 469-478
if len(collected_fields) < 14 or timeout:
    status_code = 0  # 标记为失败
    record = DetectionRecord(status_code=0, ...)  # 空字段
```

---

### 发文信息（3 字段 - 由 `collect_fwxx.py` 负责）

**触发条件**:
- 主流程不负责发文采集。
- 自动补采筛选：`anjianywzt == '驳回等复审请求'` 且 `fwxx_list IS NULL`。
- `fwxx_list` 非 `NULL` 即为完成，接口明确返回的空列表 `[]` 也算完成。
- 费用字段是否缺失不影响发文待采集数量。

**采集流程** (collect_fwxx.py):
1. 从 SQLite 筛选 `fwxx_list` 尚未采集的驳回复审案件
2. 点击"发文信息"标签
3. MITM 拦截对应 API 响应
4. 解析 `fwxx_list`、`bhsjtzs_xiazaisj`、`bhsjtzs_data`
5. 只更新发文字段；批次完成后刷新 JSONL 备份和 Excel

### 费用信息（4 类明细 - 由 `collect_fees.py` 负责）

**触发条件**（2026-08-05 起改为数据集口径，见 decision-log D011）:
- 费用采集范围由用户导入的**费用数据集**（`fee_targets` 表）决定，与 `anjianywzt` 案件状态无关。
- 导入方式：`python import_fee_targets.py 名单.xlsx`（CSV/Excel 含申请号列），或 Dashboard「发文与费用」页上传；导入即整表替换。
- 数据集内、已建档（patents 有记录）、且 `payable_fee_records`、`paid_fee_records`、`fee_receipt_dispatch_records` 任一为 `NULL` 的申请号进入待采队列。
- 数据集内但主库无记录的申请号计为「未建档」，不进待采队列（否则费用结果无处落库、只会反复失败），需先跑主采集建档。
- 三个必需列表均非 `NULL` 即为完成，接口明确返回的空列表 `[]` 也算完成。
- `late_fee_schedule_records` 是可选栏目；接口不返回时保留 `NULL`，不阻止费用任务完成。

**采集流程** (`collect_fees.py`):
1. 从费用数据集筛选必需费用栏目未完整的已建档申请号（`--force` 时为整个数据集）
2. 进入详情页后只点击“费用信息”标签
3. MITM 拦截并解析 `payable_fee_records`、`late_fee_schedule_records`、`paid_fee_records`、`fee_receipt_dispatch_records`
4. 只更新费用字段和 `fee_snapshot_at`，不刷新基础案件状态的通用 `timestamp`
5. 批次完成后刷新 JSONL 备份和 Excel

**费用快照一致性**：本地写入按 `fee_snapshot_at` 的 UTC 时刻比较，保留微秒精度。新快照替换整组费用栏目，未返回的栏目恢复为 `NULL`，不能沿用旧滞纳金或旧缴费记录；同一快照允许补全栏目。旧快照或无有效时间的内容不覆盖已有的新快照。任务完成状态按数据库实际保留的必需费用栏目判断。

**待缴分析口径**:
- 只分析 CNIPA 费用状态精确为“未缴”的记录
- 应缴列表包含未来年度年费，按缴费截止日分层，不能将整张表视为当前欠费
- 滞纳金多行是同一费用在不同日期区间的互斥档位；按分析日选择唯一一档，禁止相加
- 滞纳金栏目缺失保留为 `NULL`，不伪装为成功空列表 `[]`

**执行方式**:
```bash
python collect_fwxx.py  # 只补采发文（可选）
python collect_fees.py  # 只补采费用（可选）
```

两个脚本的数据计划互相独立，但共用桌面浏览器、PyAutoGUI 坐标和当前申请号标记，因此不可并行运行。两个采集入口统一通过 `desktop_collection_lock.py` 持有跨进程文件锁；无论从 Dashboard 还是命令行启动，第二个发文/费用任务都会在控制浏览器前被自动拒绝，不再仅依赖操作者人工协调。

**覆盖率**:
- 驳回复审案件：1403 条
- 已有发文信息：1382 条（98.50%）
- 缺失：21 条（可能为网络超时、后端异常等）

---

## 失败定义与重试

### 采集失败的定义

| 场景 | 状态码 | 重试 |
|------|--------|------|
| MITM 超时（8s 未收到完整 13 字段） | 0 | ✓ (retry_failed_applications.py) |
| 网络错误（连接失败） | 0 | ✓ |
| 浏览器崩溃 | 0 | ✓ (手动恢复) |
| 部分字段缺失（<13 字段） | 0 | ✓ |
| 成功采集 | 200 | - |
| 发文采集失败 | 200 (基础字段成功) | ✓ (collect_fwxx.py) |
| 费用采集失败 | 200 (基础字段成功) | ✓ (collect_fees.py) |

### 重试策略

**main_automation.py**:
- 浏览器创建: 最多 3 次重试，指数退避（1s, 2s, 4s）
- MITM 查询: 单次超时 8s，不重试（由 retry_failed_applications.py 处理）

**retry_failed_applications.py**:
- 从 detection_log.json 中筛选 `status_code=0` 的记录
- 批量重新采集

**collect_fwxx.py**:
- 补采 `fwxx_list=null` 且 `anjianywzt=='驳回等复审请求'` 的记录

**collect_fees.py**:
- 补采必需费用列表任一为 `null` 且 `anjianywzt=='驳回等复审请求'` 的记录

---

## 数据导出规范

### JSON 格式（detection_log.json）

```json
{
  "metadata": {
    "total_records": 500,
    "successful": 475,
    "failed": 25,
    "export_time": "2026-05-10T14:30:00Z"
  },
  "records": [
    {
      "application_no": "CN201880002233",
      "status_code": 200,  // 200=成功, 0=失败
      "response_time_ms": 3400.5,
      
      // 基础字段 (13个专利字段)
      "famingzlsqgbg": "...",
      "shouquanggh": "...",
      "zhuanlimc": "一种...",
      "shenqingrxm": "申请人名字",
      "zhuanlilx": "发明专利",
      "shenqingr": "2018-01-15",
      "gongkaiggh": "...",
      "falvzt": "驳回等复审请求",
      "gongkaiggr": "...",
      "shouquanggr": "...",
      "zhufenlh": "...",
      "anjianbh": "...",
      "anjianywzt": "驳回等复审请求",
      
      // 发文信息 (3个字段) - 仅当 anjianywzt == '驳回等复审请求' 时
      "fwxx_list": [...],
      "bhsjtzs_xiazaisj": "2024-01-01",
      "bhsjtzs_data": {...}
    }
  ]
}
```

### Excel 格式

- 列: 基础字段 + 发文字段（共 17 列）
- 行: 每条申请号一行
- 空值: 用 `N/A` 表示

---

## 常见问题排查

### Q: 主流程已采但补采脚本认为需采

**已解决**:
- 实测数据确认：falvzt 全为 `--`，不可用
- anjianywzt 为准，已在代码和文档中统一
- 补采脚本正确使用 anjianywzt

### Q: 某条记录的发文信息为空（fwxx_list=null）

**可能原因**:
- MITM 超时
- 浏览器崩溃前页面未加载完
- 后端接口异常

**恢复**:
```bash
python collect_fwxx.py  # 补采脚本
```

### Q: 采集结果不完整（字段缺失）

**原因**: 可能是 MITM 拦截到了部分响应，而非完整 JSON

**检查**: 查看 data/patent_cache.json 中该申请号的记录

---

*更新时间*: 2026-07-27
*验证完成*: falvzt vs anjianywzt - 已确认 falvzt 不可用，anjianywzt 为准
