# 记账机器人 (jizhang)

一个可部署到 [Railway](https://railway.app) 的 Telegram 自动记账机器人。  
将消息转发给机器人，它会自动识别金额并完成记账；每天/每月定时推送汇总报表。

---

## 功能

| 功能 | 说明 |
|------|------|
| 转发自动记账 | 转发消息 → 自动提取金额 → 识别来源 → 入账 |
| 多金额候选 | 发现多个候选金额时弹出按钮让用户选择 |
| 幂等防重复 | 同一条原始消息重复转发只记一次 |
| 每日报表 | 00:00 推送"昨日入账统计"（总额 + 分人 + 笔数） |
| 每月报表 | 每月 1 日 00:00 同时推送"上月入账排行" |
| 今日统计 | 私聊内联按钮可查看"今日入账统计"（总额 + 分人 + 分项目） |
| 关键词别名 | 管理员可将名称关键词绑定到用户 ID |
| 权限控制 | 管理员白名单；可选用户/群白名单；添加用户仅可查看/记录/清空自己的记账 |
| 全流程内联交互 | 私聊内通过内联按钮完成统计、权限、关键词绑定与清理等流程 |

---

## 快速开始

### 本地运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量（复制示例后编辑）
cp .env.example .env
# 编辑 .env，至少填写 BOT_TOKEN、ADMIN_IDS、WEBHOOK_BASE_URL

# 3. 加载环境变量并启动
export $(grep -v '^#' .env | xargs)
python bot.py
```

### 部署到 Railway

1. 在 Railway 创建新项目，关联此仓库。
2. 在 **Variables** 面板设置以下环境变量（至少填写带 `*` 的项）：

   | 变量 | 说明 |
   |------|------|
   | `BOT_TOKEN` * | BotFather 给出的 Bot Token |
   | `ADMIN_IDS` * | 管理员的 Telegram 用户 ID（逗号分隔） |
   | `WEBHOOK_BASE_URL` * | 机器人公网地址（例如 `https://<你的域名>`；Railway 可用 `https://${RAILWAY_PUBLIC_DOMAIN}`） |
   | `REPORT_CHAT_ID` | 接收每日/每月报表的 chat ID（0=不推送） |
   | `ALLOWED_USER_IDS` | 允许使用的用户 ID 白名单（空=不限制） |
   | `ALLOWED_CHAT_IDS` | 允许使用的群组 ID 白名单（空=不限制） |
   | `DATABASE_URL` | PostgreSQL 连接串（如 `postgresql://postgres@host:5432/db`，也会自动识别 `DATABASE_PRIVATE_URL` / `DATABASE_PUBLIC_URL`） |
   | `TZ` | 时区（默认 `Asia/Shanghai`） |
   | `WEBHOOK_PATH` | Webhook 路径（默认 `/telegram/webhook`） |
   | `WEBHOOK_SECRET_TOKEN` | Telegram webhook 请求校验密钥（建议设置） |
   | `DEFAULT_PROJECT_NAME` | 未识别到项目时使用的默认项目名（默认 `默认项目`） |

   可直接复制到 Railway Variables 的清单（按当前项目环境）：

   ```env
   BOT_TOKEN=替换为你的TelegramBotToken
   ADMIN_IDS=123456789
   WEBHOOK_BASE_URL=https://${RAILWAY_PUBLIC_DOMAIN}
   REPORT_CHAT_ID=0
   ALLOWED_USER_IDS=
   ALLOWED_CHAT_IDS=
   DATABASE_URL=postgresql://postgres@localhost:5432/jizhang
   TZ=Asia/Shanghai
   WEBHOOK_PATH=/telegram/webhook
   WEBHOOK_SECRET_TOKEN=替换为随机长字符串
   DEFAULT_PROJECT_NAME=默认项目
   ```

3. 在 Railway 中为项目绑定 PostgreSQL 服务，并确保 `DATABASE_URL` 已注入。

4. Railway 会自动检测 `Procfile` 并以 `python bot.py` 启动 web 进程。  
   启动后机器人会自动注册 webhook，无需轮询模式。

### 环境变量详解（按当前代码）

| 变量 | 是否必填 | 默认值 | 示例 | 说明 |
|------|----------|--------|------|------|
| `BOT_TOKEN` | 是 | 无 | `123456:ABC...` | Telegram 机器人令牌，缺失会直接启动失败。 |
| `ADMIN_IDS` | 建议 | 空 | `123456789,987654321` | 管理员用户 ID 列表（逗号分隔）。 |
| `ALLOWED_USER_IDS` | 否 | 空 | `123456789` | 用户白名单；空表示不限制。 |
| `ALLOWED_CHAT_IDS` | 否 | 空 | `-1001234567890` | 群聊白名单；空表示不限制。 |
| `REPORT_CHAT_ID` | 否 | `0` | `-1001234567890` | 日报/月报推送目标；`0` 表示关闭推送。 |
| `DATABASE_URL` | 建议 | 自动回退 | `postgresql://postgres@host:5432/db` | 主数据库连接串；优先级最高。 |
| `DATABASE_PRIVATE_URL` | 否 | 空 | `postgresql://...` | 当 `DATABASE_URL` 缺失时可作为回退。 |
| `DATABASE_PUBLIC_URL` | 否 | 空 | `postgresql://...` | 当上面两项缺失时可作为回退。 |
| `POSTGRES_URL` | 否 | 空 | `postgresql://...` | 兼容旧变量名，继续回退。 |
| `POSTGRESQL_URL` | 否 | 空 | `postgresql://...` | 最后一个数据库变量回退项。 |
| `TZ` | 否 | `Asia/Shanghai` | `Asia/Shanghai` | 时区配置（影响日期统计与定时任务）。 |
| `RAILWAY_PUBLIC_DOMAIN` | 否 | 空 | `xxx.up.railway.app` | Railway 自动注入；用于推导默认 `WEBHOOK_BASE_URL`。 |
| `WEBHOOK_BASE_URL` | 是（生产） | 若有域名则自动推导，否则空 | `https://xxx.up.railway.app` | Webhook 公网基地址；生产环境建议显式设置。 |
| `WEBHOOK_PATH` | 否 | `/telegram/webhook` | `/telegram/webhook` | Webhook 路径；若未以 `/` 开头会自动补齐。 |
| `WEBHOOK_SECRET_TOKEN` | 否（建议） | 空 | `a-long-random-token` | Telegram webhook 请求校验密钥。 |
| `WEBHOOK_LISTEN` | 否 | `0.0.0.0` | `0.0.0.0` | Web 服务器监听地址。 |
| `PORT` | 否 | `8080`（优先于 `WEBHOOK_PORT`） | `8080` | Railway 通常自动注入；作为 webhook 服务端口。 |
| `WEBHOOK_PORT` | 否 | `8080`（仅当 `PORT` 缺失时生效） | `8080` | 本地或自托管时可手动指定端口。 |
| `DEFAULT_PROJECT_NAME` | 否 | `默认项目` | `默认项目` | 无法识别项目时写入的项目名。 |

> 数据库连接串最终解析顺序：`DATABASE_URL` → `DATABASE_PRIVATE_URL` → `DATABASE_PUBLIC_URL` → `POSTGRES_URL` → `POSTGRESQL_URL` → `postgresql://localhost/jizhang`。

---

## 命令入口

| 命令 | 权限 | 说明 |
|------|------|------|
| `/start` | 所有人 | 打开主菜单，后续全部通过内联按钮交互 |

> 管理员在私聊发送 `/start` 后，可通过内联按钮完成：用户关键词绑定、项目关键词绑定、统计查看、可用用户权限增删查、清理用户记账等全部流程。  
> 非管理员（被添加用户）在私聊可绑定/查看自己的项目关键词、清空自己的记账，并且统计仅展示自己的记账数据。

---

## 记账逻辑

### 金额识别

从消息文本中提取所有数字，过滤掉：

- 时间（`14:30`、`08:00:00`）
- 日期（`2024-01-15`、`2024年1月15日`）
- 手机号（`138xxxxxxxx`）
- 订单号 / 长数字串（≥11 位）
- 百分比（`5.5%`）

### 来源识别优先级

1. 转发来源用户的 **Telegram 用户 ID**（最可靠）
2. 转发来源的 **显示名称**
3. 通过 `/bindid` 绑定的 **关键词别名**

### 项目识别优先级

1. 文本中的显式项目标记：`#项目名` / `项目:项目名` / `项目A 100`
2. 通过 `/bindproject` 绑定的关键词命中
3. 默认项目名（`DEFAULT_PROJECT_NAME`）

### 幂等防重复

每条转发消息根据其原始来源（用户 ID + 转发时间，或频道 + 消息 ID）生成唯一哈希，  
数据库设有唯一索引；重复转发将被提示跳过，不会二次入账。

---

## 项目结构

```
jizhang/
├── bot.py           # 主程序：Bot 实例、命令、消息处理、定时任务
├── config.py        # 从环境变量读取配置
├── db.py            # 异步 PostgreSQL 数据库层（asyncpg）
├── parser.py        # 金额提取与噪声过滤
├── requirements.txt
├── Procfile         # Railway web 进程定义
├── railway.json     # Railway 部署配置
└── .env.example     # 环境变量示例
```

---

## 开发

```bash
pip install -r requirements.txt
# 运行单元测试（如有）
python -m pytest tests/ -v
```
