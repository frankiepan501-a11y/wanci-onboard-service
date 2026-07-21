# wanci-onboard-service (万词上线自动化 L2)
飞书「万词上线申请」表 → n8n 触发 POST /onboard {record_id} → 全自动建作战台+审计+发运营。
密钥全走 env: FEISHU_APP_ID/FEISHU_APP_SECRET/LX_PROXY_TOKEN/ONBOARD_TOKEN。

## 修复记录
### 2026-07-21 P1：DE 站误读 UK 店铺 Listing
- 问题：运营反馈 DE 站两个报告显示“Listing需先处理：店铺不可售”，但亚马逊后台截图显示同一 ASIN 和店铺里的 SKU 实际在售，标题也和报告不一致。
- 根因：万词总台和申请表里两条 DE 记录填了 UK 店铺编号 `1192`，其中一条店铺里的 SKU 还带了换行。系统按错误店铺编号读取 Listing，读到 UK 店铺旧数据后误判为 Listing 问题。
- 数据修正：
  - `B0DHVP5DL7 / DE`：店铺编号改为 `1194`，店铺里的 SKU 修正为 `PPFFSCWD-MN-EU`，申请表店铺名改为 `FunlabDirect-DE`。
  - `B0CM3GM3B3 / DE`：店铺编号改为 `1194`，店铺里的 SKU 保持 `PPFFSC-CATPAW02-EU`，申请表店铺名改为 `FunlabDirect-DE`。
- 代码改动：
  - `resolve_store()` 自动反查店铺时先按站点国家筛店铺，再匹配店铺名，避免 `FunlabDirect` 这类泛名称命中 UK 店。
  - 新增店铺编号和站点国家校验；错配时报告显示“配置需先修正”，不读取 Listing，不写表 2/3/5/6，也不发“已完成”成功话术。
  - 14 维报告的“系统读取依据”增加店铺名、店铺国家、站点应对应国家，运营能直接看出系统读的是哪家店。
  - `ext()` / `ss()` 统一去掉前后空格，避免店铺里的 SKU 前后带换行导致查错。
- 验证：本地 `C:\tmp\py311-embed\python.exe -m py_compile app.py`；`C:\tmp\py311-embed\python.exe -m unittest discover -s tests`，11 个测试通过。线上只读核对 `/report?asin=B0DHVP5DL7&site=DE` 和 `/report?asin=B0CM3GM3B3&site=DE`：均显示店铺编号 `1194`、`FunlabDirect-DE`、在售状态 `BUYABLE/DISCOVERABLE`，不再显示“店铺不可售”。
- 剩余风险：代码保护需要部署后才会在新报告里显示店铺国家和拦截错配；已修正的两条记录线上报告已恢复正确口径。

### 2026-07-17 P1：卡片闭环 + 3 天广告否词建议
- 问题：运营如果去万词总表里手动改状态，系统无法稳定知道是谁处理、什么时候处理、处理说明是什么，也无法做到 7 天复检和 14 天升级。广告开跑后也缺少按广告组整理的否词建议闭环。
- 改动：
  - 新增万词问题卡片结构：卡片包含产品、站点、ASIN、负责人、店铺编号、店铺 SKU、问题类型、具体原因、下一步和 14 维报告链接。
  - 周复审发现 Listing 需先处理、待办过期、广告没跑、最近停了时，会先写入「万词执行跟进台」；发卡默认关闭，避免未灰度就打扰运营。
  - 新增卡片动作：`wanci_issue_done`、`wanci_issue_skip`、`wanci_issue_reassign`、`wanci_issue_help`、`wanci_negatives_confirm`、`wanci_negatives_skip`、`wanci_negatives_help`。
  - 新增 `/wanci/card/callback`：接收亚马逊助手回调转发，回填「万词执行跟进台」，并 PATCH 原卡为“已处理，无需重复点击”。
  - 新增 `/wanci/negatives/run`：默认 dry-run，读取「在跑 + Listing 正常 + 有店铺编号/店铺 SKU/负责人」的万词记录，按近 3 天广告搜索词生成广告组级精准否定建议。
  - 新增否词写回保护：真实写 ERP 前必须有广告组检查接口、已存在否词检查接口、写回接口，并且 `WANCI_NEG_WRITE_ENABLED=1`；否则只 dry-run 或阻止。
- 关键环境变量：
  - `WANCI_TRACK_TB`：万词执行跟进台 table_id。未配置时不写表，只返回 dry-run 结果。
  - `WANCI_NEG_TB`：万词否词建议台 table_id。未配置时不写表，只返回建议。
  - `WANCI_CARD_APP_ID` / `WANCI_CARD_APP_SECRET`：发卡和 PATCH 原卡使用的 App，生产应配置为「亚马逊助手 App」。
  - `WANCI_CARD_FRANKIE_ONLY`：默认 `1`，真实负责人发卡前先 Frankie-only 测试。
  - `WANCI_ISSUE_CARD_ENABLED`：默认 `0`。设为 `1` 后，周复审会发送万词问题卡；仍受 `WANCI_CARD_FRANKIE_ONLY` 控制。
  - `WANCI_ISSUE_CARD_LIMIT`：默认每次最多发 30 张问题卡。
  - `WANCI_NEG_WRITE_ENABLED`：默认 `0`，不开启真实 ERP 写回。
  - `WANCI_NEG_ADGROUP_CHECK_ENDPOINT`、`WANCI_NEG_EXISTING_ENDPOINT`、`WANCI_NEG_WRITE_ENDPOINT`：ERP 否词真实写回前必须现场确认并配置。
  - 否词卡片只有在回调 payload 或卡片 `value` 明确带 `commit=true`，且上面写回开关和检查接口都已配置时，才会尝试真实写 ERP。
- 接口：
  - `POST /wanci/card/callback`：给现有亚马逊助手回调入口转发 `wanci_*` 卡片动作。
  - `POST /wanci/negatives/run`：默认 `{"dry_run": true, "frankie_only": true}`；不会发真实运营卡，不会写 ERP。
- 验证：本地 `C:\tmp\py311-embed\python.exe -m py_compile app.py`；`C:\tmp\py311-embed\python.exe -m unittest discover -s tests`，8 个测试通过。
- 未做：本轮未部署、未发真实飞书卡、未写真实 ERP 否词；现有亚马逊助手回调服务所在仓库不在当前项目内，需要后续加一个 `wanci_*` 转发分支。

### 2026-07-15 P1：周复审误报和普通话术修复
- 问题：周自检把「筹备中」「店铺不可售」「后台搜索词没填」的项目也混进广告问题里，并用「失职 / 半成品 / 催」等词，运营容易误解为自己没有处理文案或广告。
- 根因：`do_review()` 判断广告没跑时只看库存、排名、曝光，没有先确认项目是否正式在跑、Listing 是否正常可售；`compute_audit()` 只返回「半成品」这类粗标签，没有告诉同事具体缺哪一项。
- 改动：广告提醒现在只对「状态=在跑」且「Listing 正常」的项目生效；Listing 异常改成具体原因，如「后台搜索词没填」「店铺不可售」「店铺没这条Listing」。周报文案改成「广告没跑 / 最近停了 / Listing需先处理 / 待办过期」。
- 数据纠偏：按运营反馈和领星核对，把 11白眼 UK、24图鉴 UK、24波纹 UK 的总台店铺改为实际可售的 DRIESNAUDE-UK，避免系统继续读 FunlabDirect-UK 的不可售旧记录。
- 验证：本地 `py_compile`；部署后触发 `/review` 检查总览卡是否不再把筹备或不可售项目列入广告问题。

### 2026-07-15 P1：周复审快照静默不更新
- 问题：`/review` 返回 `review started`，但「万词周快照」没有新增记录，导致看板把旧快照误判为未建 Rank/快照过期。
- 根因：飞书单选字段在读取时可能返回单值字符串，也可能返回一项列表；`do_review()` 直接用 `f.get("状态") == "在跑"`、`f.get("站点") == site` 比较，后台线程会筛错数据且没有可见日志。
- 改动：新增 `ss()` 单选规范化，应用到 `/review` 的状态/站点/区域匹配和 `/report` 的站点匹配；同时为 `/review` 增加启动、加载、计算、写入日志。
- 验证：本地 `py_compile` 通过；部署后用 `POST /review {"frankie_only": true}` 触发并检查周快照表是否写入当天记录。