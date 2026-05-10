# 业务规则与字段口径

## 字段定义与规范化

### 核心判定字段

#### 1. `falvzt` (法律状态) - ⭐ 关键

**定义**: 专利的当前法律状态，由 CNIPA 官方定义

**可能值** (常见):
- `授权公告` - 已授权
- `驳回等复审请求` - ⭐ **发文信息采集触发条件**
- `失效` - 已失效
- `审查中` - 审查进行中
- 其他法律状态...

**采集规则**:
```python
# 当前代码（待改）
if falvzt == '驳回等复审请求':  # ⚠️ 实测数据表明 falvzt 全为 '--'，不可用
    fwxx_data = navigate_to_fwxx(driver, application_no)

# 建议改为
if anjianywzt == '驳回等复审请求':  # ✅ 实测数据有真实分布
    fwxx_data = navigate_to_fwxx(driver, application_no)
```

**备注**: 
- main_automation.py line 455 当前仍为 falvzt（待改）
- 实测数据：falvzt 在 9418 条成功记录中全为 `--`，anjianywzt 有真实分布
- 来自 MITM 拦截的 API 响应（基础字段）

---

#### 2. `anjianywzt` (案件业务状态) - ✅ 已确认为准

**定义**: 案件在专利局业务流程中的当前状态

**当前代码行为**:
- `main_automation.py: line 455` - 采集发文时使用 `falvzt == '驳回等复审请求'`（⚠️ 待改）
- `collect_fwxx.py: line 232` - 补采目标筛选使用 `anjianywzt == '驳回等复审请求'`（✅ 正确）

**业务确认**:
- ✅ `falvzt` 和 `anjianywzt` 一般相同
- ✅ **采集发文的判定条件应以 `anjianywzt` 为准**

**改进方向**:
1. 建议将主流程 (`main_automation.py: line 455`) 的判定条件也改为 `anjianywzt`（与补采脚本一致）
2. 这样可以避免主流程和补采脚本的逻辑分歧

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

### 发文信息（3 字段 - 由补采脚本负责）

**触发条件**:
- 主流程不负责发文采集
- 补采脚本筛选：`anjianywzt == '驳回等复审请求'` 且 `fwxx_list == null`

**采集流程** (collect_fwxx.py):
1. 加载 detection_log.json，筛选未采发文的驳回复审案件
2. 点击"发文信息"标签
3. MITM 拦截对应 API 响应
4. 解析 `fwxx_list`、`bhsjtzs_xiazaisj`、`bhsjtzs_data`
5. 更新 detection_log.json 中对应申请号的发文字段

**执行方式**:
```bash
python collect_fwxx.py  # 主流程完成后运行（可选）
```

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

### 重试策略

**main_automation.py**:
- 浏览器创建: 最多 3 次重试，指数退避（1s, 2s, 4s）
- MITM 查询: 单次超时 8s，不重试（由 retry_failed_applications.py 处理）

**retry_failed_applications.py**:
- 从 detection_log.json 中筛选 `status_code=0` 的记录
- 批量重新采集

**collect_fwxx.py**:
- 补采 `fwxx_list=null` 且 `anjianywzt=='驳回等复审请求'` 的记录

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

**原因**: `falvzt` vs `anjianywzt` 口径不统一

**解决**:
1. 确认两个字段的业务含义
2. 统一为单一条件（推荐用 `falvzt`）
3. 更新 collect_fwxx.py

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

*更新时间*: 2026-05-10  
*待确认项*: falvzt vs anjianywzt 的定义与关系
