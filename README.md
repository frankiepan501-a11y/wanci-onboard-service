# wanci-onboard-service (万词上线自动化 L2)
飞书「万词上线申请」表 → n8n 触发 POST /onboard {record_id} → 全自动建作战台+审计+发运营。
密钥全走 env: FEISHU_APP_ID/FEISHU_APP_SECRET/LX_PROXY_TOKEN/ONBOARD_TOKEN。

## 修复记录

### 2026-07-15 P1：周复审快照静默不更新
- 问题：`/review` 返回 `review started`，但「万词周快照」没有新增记录，导致看板把旧快照误判为未建 Rank/快照过期。
- 根因：飞书单选字段在读取时可能返回单值字符串，也可能返回一项列表；`do_review()` 直接用 `f.get("状态") == "在跑"`、`f.get("站点") == site` 比较，后台线程会筛错数据且没有可见日志。
- 改动：新增 `ss()` 单选规范化，应用到 `/review` 的状态/站点/区域匹配和 `/report` 的站点匹配；同时为 `/review` 增加启动、加载、计算、写入日志。
- 验证：本地 `py_compile` 通过；部署后用 `POST /review {"frankie_only": true}` 触发并检查周快照表是否写入当天记录。
