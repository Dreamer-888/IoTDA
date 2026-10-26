# WebSocket 实时数据与设备告警

更新日期：2026-10-15。当前提供 17 个 HTTP 业务接口和 1 个 WebSocket 接口。

## 启动和接入

```bash
.venv/bin/python -m pip install -r requirements.txt
npm --prefix frontend run build
.venv/bin/python run.py
```

访问 `http://127.0.0.1:5000/test-ui/`。页面及实时连接均使用这个端口，浏览器自动携带登录 Cookie。现有 AMQP 接收器、HTTP 查询接口和每日步数保存继续使用同一后端进程。

实现使用 [Flask-Sock](https://flask-sock.readthedocs.io/en/latest/quickstart.html)，启用 25 秒协议心跳并限制客户端消息为 4096 字节；[接收与关闭接口](https://simple-websocket.readthedocs.io/en/latest/api.html) 由 simple-websocket 提供。

单独使用 Vite 开发时，`frontend/vite.config.js` 已启用 WebSocket 代理。后端 `.env` 中的 `FRONTEND_ORIGIN` 需与浏览器实际来源精确一致，例如 `http://127.0.0.1:5173`；修改后重启后端。统一使用 `5000/test-ui/` 时不需要这项跨来源配置。反向代理部署需要转发 WebSocket Upgrade。

## 登录和权限

先使用现有登录接口或 `GET /api/auth/me` 恢复登录状态，再建立 WebSocket。后者也会为旧登录会话补充实时连接使用的会话标识，前端不需要读取它。

登录 Cookie 有效期为 7 天；普通只读请求不再续签 Cookie，以避免并发响应覆盖较新的登录或升级状态。旧 Cookie 的并发恢复使用同一实时连接标识。WebSocket 断开后，前端会先调用 `auth/me` 恢复会话再补取告警，后续连接尝试可自动恢复。

- 连接地址：`ws://127.0.0.1:5000/api/telemetry/live`。HTTPS 页面使用同主机的 `wss://`。
- 握手校验 Cookie、数据库会话版本和 Origin。缺少登录为 HTTP 401，来源不允许为 HTTP 403。
- 连接内每秒重新检查会话版本、有效期和当前订阅设备归属。
- 登录切换或退出撤销当前浏览器会话的连接；修改密码撤销该用户全部实时连接。
- 每个用户最多 8 个连接，进程最多 128 个；每连接最多暂存 256 条消息。慢连接积压后关闭并重连同步，避免阻塞 AMQP 消费。
- 当前仍要求单个后端进程。内存样本和实时连接中心未实现跨进程共享。

## 客户端订阅

服务端连接成功后先发送：

```json
{"type":"ready","data":{"user_id":1,"store_id":"本地告警存储标识"}}
```

客户端随后发送订阅；`device_id` 为本地数字 ID：

```json
{"type":"subscribe","subscription_id":1,"device_id":7,"seconds":60,"filter":"none","filter_window":5}
```

`seconds`、`filter`、`filter_window` 与窗口 HTTP 接口约束一致。`subscription_id` 为客户端自行递增的非负整数，最大 2147483647；服务端在样本、快照和状态消息中原样返回。切换设备、时间窗口或滤波设置后发送新订阅，前端丢弃旧订阅消息。

离开监测页或隐藏标签页时，可以发送 `device_id: null` 停止样本订阅，连接仍接收该用户全部已绑定设备的告警。

| 消息类型 | 内容 |
| --- | --- |
| `ready` | 登录用户 ID 和告警存储标识 |
| `snapshot` | `{user_id,device_id,raw_samples,latest,window,summary,status}`，订阅时完整同步；包含 `subscription_id` |
| `sample` | `data` 为一条完整样本，与 latest 字段一致；包含 `subscription_id` |
| `status` | `data` 与设备状态接口一致，每秒更新；包含 `subscription_id` |
| `alert` | `data` 为一条告警事件，用户其他已绑定设备的告警也会发送 |
| `error` | `{type,code,message}`，订阅非法或设备无权限等错误 |

前端首次使用快照，随后追加新样本并裁剪窗口。姿态读取新样本的 roll/pitch/yaw；曲线和六轴统计在前端按窗口更新，统计使用原始值。连接中断时回退到现有 HTTP 查询，并按 1–30 秒间隔重连。重连后重新获取快照及告警。

关闭码：4401 表示登录失效；4404 表示设备已解绑或归属失效；4400 表示订阅格式错误；1013 表示连接积压；1011 表示实时服务异常。登录失效应停止重连并清空用户数据。

## 告警触发与保存

每台设备分别保存 `is_fall` 和 `is_sedentary` 的最近状态：

| 变化 | 行为 |
| --- | --- |
| `0 → 1` | 生成一次对应的 `fall` 或 `sedentary` 事件 |
| `1 → 1` | 不重复生成 |
| `1 → 0` | 恢复状态，允许下次触发，不生成新的告警 |
| 首次收到即为 `1` | 视为从默认 `0` 触发，生成一次事件 |

告警状态和事件存放在已有 `DAILY_STEPS_STATE_PATH` SQLite 工作文件中，默认 `data/steps.sqlite3`，不新增或修改 MySQL 表。重启后保留状态，持续的 `1` 不会仅因重启重复告警。上报时间和云端事件时间早于已处理状态的旧报文不会回退状态；稳定消息标识用于去重，最近 50000 条告警处理投递标识保存在工作文件中。没有稳定消息标识时不能保证任意重发完全去重。

事件在 AMQP 确认前保存。步数先提交，告警保存失败时消息不确认；重试时已有步数不会再次相加。解绑清除设备当前标记状态并撤销对应实时订阅，告警查询只返回当前仍归属登录用户的设备事件。

事件字段示例（仅用于格式说明）：

```json
{"sequence":42,"event_id":"唯一事件标识","device_id":7,"type":"fall","occurred_at":"2026-10-15T01:00:00+00:00","received_at":"2026-10-15T01:00:00.100000+00:00"}
```

`occurred_at` 来自设备服务上报时间，`received_at` 是后端接收时间，均为 UTC。事件保留查询最近 7 天的范围；新告警产生时清理过期事件。它记录设备标记触发事实，不代表后端重新运行了摔倒判断算法。

## 告警补取 HTTP 接口

```http
GET /api/telemetry/alerts?after=0&limit=100
```

使用 Cookie 登录。`after` 是非负整数序号，`limit` 为 1–200，默认 100；不能重复提交参数。

```json
{"code":200,"message":"查询成功","data":{"user_id":1,"store_id":"告警存储标识","events":[],"cursor":42,"has_more":false,"retention_seconds":604800}}
```

首次或重连后按已保存的 `cursor` 查询；`has_more=true` 时继续用新游标取下一页。事件按序号升序返回，前端使用 `event_id` 去重。游标只用于读取位置，不表示用户确认或关闭了告警。`store_id` 改变表示工作文件已更换，需要清空旧游标从 0 同步。

当前页面最多展示最近 50 条事件；实时新事件显示提醒，断线补取和首次加载的旧事件只补入列表。退出、换用户和设备解绑时清理相应显示。历史步数接口和每天 22:00 的保存规则不受这次功能影响。

## 验证方式

```bash
PYTHONPATH=tests .venv/bin/python -m unittest test_live
IOTDA_SOCKET_TESTS=1 PYTHONPATH=tests .venv/bin/python -m unittest test_live_socket
npm --prefix frontend test
```

第一组验证短脉冲、上升沿、持续状态、重启去重、乱序、分页、用户隔离、解绑与慢连接。第二组使用本机临时端口进行真实 WebSocket 握手和消息测试，使用临时 SQLite 与模拟数据库，不访问华为云或正式数据库。
