# 发文信息采集 - 快速参考

## 三行代码快速启动

```bash
# 终端 1：启动 MITM 代理
python start_mitm_proxy.py

# 终端 2：启动采集程序（测试模式，仅 3 个）
USE_MITM_PROXY=true python collect_fwxx.py --test 3
```

## 核心文件

| 文件 | 功能 |
|------|------|
| `collect_fwxx.py` | 主采集模块（625 行） |
| `patent_mitm_scraper.py` | MITM 脚本（已改动） |
| `detection_logger.py` | 日志导出（已改动） |

## 工作流

```
筛选目标 → 配置坐标 → 逐个采集 → 更新日志 → 导出 Excel
```

## 采集流程（单个申请号）

```
搜索申请号
  ↓
进详情页（新标签）
  ↓
标记申请号 (data/current_fwxx_target.json)
  ↓
点击发文信息
  ↓
MITM 拦截 API
  ↓
读取缓存 (data/patent_fwxx_cache.json)
  ↓
关闭标签回搜索页
```

## 关键命令

| 命令 | 说明 |
|------|------|
| `python test_fwxx_basic.py` | 基础功能测试 |
| `USE_MITM_PROXY=true python collect_fwxx.py --test 3` | 测试模式 |
| `USE_MITM_PROXY=true python collect_fwxx.py` | 完整采集 |

## 输出文件

- `data/results/detection_log.json` - 采集日志（更新）
- `data/results/patents_data.xlsx` - Excel 导出（两个 Sheet）
  - Sheet1: 专利主信息
  - Sheet2: 发文信息

## 防爬虫特性

✅ PyAutoGUI 物理操作（不触及 DOM）
✅ 字符级延迟模拟（50-180ms）
✅ 随机长延迟注入（15% 概率）
✅ 批量防反爬等待（每 3 个申请号 2-5s）

## 常见问题

**Q: 首次运行需要什么？**
A: 需要手动记录 2 个坐标（申请号链接 + 发文菜单位置）

**Q: 如果某个申请号失败了？**
A: 会自动跳过并继续下一个，支持 Ctrl+C 中断，下次会跳过已采集的

**Q: Excel 文件在哪里？**
A: `data/results/patents_data.xlsx` （两个 Sheet）

**Q: 能否并行采集多个？**
A: 当前是顺序采集，已包含防反爬等待，不建议并行

## 性能指标

| 指标 | 时间 |
|------|------|
| 单个申请号 | 15-25 秒 |
| 10 个申请号 | 2-3 分钟 |
| 50 个申请号 | 10-15 分钟 |
| 100 个申请号 | 20-30 分钟 |

## 遇到问题

1. **坐标不准？** - 删除 `data/config_fwxx.json` 重新记录
2. **MITM 超时？** - 检查代理是否正常运行
3. **标签未关闭？** - 检查浏览器窗口，手动关闭多余标签

## 下一步

1. 运行基础测试：`python test_fwxx_basic.py`
2. 启动 MITM 代理：`python start_mitm_proxy.py`
3. 测试采集：`USE_MITM_PROXY=true python collect_fwxx.py --test 3`
4. 查看结果：打开 `data/results/patents_data.xlsx`

---

**最后更新**: 2026-03-01
