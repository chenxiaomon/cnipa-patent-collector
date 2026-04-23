# 公开搜索采集 - 快速开始

## 🚀 3 步完成采集

### 步骤 1：启动代理（终端 1）
```bash
python start_mitm_public_search.py
```
保持这个终端打开，看到 `[*] 监听地址: 127.0.0.1:8080` 说明成功。

---

### 步骤 2：打开浏览器（终端 2）
```bash
python launch_browser_with_proxy.py
```
浏览器会自动打开并配置好代理。

---

### 步骤 3：手动采集
在浏览器中：
1. 输入查询条件（申请人等）
2. 点击"查询"按钮
3. **手动点击"下一页"**按钮，重复直到完成
4. 关闭浏览器（Ctrl+C 或关闭窗口）

**监控进度**：看终端 1 的日志
```
[+] 拦截到公开搜索 API: ...
[*] 成功提取 30 条数据
[✓] 本页新增 30 条数据，累计 1580 条
```

---

## 📥 导出数据

采集完成后，运行：
```bash
python export_public_search.py
```

**输出文件**：
- 📊 `data/results/public_search_results.xlsx`（Excel，推荐）
- 📄 `data/results/public_search_results.json`（JSON）

---

## ✅ 查看结果

打开 Excel 文件：
```bash
start data/results/public_search_results.xlsx
```

或双击：`data/results/public_search_results.xlsx`

**包含的字段**：
- 申请号 / 发明名称 / 申请人 / 专利类型
- 申请日 / 法律状态 / 主分类号 / 等等

---

## 🆘 常见问题

**Q：浏览器无法打开？**
- 检查第 1 步代理是否运行

**Q：代理后浏览器显示错误？**
- 这是正常的（HTTPS 证书），忽略即可

**Q：采集了多页但导出数据很少？**
- 检查 `data/raw_responses/` 目录有多少个 JSON 文件
- 每个文件应该有 30+ 条记录

**Q：需要采集大量数据（50+ 页）？**
- 改用自动翻页脚本：`python auto_paginate.py --delay 1.5 --max-pages 50`

---

## 📋 完整流程图

```
终端 1              浏览器            终端 2
   │                 │                 │
   │ python start_   │                 │
   │ mitm_public_    │                 │
   │ search.py       │                 │
   │                 │                 │
   │ 代理运行中...    │                 │
   │                 │                 │
   │                 │ python launch_  │
   │                 │ browser_with_   │
   │                 │ proxy.py        │
   │                 │                 │
   │                 ├─ 打开 CNIPA    │
   │                 │                 │
   │                 ├─ 输入查询条件  │
   │                 │                 │
   │                 ├─ 点击查询      │
   │ 拦截第 1 页    │                 │
   │ 保存 JSON       │                 │
   │                 │                 │
   │                 ├─ 点击下一页    │
   │ 拦截第 2 页    │                 │
   │ 保存 JSON       │                 │
   │                 │                 │
   │  ... 重复 ...   │  ... 重复 ...  │
   │                 │                 │
   │                 ├─ 关闭浏览器    │
   │                 │                 │
   │ 保存完成        │                 │
   │                 │                 │
                                      python export_
                                      public_search.py
                                         │
                                         ↓
                                      data/results/
                                      ├─ .xlsx
                                      └─ .json
```

---

## 💡 Tips

- **快速采集**：点击翻页很快，代理也会立即采集
- **实时监控**：每点击一下翻页，终端 1 就会显示新的数据
- **随时停止**：采集中随时可以停止，已采集的数据都保存了
- **重新采集**：删除 `data/raw_responses/` 目录，可以重新采集

---

## 🎯 预期结果

- **采集 1-10 页**：150-300 条数据 ⏱️ 10 分钟
- **采集 20-30 页**：600-900 条数据 ⏱️ 30 分钟
- **采集 40+ 页**：1200+ 条数据 ⏱️ 1 小时

Excel 文件中的数据：
- ✅ 申请号去重（无重复）
- ✅ 表头加粗、可筛选
- ✅ 自动列宽
- ✅ 可直接使用

---

**就这么简单！** 🎉
