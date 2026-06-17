# 测试 Fixture — 真实 API 响应存档

## 用途

这里存放从 CNIPA 真实 API 抓取的响应，作为**金标准（golden master）**。

当 CNIPA 修改 API 响应格式（字段名变化、结构调整）时，对应的 fixture 文件与新响应会出现差异，
从而触发 `TestGoldenMasterSqxx` / `TestGoldenMasterFwxx` 等测试失败——这是预期行为，说明真实 API 已变化。

## 文件说明

| 文件 | 来源 API | 说明 |
|------|---------|------|
| `sqxx_real_response.json` | `/api/view/gn/sqxx` | 专利详情接口，含代理机构字段 `dailijg.dailijgList` |

## 更新 Fixture 的流程

当 CNIPA 改了 API 格式（测试因此变红）：

1. 用 MITM 代理重新抓取一次真实响应（浏览器打开 CNIPA，代理日志里找对应 URL）
2. 把新响应内容替换对应的 `.json` 文件
3. 更新测试文件中对新字段名的断言
4. 提交 —— 这次 commit 就是 "适配 CNIPA API 变更" 的记录

## 注意事项

- fixture 内容是真实专利数据，已脱敏（发明人姓名等不包含真实个人信息）
- **不要**把真实用户的申请号/姓名写入 fixture
- 如果 CNIPA 未改格式但 fixture 过期（版本号变化等），也可按上述流程更新
