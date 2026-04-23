# vib3 - 专利数据采集系统

自动化从中国知识产权局（CNIPA）采集专利数据的系统。

## 🚀 快速开始

### 环境要求

```bash
# 安装依赖
pip install undetected-chromedriver selenium pyautogui mitmproxy pandas openpyxl
```

### 运行步骤

#### 终端 1：启动 MITM 代理
```bash
python start_mitm_proxy.py
```

#### 终端 2：启动主程序
```bash
USE_MITM_PROXY=true python main_automation.py
```

## 📊 数据采集

### 采集的数据字段（17 个）

**基础专利信息 (14 字段)**：
- `zhuanlimc` - 专利名称
- `shenqingrxm` - 申请人
- `zhuanlilx` - 专利类型
- `shenqingr` - 申请日
- `falvzt` - 法律状态
- `anjianbh` - 案件编号
- `anjianywzt` - 案件业务状态
- `famingzlsqgbg` - 发明公布号
- `shouquanggh` - 授权公告号
- `gongkaiggh` - 公开公告号
- `gongkaiggr` - 公开公告日
- `shouquanggr` - 授权公告日
- `zhufenlh` - 主分类号

**发文信息 (3 字段，仅在"驳回等复审请求"时采集)**：
- `fwxx_list` - 完整的发文列表
- `bhsjtzs_xiazaisj` - 驳回决定的时间
- `bhsjtzs_data` - 驳回决定的详细信息

### 输入与输出

**输入**：
- `data/search_list.txt` - 申请号列表（一行一个）
- `data/config.json` - 鼠标坐标配置

**输出**：
- `data/results/detection_log.json` - 采集结果日志
- `data/results/patents_data.xlsx` - Excel 导出文件

## 🔧 核心程序文件

| 文件 | 功能 |
|------|------|
| `main_automation.py` | 主自动化程序，控制全流程 |
| `start_mitm_proxy.py` | 启动 MITM 代理 |
| `patent_mitm_scraper.py` | MITM 拦截脚本，解析 API 响应 |
| `detection_logger.py` | 日志记录模块，数据序列化和导出 |
| `patent_data_cache.py` | 内存缓存模块（备用） |

## 💡 工作原理

```
CNIPA 网站
    ↓
PyAutoGUI 物理操作 (鼠标、键盘)
    ↓
MITM 代理拦截 API 响应 (127.0.0.1:8080)
    ↓
patent_mitm_scraper.py 解析 JSON
    ↓
缓存数据 (patent_cache.json)
    ↓
main_automation.py 查询缓存
    ↓
创建 DetectionRecord
    ↓
导出 JSON 和 Excel
```

## 📝 测试模式

```bash
# 测试前 3 个申请号
USE_MITM_PROXY=true python main_automation.py --test 3
```

## 💾 数据位置

- **搜索列表**: `data/search_list.txt`
- **采集结果**: `data/results/detection_log.json` (JSON 日志)
- **Excel 导出**: `data/results/patents_data.xlsx`
- **基础缓存**: `data/patent_cache.json` (MITM 拦截的原始数据)
- **配置文件**: `data/config.json` (鼠标坐标)

## ⚙️ 系统特点

- ✅ **完全避免检测** - 零 DOM 操作，无法被反爬虫系统识别
- ✅ **完整数据采集** - 14 个基础字段 + 3 个发文字段
- ✅ **断点续传** - 已处理的申请号自动跳过
- ✅ **优雅降级** - MITM 失败时仍可继续运行
- ✅ **条件采集** - 仅对"驳回等复审"状态的案件采集发文信息

## 🆘 常见问题

### Q: MITM 代理无法启动
```bash
# 确保已安装 mitmproxy
pip install mitmproxy
```

### Q: 浏览器连接不到代理
```bash
# 检查端口是否被占用
lsof -i :8080
```

### Q: 程序卡住了
- 按 Ctrl+C 中断
- 检查 `data/search_list.txt` 中已处理的申请号
- 清理未完成的缓存文件（如需要）

---

**最后更新**: 2026-03-01
**项目大小**: ~20 MB (包含 chromedriver)
**数据量**: 500+ 条采集记录，~700 KB
