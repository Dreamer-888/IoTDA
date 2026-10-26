# 物联网姿态可视化系统

基于华为云 IoT 与六轴姿态传感器的项目。当前后端已实现用户管理、设备管理、MySQL 数据访问、AMQP 1.0 主动接收华为云通知、内存缓冲、每日步数持久化及历史查询。接收格式采用真实设备的 `device.property / six_axis` 通知，完整保留六轴、姿态角、步数增量、久坐和摔倒标记。前端提供实时曲线、姿态模型、历史步数及用户和设备管理；优先通过 WebSocket 接收实时样本与设备告警，断线时使用 HTTP 备用查询。

## 项目结构

```text
IoTDA/
├── README.md                 # 项目说明与启动指南
├── .env                      # 环境变量配置
├── run.py                    # 自动加载 .env 并启动后端开发服务
├── requirements.txt          # Python 依赖
├── mysql_schema.sql          # MySQL 数据库与表初始化脚本
├── iot_database_design.md    # 数据库设计说明
├── migrations/               # 已有数据库的版本迁移脚本
├── frontend/                 # Vite 前端源码；dist/ 为 Flask 提供的构建产物
├── tests/                    # 测试代码与 fixtures 报文样例
└── src/
    ├── auth_api.py           # Flask 应用入口，注册用户与设备接口
    ├── DBcontrol.py          # MySQL 连接池、事务与数据访问
    ├── common/               # 公共鉴权、日志、错误、响应与时间转换
    ├── user/                 # 用户 Controller、Service、DTO、VO 与模型
    ├── devices/              # 设备 Controller、Service、DTO、VO、模型与归属验证
    └── telemetry/            # 六轴数据接收、缓冲、统计、滤波与查询接口
```

## 安装与启动

WSL/Ubuntu 首次安装时，先安装 AMQP 库的 TLS 编译依赖：

```bash
sudo apt-get update
sudo apt-get install -y build-essential python3-dev libssl-dev pkg-config ca-certificates
```

在项目根目录执行，已有虚拟环境时可直接安装依赖：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

若曾在缺少 OpenSSL 开发文件时安装过 Proton，可能得到不支持 TLS 的库。
安装上述系统依赖后重新构建（不要复用旧 wheel）：

```bash
.venv/bin/python -m pip install --force-reinstall --no-cache-dir --no-binary=python-qpid-proton 'python-qpid-proton>=0.40,<0.41'
```

启动前确保 MySQL 服务运行，目标数据库已有符合 [数据库设计](iot_database_design.md) 的 `users`、`devices` 和 `daily_steps` 表。应用不会自动创建数据库或表。

新环境可使用 [mysql_schema.sql](mysql_schema.sql) 初始化，需要使用有建库和建表权限的 MySQL 账号：

```bash
mysql -u your_mysql_user -p --init-command="SET SESSION time_zone = '+00:00'" < mysql_schema.sql
```

脚本创建并选择 `iot_visualization` 数据库。`CREATE TABLE IF NOT EXISTS` 会跳过已有表，不会将已有表结构迁移到新版本。

已有数据库升级到当前版本时，若 `users` 表尚无 `session_version` 字段，应在启动新后端前执行一次迁移：

```bash
mysql -u your_mysql_user -p iot_visualization < migrations/001_user_session_version.sql
```

新建数据库无需执行此迁移；已执行的迁移不要重复运行。迁移保留用户与设备数据，升级前的登录会话需重新登录。

已有数据库还需执行每日步数表迁移；该脚本可重复执行，不会删除已有数据：

```bash
mysql -u your_mysql_user -p iot_visualization < migrations/002_daily_steps.sql
```

### WSL 中构建前端并统一启动

前端构建需要 Node.js 22.12+ 或 24+。首次部署或修改前端后，在 WSL 的项目根目录执行：

```bash
npm --prefix frontend ci
npm --prefix frontend run build
```

`npm ci` 按锁文件重新安装当前系统的依赖，会替换 `frontend/node_modules`。Windows 的依赖目录不能直接复用于 WSL。首次安装后，仅修改前端源码时执行构建命令即可；不要在 Windows 和 WSL 中共用同一份 `node_modules`。

编辑 [.env](.env)，配置数据库账号、密码及 `APP_SECRET_KEY` 等配置，然后启动：

```bash
.venv/bin/python run.py
```

`run.py` 自动读取项目根目录的 `.env`，默认监听 `127.0.0.1:5000`。Flask 同时提供 `frontend/dist` 中的页面、`/api` 接口，并运行 AMQP 接收及步数定时任务；日常启动只需上述 Python 命令，无需另开 Vite。Windows 浏览器访问 http://127.0.0.1:5000/test-ui/，按 `Ctrl+C` 停止。缺少前端构建产物时，页面入口返回 503 并提示构建，后端接口仍可使用。已有环境变量优先于 `.env`。配置文件支持 `KEY=value`、空值及单行引号，不执行 shell 命令，也不展开 `$变量`。

修改前端后重新执行 `npm --prefix frontend run build`，再刷新页面；只修改前端无需重启后端。前端改为请求同源 `/api`，此运行方式不需要配置 `API_TARGET` 或跨域来源。需要前端热更新时，可参考 [前端说明](frontend/README.md) 单独启动 Vite。

可选启动参数：

```bash
.venv/bin/python run.py --host 127.0.0.1 --port 5001
.venv/bin/python run.py --debug
```

`--debug` 仅用于本地开发，不启动自动重载，避免重复创建队列消费者。

使用 `run.py` 同时启动 Flask 和 AMQP 接收线程，共享同一份内存缓冲。
直接使用 `flask --app src.auth_api:create_app run` 只会创建查询/管理接口，不启动接收器。
不要将 AMQP 消费者作为独立进程导入 `create_app()`，也不要启动多个 Web worker；
当前内存缓存不跨进程共享，多个队列消费者也可能分摊消息。

仅检查云端 TLS、认证和队列连接（不启动 Web、不消费队列消息、不访问 MySQL）：

```bash
.venv/bin/python run.py --check-amqp
```

直接验证能否收到华为云报文，无需登录、绑定设备、配置 MySQL 或 `APP_SECRET_KEY`：

```bash
.venv/bin/python run.py --peek-amqp --wait-seconds 60
```

先停止正常运行的后端及同队列其他消费者，再运行此命令；看到“AMQP 已连接”后，
让设备持续上报。命令逐条显示接收时间（北京时间）、报文序号和原始内容，按 `Ctrl+C` 停止并显示总数。
每条最多显示 16384 字符；不要求报文符合六轴格式，不调用业务服务、不写入内存缓存。
`--wait-seconds` 默认为 30，范围 1–300；持续模式中该值是无报文提示间隔，不是总运行时长，
无报文时继续等待。网络连接中断后按 1–30 秒退避重连。
每条成功显示并刷新终端后发送接受确认，队列不再将该条消息留给业务接收器；
重复投递仍可能发生，例如显示成功后确认前连接中断。

需要保留原先的单条预览行为时，加上 `--once`：

```bash
.venv/bin/python run.py --peek-amqp --once --wait-seconds 60
```

单条模式接收一条后退出，并发送释放请求而非接受确认，允许消息重新投递。
单条模式连接正常但等待超时返回退出码 `2`，重复预览可能看到同一条消息。
正常后端会确认并忽略未绑定设备的有效上报；要通过用户接口查询六轴数据，仍须先绑定设备。

`APP_SECRET_KEY` 用于签名会话 Cookie，长期运行时应保存并复用同一密钥。本机使用 Unix socket 连接可额外设置 `DB_SOCKET=/var/run/mysqld/mysqld.sock`，数据库账号仍须有相应权限。

| 配置项 | 说明 |
| --- | --- |
| `DB_HOST` / `DB_PORT` | MySQL 地址与端口，默认 `127.0.0.1:3306` |
| `DB_USER` / `DB_PASSWORD` / `DB_NAME` | MySQL 登录账号、密码与目标数据库 |
| `DB_SOCKET` | 可选，本机 MySQL socket 路径 |
| `DB_POOL_SIZE` | 每个应用进程的连接池大小，默认 `5`，范围 `1–32` |
| `DB_CONNECT_TIMEOUT` | 建立数据库连接的超时秒数，默认 `5`，必须为正整数 |
| `APP_SECRET_KEY` | 必填，会话签名密钥 |
| `FRONTEND_ORIGIN` | 允许携带 Cookie 的前端完整来源，例如 `http://localhost:5173` |
| `SESSION_COOKIE_SECURE` | 默认 `false`，HTTPS 部署时设置为 `true` |
| `SESSION_COOKIE_SAMESITE` | 默认 `Lax`，按前后端部署关系配置 |
| `DEVICE_BINDINGS` | 设备预分配 JSON，默认 `{}`，具体见设备管理部分 |
| `LOG_LEVEL` | 日志级别，默认 `INFO`，支持 `DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL` |
| `LOG_DIR` | 日志目录，默认 `logs`；相对路径以项目根目录为基准 |
| `AMQP_ACCESS` | 必填，IoTDA AMQPS 公网接入域名；也接受 `amqps://域名:端口` |
| `AMQP_PORT` | 默认 `5671`；若地址中有端口，两处必须一致 |
| `AMQP_IP_VERSION` | 默认 `4`；支持 `4`、`6`、`auto`。默认避免 WSL 中 IPv6 不通导致建链超时；始终按原域名校验证书 |
| `AMQP_QUEUE` | 必填，转发规则选用的实际队列名称 |
| `AMQP_AK` / `AMQP_CODE` | 必填，IoTDA 预置接入凭证键值和密钥；兼容 `AMQP_ACCESS_KEY` / `AMQP_ACCESS_CODE`，两种名称同时填写时必须一致 |
| `AMQP_INSTANCE_ID` | 可选，按实例接入要求填写，尤其同区域多个标准版实例 |
| `AMQP_SERVICE_ID` | 真实设备服务 ID，默认 `six_axis`；留空也使用默认值，其他名称启动时会报错 |
| `AMQP_CA_FILE` | 可选，PEM CA 信任文件路径；默认使用系统 CA，始终校验云端证书和域名 |
| `TELEMETRY_RETENTION_SECONDS` | 样本保留秒数，默认 `300`，正整数 |
| `TELEMETRY_MAX_SAMPLES` | 单设备最多保留的样本数，默认 `6000`，正整数 |
| `TELEMETRY_MAX_DEVICES` | 内存缓冲最多保存的设备数，默认 `128`，正整数 |
| `DAILY_STEPS_SAVE_TIME` | 北京时间每天保存当天步数的时间，默认 `22:00`，格式 `HH:MM` |
| `DAILY_STEPS_STATE_PATH` | 步数累计和去重的本地持久化工作文件，默认 `data/steps.sqlite3`；相对路径以项目根目录为基准，重启时复用 |
| `TELEMETRY_ONLINE_TIMEOUT_SECONDS` | 最近上报的在线推断阈值，默认 `30` 秒，正整数 |

上述启动命令用于本地开发，当前数据缓冲要求单进程部署。连接池应为 MySQL 的其他连接留出余量。

## 前端交互约定

前端交接请使用 [接口交接说明](docs/前端接口交接.md) 和 [OpenAPI 定义](docs/openapi.json)，包含当前 17 个 HTTP 业务接口及 WebSocket 接入说明、字段单位、Cookie 登录方式、实时查询与每日历史步数的区别，以及异机联调步骤。

### 浏览器前端

构建前端并启动后端后，打开 **http://127.0.0.1:5000/test-ui/**。页面及其 JavaScript、CSS、字体和图片均由 Flask 从 `frontend/dist` 提供，使用同源 Cookie。Node.js 只用于安装依赖和构建，日常运行无需启动 Node 服务。请通过这个地址访问页面，避免直接打开本地 HTML 文件。

页面支持：

- 注册、登录、退出及恢复当前登录会话。
- 查询用户资料、修改用户名和邮箱、修改密码；邮箱留空会清除邮箱。
- 绑定设备、查看列表、重命名和解绑；设备必须已在 `DEVICE_BINDINGS` 中分配。
- 实时监测：选择自己的设备，查看三维姿态、六轴曲线、短窗口统计、步数增量及久坐和摔倒标记。
- 历史步数：近 7/30 天和自定义日期查询、每日明细，区分尚未记录与零步数。
- 显式演示模式：入口 `/test-ui/?demo=1`，仅使用页面内存中的演示数据，不访问真实接口。
- WebSocket 实时样本与告警：久坐、摔倒标记从 0 变为 1 时生成事件，前端实时提醒并保留事件列表；断线后按游标补取。

实时协议、Origin 配置、告警保存和接口示例见 [WebSocket 与告警](docs/WebSocket与告警.md)。统一使用 `5000/test-ui/` 时浏览器自动携带 Cookie，无需另开端口。事件保存在已有本地工作文件，查询最近 7 天，无需执行 MySQL 迁移。

真实模式下，用户和设备管理操作访问当前数据库，测试账号建议使用单独用户名。解绑前会弹出确认框。页面不会自动重试写入，也不会把密码保存到本地存储。切换用户、退出或会话失效时会清空旧用户资料、设备列表、曲线、姿态和历史记录，并丢弃旧请求响应。

真实监测数据来自正式 AMQP 接收流程，只接受已绑定设备通过完整真实报文校验的通知。请先停止 `--peek-amqp`，再用 `.venv/bin/python run.py` 启动后端；预览命令不会写入页面使用的缓存，同队列多个消费者也可能分摊消息。云端命令控制尚未实现。原始报文接口仍保留，见 [前端接口交接说明](docs/前端接口交接.md)。

### 请求与响应

请求体使用 JSON，并设置 `Content-Type: application/json`。用户与设备接口共用登录会话；登录成功后后端设置 HttpOnly 签名 Cookie，浏览器后续请求需携带 Cookie：

```javascript
const response = await fetch('http://localhost:5000/api/devices', {
  credentials: 'include'
});
const result = await response.json();
```

前后端分端口时，`FRONTEND_ORIGIN` 必须与浏览器页面来源一致。接口使用统一响应结构：

```json
{ "code": 200, "message": "操作成功", "data": {} }
```

成功注册或绑定设备返回 HTTP `201`，其他成功操作返回 `200`。常见错误包括参数错误 `400`、未登录或会话失效 `401`、权限不足 `403`、资源不存在 `404`、唯一性冲突 `409`、数据库繁忙或连接中断 `503`。

请求方法错误 `405` 和请求体过大 `413` 也返回统一 JSON；请求体上限为 `16 KiB`。所有响应携带 `X-Request-ID`，前端可用它关联后端日志。后端记录请求 ID、方法、路由模板、状态码和耗时，日志不记录请求体、密码或 Cookie。成功请求默认以 `INFO` 级别记录。

用户与设备时间字段均输出带 `+00:00` 时区的 UTC 时间。

## 用户管理

用户模块按 `AuthController → AuthService → DBControl` 分层，提供注册、登录、资料查询与修改。密码以哈希保存，响应不包含密码或密码哈希。

| HTTP 接口 | 功能 |
| --- | --- |
| `POST /api/auth/register` | 注册，校验用户名与邮箱唯一性 |
| `POST /api/auth/login` | 验证用户名与密码，建立登录会话 |
| `GET /api/auth/me` | 查询当前登录用户 |
| `GET /api/users/{id}` | 查询自己的用户信息 |
| `PATCH /api/users/{id}` | 修改自己的用户名、邮箱或密码 |
| `POST /api/auth/logout` | 清除登录会话 |

### 注册

`POST /api/auth/register`

请求示例：

```json
{
  "username": "alice",
  "password": "a-long-password",
  "email": "alice@example.com"
}
```

用户名长度为 `1–64` 字符，密码为 `8–128` 字符。邮箱可省略或传 `null`，填写时会去除首尾空白并转为小写。成功返回 HTTP `201`，`data` 是用户基本信息；用户名或邮箱重复返回 `409`。注册后需单独调用登录接口。

### 登录

`POST /api/auth/login`

```json
{
  "username": "alice",
  "password": "a-long-password"
}
```

成功返回 HTTP `200`，响应中 `data.user` 是用户基本信息，`data.authenticated` 为 `true`，同时设置 HttpOnly 会话 Cookie。账号不存在或密码错误统一返回 HTTP `401`。

### 查询用户信息

- `GET /api/auth/me`：根据当前会话查询登录用户；若会话中的用户已删除，后端会清理该会话并返回 `401`。
- `GET /api/users/{id}`：按用户 ID 查询信息，当前仅允许用户查询自己的 ID。

### 修改用户信息

`PATCH /api/users/{id}`，仅允许修改当前登录用户自己的资料。

修改用户名或邮箱：

```json
{ "username": "alice_new", "email": "new@example.com" }
```

清除邮箱可传 `{ "email": null }`。修改密码需同时传入当前密码和新密码：

```json
{ "current_password": "old-password", "password": "new-password-123" }
```

用户名和邮箱唯一性由数据库约束保证。修改密码时，服务会验证当前密码后再更新密码哈希。

密码更新会同时校验读取时的会话版本和原密码哈希，避免并发请求覆盖已修改的密码。冲突返回 `409`；若请求开始时会话已失效则返回 `401`。密码修改成功后，当前会话被清除，其他浏览器的旧会话在下次请求时返回 `401`，前端应跳转到登录页。即使密码更新的响应因网络问题丢失，旧会话版本也会失效。

### 退出

`POST /api/auth/logout`：清除会话并退出登录。

## 设备管理

设备模块按 `DeviceController → DeviceService → DBControl` 分层。`Device` 对应数据库记录，`DeviceDTO` 接收绑定参数，`DeviceVO` 输出资料。一个用户可绑定多台设备，每台设备只归属一个用户。

| HTTP 接口 | 功能 |
| --- | --- |
| `POST /api/devices` | 将已确认归属的云端设备绑定到登录用户 |
| `GET /api/devices` | 列出登录用户的全部设备 |
| `PATCH /api/devices/{id}` | 修改自己的设备显示名称 |
| `DELETE /api/devices/{id}` | 删除自己的设备绑定记录 |

### 绑定与重命名

绑定请求：

```json
{
  "cloud_device_id": "cloud-device-001",
  "cloud_product_id": "cloud-product-001",
  "display_name": "六轴传感器",
  "hardware_model": "NodeMCU + IMU"
}
```

`hardware_model` 可省略或为 `null`。云设备和产品标识最长 `128` 字符，显示名称、硬件型号最长 `100` 字符。用户 ID 始终取自登录会话，前端提交的 `user_id` 不决定归属。采样间隔和最后上报时间由设备消息与确认流程更新。

重命名请求：

```json
{ "display_name": "新的设备名" }
```

### 绑定归属验证

当前使用管理员预分配方案。启动前配置 `DEVICE_BINDINGS`，将华为云已注册设备分配给应用用户：

```bash
export DEVICE_BINDINGS='{"cloud-device-001":{"user_id":3,"cloud_product_id":"cloud-product-001"}}'
```

这里的用户 ID 是应用数据库中的 `users.id`。配置默认为空对象，此时没有设备获准绑定。绑定时同时核对用户 ID 和产品 ID：未分配或不匹配返回 `403`，重复绑定返回 `409`。

后续可通过 `create_app(device_verifier=...)` 注入云平台或配对码验证器。验证器接收 `(user_id, DeviceDTO)` 并返回布尔值，应根据可信的设备持有证明核验归属。

### 解绑与访问权限

解绑删除 MySQL 设备归属记录，不会调用华为云删除设备 API。未登录或会话失效返回 `401`；操作非本人设备返回 `404`。设备时间字段输出为 UTC。

数据接收模块会忽略未绑定云设备的上报，并在解绑时清理当前进程中的样本和报文缓冲。报文查询每次重新读取当前用户的绑定关系，前端同步移除已解绑设备。WebSocket 订阅在解绑时撤销，未发送的样本会清理。

## 设备数据接收：AMQP

数据链路：设备 MQTT 上报 → IoTDA 转发规则 → AMQP 队列 → 本进程接收线程 →
`TelemetryService.receive()` → 按设备保存样本和报文 → 已登录用户的 HTTP 查询接口。
不需要公网回调地址、内网穿透或本地 HTTPS 服务器。旧的
`POST /api/telemetry/report` 已移除（返回 404），`TELEMETRY_INGEST_KEY` 不再使用。

### 云端和本地配置

1. 进入设备所在的 IoTDA 实例，在“规则 → 数据转发”创建并启动规则。
2. 选择“设备属性 → 设备属性上报”，对应当前真实设备的 `device.property` 通知。
3. 转发目标选择“AMQP 推送消息队列”，新建或选择队列。保留原始通知结构，不使用 SQL 改写字段名。
4. 在实例总览的接入信息中复制 AMQPS 公网接入域名；在实例详情预置接入凭证。
   这里的凭证不是设备 MQTT 密码，也不是普通云 API AK/SK。
5. 在 `.env` 填写（已有配置请保留自己的实际值）：

```dotenv
AMQP_ACCESS=你的AMQPS接入域名
AMQP_PORT=5671
AMQP_IP_VERSION=4
AMQP_QUEUE=你的实际队列名称
AMQP_AK=你的接入凭证键值
AMQP_CODE=你的接入凭证密钥
AMQP_INSTANCE_ID=
AMQP_SERVICE_ID=six_axis
AMQP_CA_FILE=
```

连接使用 AMQP **1.0** / TLS / SASL PLAIN，依赖 `python-qpid-proton`。
证书和域名校验始终开启；无需申请本地网站证书。如系统信任链不包含云端 CA，
通过 `AMQP_CA_FILE` 提供可信 CA，而不是关闭校验。客户端系统时间应准确，
每次重连都会重新生成认证时间戳。
默认重新解析域名的 IPv4 地址，多地址时在重连间轮换，TLS SNI 和证书校验仍使用原始域名。
如果你的网络需要 IPv6，可设置 `AMQP_IP_VERSION=6`；`auto` 使用 Proton 默认的地址选择。

### 上报格式与绑定

真实通知见 [tests/fixtures/iotda-six-axis-report.json](tests/fixtures/iotda-six-axis-report.json)，使用用户提供的实际报文替换了两份旧模拟样例。正式接收只处理 `resource=device.property`、`event=report`，从 `notify_data.body.services[]` 选择唯一的 `service_id=six_axis` 服务并读取 `properties`。其他资源、事件或服务确认后忽略；重复的 `six_axis` 服务拒绝，避免混合不同样本。

旧的 `device.message` 自定义消息解析、`data` 包裹格式、按六轴字段自动猜测服务及 `IMU` 服务样例已移除。内部 DTO 直接校验真实 `properties`，不再构造模拟的 `{cloud_device_id, data}` 接收结构。`--peek-amqp` 仍可查看任意原始消息，不执行上述业务校验。

以下 12 个属性都必须存在：

| 字段 | 校验与处理 |
| --- | --- |
| `accel_x/y/z`、`gyro_x/y/z` | 有限数值，拒绝字符串、布尔值和 `null`；支持六轴统计和显示滤波 |
| `roll/pitch/yaw` | 有限数值，完整保留设备计算的姿态角 |
| `step_increment` | 非负整数，完整保留本条步数增量 |
| `is_sedentary`、`is_fall` | 整数 `0` 或 `1`，完整保留设备标记 |

设备 ID、产品 ID 始终取 `notify_data.header.device_id/product_id`；属性中的 `user_id`、`cloud_device_id` 不会改变归属。单位由设备端约定，后端不转换单位。

通知顶层的 `event_time`、服务的 `event_time` 必须是 `YYYYMMDDTHHMMSSZ` UTC 时间，`event_time_ms` 必须是带三位毫秒的 ISO UTC 时间，例如 `2026-10-12T03:40:18.976Z`。接口分别输出：

- `reported_at`：服务 `event_time`，保留其原有秒级精度。
- `cloud_event_at`：顶层 `event_time_ms`，保留毫秒精度。
- `received_at`：本后端实际接收时间。

三者均输出带 UTC 时区的 ISO 时间。窗口仍按后端接收次序和单调时间计算，不把云通知时间当作设备采样时间；在线状态仍以最近接收时间推断。

必须先通过设备管理接口完成本地绑定，未绑定设备的数据会忽略并确认，不会自动创建设备或用户。
`DEVICE_BINDINGS` 仍用于管理员预分配。绑定关系与 AMQP 接入凭证互相独立。

### 消息确认、重连和运行限制

- 使用单消费者、有限接收 credit；样本处理成功后才发送 ACCEPTED。
- 未绑定设备、不相关事件/服务会确认并忽略；日志给出固定原因。
- 非法格式或数值发送 REJECTED，避免坏消息不断重试；日志不输出原始载荷或密钥。
- 数据库、缓冲容量等处理失败会发送 RELEASED，关闭连接并退避重连（1–30 秒），请求云端重新投递。
- 使用通知 `request_id`，或 AMQP 消息 ID，结合设备、产品、全部 12 个属性及上报/通知时间做进程内去重；
  最多缓存 4096 个标识、保留 300 秒。无标识的消息不去重。
- 消息确认只代表已写入**内存**或按上述规则处理，不代表持久化。
  重启会丢失样本和去重记录，不能保证跨重启恰好一次投递。
- 缓冲默认保留 300 秒、每设备 6000 条、最多 128 台设备；最近上报时间按分钟节流写入 MySQL。
- 设备解绑会清理该设备缓冲。在线状态仍由近期样本推断，不是云端在线事件。

启动后日志中 `event=amqp.connected` 表示 TLS、认证及队列接收链路已建立；
`event=amqp.disconnected` / `event=amqp.reconnecting` 表示正在重连。
调试样本写入可设置 `LOG_LEVEL=DEBUG` 查看 `event=telemetry.accepted`。
连接失败时先运行 `--check-amqp`，检查网络、凭证、队列、实例 ID、CA 和系统时间。

启动步骤：先完成设备绑定并启用云端规则，然后执行 `.venv/bin/python run.py`，让设备上报，
使用登录 Cookie 查询 `/api/devices/{本地设备ID}/telemetry/latest` 或 `window`。
也可打开 `/test-ui/`，在“实时监测”选择设备查看最新值、姿态和曲线；原始云端通知通过 `/api/telemetry/reports` 查询。
当前页面使用 WebSocket 推送监测数据；原始报文 HTTP 接口继续保留。实时连接及告警接口见 [WebSocket 与告警](docs/WebSocket与告警.md)。
离线消息与查询集成测试执行：`.venv/bin/python -B -m unittest discover -s tests -v`。

参考：[华为云 AMQP 接入](https://support.huaweicloud.com/usermanual-iothub/iot_01_00100_2.html)、
[属性通知](https://support.huaweicloud.com/api-iothub/iot_06_v5_01202.html)、
[消息通知](https://support.huaweicloud.com/intl/zh-cn/api-iothub/iot_06_v5_01205.html)、
[Proton 同步接收 API](https://qpid.apache.org/releases/qpid-proton-0.40.0/proton/python/docs/proton.utils.html)。

## 六轴数据处理

处理顺序为：数据校验 → 按设备保存原始样本 → 读取最新值或时间窗口 → 计算统计量或显示滤波 → 返回前端。各阶段通过内部 `SensorSample` 对接，不依赖华为云原始报文格式。

查询接口使用用户登录 Cookie，与 AMQP 的云端接入凭证分开。仅允许用户读取自己的设备；未登录返回 `401`，设备不存在或归属其他用户返回 `404`。

| HTTP 接口 | 用途 |
| --- | --- |
| `GET /api/telemetry/reports` | 当前登录用户全部绑定设备的最新值和最近云通知，按设备分组，测试页面每秒查询 |
| `GET /api/telemetry/steps/daily` | 当前登录用户的 MySQL 历史每日步数，合并所有设备，默认近 30 天 |
| `GET /api/devices/{id}/telemetry/latest` | 最新六轴样本及合加速度、合角速度，供数值仪表盘使用 |
| `GET /api/devices/{id}/telemetry/window?seconds=60` | 最近窗口样本，供六条曲线、当前窗口回放和前端导出使用 |
| `GET /api/devices/{id}/telemetry/summary?seconds=60` | 原始六轴数据的最大值、最小值、平均值、波动幅度 |
| `GET /api/devices/{id}/telemetry/status` | 根据最近上报推断设备状态，返回最后接收时间及数据库上报时间 |

`seconds` 为正整数，不超过配置的保留时间，默认值为 `60` 与保留时间中的较小值。按后端接收次序返回样本，窗口按单调时间计算，不受系统时钟调整影响。UTC 时间字段用于显示。

### 用户和设备报文隔离

后端根据签名会话识别登录用户，查询 MySQL 中该用户的绑定设备。前端无需提交 `user_id`，也不能通过查询参数改变查询用户。云通知中的 `notify_data.header.device_id` 映射到本地唯一的 `devices.cloud_device_id`，样本与报文都以本地设备 ID 分开保存；同一用户的多台设备不会共用一份最新值或报文列表。

`GET /api/telemetry/reports` 的 `data` 包含 `user_id`、`devices`、`max_reports_per_device` 和 `retention_seconds`。每个设备包含 `device_id`、`cloud_device_id`、`display_name`、`status`、`latest` 和 `reports`。每条报文包含接收序号 `sequence`、本地设备 ID `device_id`、UTC 接收时间 `received_at`、格式化 JSON 文本 `raw` 和截断标记 `truncated`。响应设置 `Cache-Control: no-store`。

报文保留原云通知内容，不会将其改为内部 DTO；不过只有通过真实属性和时间校验且绑定有效的通知会被缓存。`raw` 是解析后重新格式化的 JSON，并非网络字节的逐字复制。每台设备最多保留最近 50 条，保留时间与 `TELEMETRY_RETENTION_SECONDS` 一致，默认五分钟；单条预览最多 16 KiB，保存的报文文本总量最多 8 MiB，超过容量时清理较早报文。截断会在页面注明。缓存只存在于当前进程，重启或解绑会清除；高频上报时页面可能跳过已经淘汰的报文，不能用作完整历史存储。

### 最新数值与曲线

最新样本附加 `accel_magnitude` 和 `gyro_magnitude`，分别计算三轴向量的欧氏长度，单位与对应原始轴一致。加速度包含设备上报中的重力分量，不能直接当作人体运动加速度。没有样本时 `latest` 的 `data` 返回 `null`，窗口返回 `count=0`、`samples=[]`。

`latest`、`window.samples[]` 和报文接口中的每个设备 `latest` 都包含全部 12 个属性，以及 `reported_at`、`cloud_event_at`、`received_at`。其中 `step_increment` 是该条上报的增量，不是当前设备累计步数；`is_sedentary/is_fall` 是设备标记，保持整数 `0/1`。

曲线可选择显示滤波：

```text
GET /api/devices/1/telemetry/window?seconds=60&filter=moving_average&filter_window=5
```

`filter` 默认为 `none`，另支持 `moving_average`。`filter_window` 为 `1–50` 的整数，默认 `5`。每条输出使用当前及此前最多 N 条窗口内样本的平均值；窗口开始处使用实际已有样本数。只对六个原始轴滤波，姿态角、步数增量、久坐和摔倒标记及时间保持原值。滤波会减弱噪声，也会延迟和削弱突变，不用于代替原始信号或未经验证的告警判断。

### 统计摘要

统计结果结构如下：

```json
{
  "device_id": 1,
  "seconds": 60,
  "basis": "raw",
  "count": 2,
  "metrics": {
    "accel_x": {"min": 0.1, "max": 0.3, "avg": 0.2, "range": 0.2}
  }
}
```

实际 `metrics` 包含全部六个轴，`range=max-min`。平均值为样本算术平均，不是按时间加权的平均。无样本时 `count=0`，各轴指标为 `null`，避免将无数据显示为零。有限输入计算出的向量长度或极差若超出浮点表示范围，相关指标返回 `null`，不会输出非法的无穷大 JSON。

### 设备状态

默认最新样本年龄不超过 30 秒时返回 `online`，超过阈值且缓冲仍有样本时返回 `offline`。没有当前进程样本（例如尚未上报、重启或缓冲已过期）返回 `unknown`；数据库中的历史 `last_seen_at` 仍可展示，但不据此宣称实时在线。状态为近期上报推断，`basis=recent_reports`，不是云平台在线事件。

### 步数、久坐、摔倒与姿态

真实设备已上报 `step_increment`、`is_sedentary`、`is_fall`，后端完成类型校验、缓存、查询和页面展示。步数现已按用户和北京时间日期累计，并定时保存到 MySQL；久坐和摔倒标记按设备的 0→1 变化生成告警事件，持续为 1 不重复生成，恢复为 0 后允许再次触发。事件及状态保存到本地工作文件，并推送给设备所属用户；设备负责人仍需确认步数增量覆盖范围和重发消息 ID。

#### 每日步数历史

`daily_steps` 仅包含 `user_id`、`steps`、`record_date` 三列，主键为 `(user_id, record_date)`。归属从数据库绑定关系取得，不接受设备报文中的 `user_id`；同一用户多台设备的 `step_increment` 相加。日期采用设备服务 `event_time` 转换后的北京时间日期，迟到报文补入对应日期。解绑保留已经累计和保存的历史。

正常运行 `.venv/bin/python run.py` 会同时启动 AMQP 接收和步数定时任务。默认每天北京时间 **22:00** 首次保存当天截至该时刻的总数；22:00 至午夜收到的增量继续累计，跨日后补齐到当天记录，迟到报文和失败写入每 30 秒检查重试。程序错过定时点时，下一次正常启动会补写已结束日期。没有设备报文的日期不生成记录；当天有有效报文但增量全为零时，保存零步数。

为了在“每日定时更新 MySQL”的前提下保留重启前的累计值，接收端在确认 AMQP 消息前提交本地 SQLite 工作记录。正增量与消息去重标识在同一事务中保存；无稳定消息 ID 时仍会累计，但无法保证重投去重。MySQL 写入使用累计总数覆盖，不做再次相加，因此写入成功但结果未知时可安全重试。本地工作文件需要保留并随数据备份；删除它会丢失待保存的累计值及去重状态。当前方案沿用单进程运行方式；多个进程或不同机器使用独立工作文件会产生不一致的累计结果。

查询示例（需先登录，携带同源 Cookie）：

```http
GET /api/telemetry/steps/daily?start_date=2026-09-14&end_date=2026-10-13
```

起止日期均包含在结果内，最多 366 天，必须使用 `YYYY-MM-DD`；省略时默认结束日期为北京时间今天、开始日期为此前第 29 天。返回 `data.user_id`、`timezone`、起止日期和按日期排序的 `points`，每个点包含 `date`、`steps`、`has_data`。未保存日期返回 `steps: null, has_data: false`，已记录零步数返回 `steps: 0, has_data: true`。接口只读取 MySQL，22:00 前尚未导出的当天累计值不会提前作为历史返回。当前前端的“历史步数”页面已接入此接口。

若后续由后端计算，步数需要重力处理、峰值检测与最小步间隔；久坐需要可靠的活动状态及持续时间；摔倒需要突变、姿态变化和后续静止的组合判断，并用佩戴位置对应的数据校准阈值。单纯静止不等于久坐，合加速度突变也不等于摔倒。当前不运行上述设备判断算法，只依据设备已经报告的标记生成告警事件。

姿态解算需要明确采样时间和算法，通常结合加速度与陀螺仪；直接积分角速度会积累漂移。六轴 IMU 不提供可靠的绝对航向参考，当前仅透传设备提供的 `roll/pitch/yaw`，不计算姿态角。

这些接口不会主动生成数据。暂时没有真实设备时，可使用华为云设备调试功能触发属性上报，或运行离线接入测试；曲线页面和 WebSocket 已接入，告警可按游标补取。

### 实现与性能约定

- 高频接收只查询设备内部 ID 和用户 ID，设备归属仍从数据库实时解析，不使用可能过期的跨请求归属缓存。
- 查询设备权限后，从缓冲获取线程安全的样本快照；处理与序列化在缓冲锁外进行。接收、解绑及按用户查询报文共用生命周期锁，保持绑定关系和报文快照一致；其他六轴查询不占用该锁。
- 短窗口从最新样本向前读取，到达窗口边界即停止，返回时仍按接收顺序排列。
- 滑动平均使用增量累计，并定期重新求和；发生严重抵消或溢出时重新计算或回退到缩放均值，原始样本不修改。
- 数据库和缓冲的整数配置共用 `src/common/config.py` 校验。查询参数拒绝重复提交，密码 DTO 的字符串表示不包含密码字段。

六轴短窗口统计的计算结果仍受浮点舍入影响。性能收益尚未通过基准测试量化；每日步数使用整数独立累计。

## 数据库交互与并发

`DBControl` 在每个实例中按需初始化 MySQL 连接池。数据库操作从池中获取连接，结束后归还；写入成功提交事务，异常时回滚。用户名、邮箱与云设备 ID 的唯一性由数据库约束保证，设备修改和解绑同时使用设备 ID 与用户 ID 限制操作范围。

用户资料修改复用当前请求鉴权时读取的用户记录，更新语句仍校验会话版本，密码修改还校验原密码哈希。设备接口鉴权仅查询用户 ID 与会话版本。设备重命名在一个事务中锁定所属设备记录、检查名称并按需更新，避免重复借还连接；名称未变化时不执行更新，非本人或不存在的设备仍返回 `404`。

连接池耗尽或连接中断、超时时，接口返回 `503`，具体原因写入服务端日志。SQL 或数据库配置错误返回服务器错误。写入期间连接中断时提交结果可能未知，应用不会自动重试写入，前端应先查询确认结果。

MySQL 保存用户、设备资料与每日步数。高频传感样本仍使用内存缓冲；步数持久化工作记录与表结构见 [数据库设计说明](iot_database_design.md)。

## 调试日志

日志模块位于 [src/common/logging.py](src/common/logging.py)，使用 Python 标准库，不增加依赖。应用创建时自动配置日志并创建日志目录，应用日志同时输出到控制台和 UTF-8 文件。使用 `run.py` 时会自动加载 `.env` 中的日志配置；使用 Flask CLI 时需手动导出。

默认文件为 `logs/app-YYYY-MM-DD.log`，例如 `logs/app-2026-10-11.log`。
文件名按北京时间（`Asia/Shanghai`）的记录日期生成，同一天多次启动追加到同一个文件，
跨午夜后的新记录自动写入新日期的文件。进程 ID 保留在每条日志中。
不再按大小拆分或生成 `.log.1` 等备份，旧的 `LOG_MAX_BYTES` / `LOG_BACKUP_COUNT` 配置不再使用。
历史日期的日志保留，需按实际需要归档或清理；应用不会在启动时删除日志。

日志使用 UTC 时间，例如下面是一条示意记录：

```text
2026-10-03T06:00:00.123Z INFO src.http pid=1234 request_id=... user_id=3 event=http.request method=GET route=/api/devices status=200 duration_ms=2.1
```

HTTP `4xx` 使用 `WARNING`，`5xx` 使用 `ERROR`。业务记录包括注册、登录、退出、资料更新、密码修改、设备绑定、重命名和解绑；只记录操作结果与内部编号，不记录用户名、邮箱、显示名称、云端密钥或请求正文。异常堆栈保留文件、行号、函数名、异常类型和数据库错误码，省略源码行、局部变量及可能包含业务值的异常消息。数据库回滚、连接归还等异常也使用同一日志配置。

本地调试可在启动前设置：

```bash
export LOG_LEVEL=DEBUG
export LOG_DIR=logs
```

运行后先列出日志文件，再按北京时间的实际日期查看：

```bash
ls logs/
tail -f logs/app-2026-10-11.log
```

前端反馈问题时可附带响应头 `X-Request-ID`，后端按该 ID 搜索对应日志。新增模块使用 `logging.getLogger(__name__)` 获取日志器，其名称位于 `src` 下时自动继承配置。

本配置仅管理应用的 `src` 日志命名空间，Flask 开发服务器和第三方库保留各自的日志配置。默认 `logs/` 与 `.env` 已加入 Git 忽略规则。

## 模拟前端测试

[tests/test_backend.py](tests/test_backend.py) 使用 Flask 测试客户端携带会话 Cookie 发起请求；[tests/test_amqp.py](tests/test_amqp.py) 使用真实通知样例验证 AMQP 配置、全部属性和时间校验、旧格式退出、消息确认与重试、包含活动字段的去重和前端查询权限；[tests/test_reports.py](tests/test_reports.py) 验证两个用户和三台设备的报文隔离、解绑清理、会话失效、缓存容量，以及最新值和滤波窗口完整保留活动字段。离线测试无需启动前端、HTTP 服务器或真实 AMQP 服务。

不连接真实数据库时执行 AMQP 配置、报文解析、确认/重试/去重、模拟数据到前端查询的集成测试及日志测试；MySQL 用例会跳过：

```bash
.venv/bin/python -B -m unittest discover -s tests -v
```

安装 Node.js 后，可额外验证前端切换用户、退出、会话失效及延迟响应不会显示旧报文：

```bash
node tests/test_report_monitor.cjs
```

浏览器页面运行本身不需要安装 Node.js。

本机 MySQL 集成测试使用 `root` 和 `/var/run/mysqld/mysqld.sock`，需要本机账号允许 socket 登录并具备临时建库权限：

```bash
IOTDA_TEST_MYSQL=1 .venv/bin/python -B -m unittest discover -s tests -v
```

数据库测试根据 `mysql_schema.sql` 创建随机命名的 `iotda_simulation_*` 临时库，所有写入只发生在临时库，结束后删除。对现有 `iot_visualization` 数据及自增序列仅作读取对照。日志测试输出到临时目录。

覆盖注册登录、重复用户名和邮箱、资料修改、密码修改后的会话失效、设备绑定与归属校验、重命名和解绑、并发注册及密码修改、连接池耗尽恢复、故障响应、跨域响应头、请求体限制、日志关联、同日追加及跨日切换。此测试不覆盖浏览器自身的 Cookie/CORS 行为或真实设备经华为云到后端的端到端联调。

2026-10-03 模拟测试：20 项全部通过。发现并修复异常调用栈输出源码行可能泄漏敏感字面量的问题。测试中的 Cookie 签名时钟基于单调时间推进，避免执行环境时钟回拨造成误判；后端会话校验规则未放宽。临时数据库已删除，原项目数据及自增序列对照一致。
