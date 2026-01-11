# Copilot Instructions — AuctionSystem (Flask + Socket.IO)

本项目是基于 Flask、Flask-SocketIO 与 MySQL 的实时在线拍卖系统。以下规则帮助 AI 代理快速理解架构、遵循现有约定并高效实现变更。

## 大局观与组件边界
- 入口与装配：`app.py#create_app()` 初始化 `Flask`、`SQLAlchemy`、`SocketIO`、`LoginManager`，注册路由与事件：
  - 视图路由：`views.register_views(app)`
  - 拍卖事件：`events.register_events(socketio)`（出价、房间管理、结果广播）
  - 私信系统：`chat.register_chat_routes(app)` 与 `chat.register_chat_events(socketio)`
  - 后台任务：线程启动 `tasks.check_auctions(app)`，每 10s 扫描状态流转
- 数据访问：统一使用 `SQLAlchemy` 模型于 `models.py`，查询封装在 `query.py`
- 系统通知：`services.send_system_message()` 以“管理员”身份写入私信并推送 Socket 事件

## 数据模型与业务状态
- 金额类型：使用 `Decimal` 与 `db.Numeric(10, 2)`；禁止使用浮点参与业务比较/累计
- 核心表：`User`、`Item`、`Bid`、`ItemImage`、`Post`、`ChatSession`、`Message`、`Appeal`（见 `models.py`/`schema.sql`）
- 拍卖状态：`pending → approved → active → ended`，管理员强制下架为 `stopped`，驳回为 `rejected`
- 支付/物流：`payment_status`：`unpaid|paid|timeout_cancelled`；`shipping_status`：`unshipped|shipped|received`
- 订单号：`tasks.check_auctions` 在成交时生成 `ORDyyyyMMddHHmmss####`

## WebSocket 与房间约定（Socket.IO）
- 用户房间：`user_{user_id}`；拍品房间：`item_{item_id}`
- 聊天房间：`chat_item_{item_id}_{min(uid1,uid2)}_{max(uid1,uid2)}`（见 `chat.py`）
- 事件：
  - 出价：`events.on_bid` 校验登录/封禁/连出价/最小加价，防狙击（最后3分钟≥第3笔出价自动延时5分钟）。广播 `price_update`
  - 结果：后台任务或管理员动作广播 `auction_ended`、`auction_result_toast`
  - 私信：`send_message` 持久化 `Message`、维护未读计数，并发 `new_message` + `new_chat_notification`
- 给指定用户推送：`socketio.emit('event', data, room=f"user_{uid}")`

## 视图与工作流（关键路径）
- 登录/注册：`/login` `/register`（演示用：明文储存于 `password_hash` 字段）
- 发布与审核：卖家 `/publish` → 管理员 `/admin/audit` 审核（通过设为 `approved`，到时自动 `active`；立即开拍则重置时长）
- 竞拍：`/item/<id>` 页面订阅 `item_{id}`；出价经 `events.on_bid`
- 成交与支付：后台任务将 `active` 到期置为 `ended`；买家 `/item/<id>/pay` 生成模拟支付二维码，`/confirm_payment` 标记 `paid`
- 物流：卖家 `/ship`，买家 `/confirm_receipt`
- 申诉：卖家 `/item/<id>/appeal`；管理员 `/admin/appeals` 审批

## 查询与复用模式
- 首页与列表：使用 `query.py` 聚合查询（示例：`get_index_items`/`get_seller_items`/`get_buyer_won_items`/`get_appeal_list`）
- 搜索：统一通过 JOIN + `or_` 模式在 `query.py` 实现，避免在视图中拼装复杂查询

## 约定与易踩坑
- 金额/加价幅度：视图中用 `Decimal(...).quantize(Decimal('0.01'))`，事件中保持 `Decimal` 比较；JSON 返回再转 `float`
- 自动迁移：`app.py` 启动时用 `ALTER TABLE` 尝试新增字段（幂等）。新增字段需：更新 `models.py` + 可选在 `app.py` 增加对应 `ALTER`
- 上传：图片与头像写入 `static/uploads`，数据库保存相对路径 `uploads/...`；用 `secure_filename` + 时间戳去重
- 管理通知：尽量用 `services.send_system_message()`，它会创建/复用 `ChatSession`、维护未读数并发 Socket 提醒
- 登录明文仅用于演示，变更前请注意所有相关比较逻辑（`views.login()`）

## 本地运行与调试
- 修改数据库连接：`app.config['SQLALCHEMY_DATABASE_URI']`（`app.py`；与 README 示例密码可能不同，以代码为准）
- 依赖安装：
  ```bash
  pip install -r requirements.txt
  pip install qrcode[pil]
  ```
- 启动：
  ```bash
  python app.py
  ```
  首次启动会 `db.create_all()` 并尝试创建默认管理员 `admin/123`
- 局域网访问：`socketio.run(..., host='0.0.0.0', port=5000)` 已启用；Windows 防火墙需放行
- 注意：工作区存在 VS Code `dotnet` 构建任务，但本项目为 Python/Flask 服务，与 .NET 无关

## 扩展示例（片段）
- 广播管理员通知给卖家：
  ```python
  socketio.emit('auction_approved', {'item_name': item.name}, room=f"user_{item.seller_id}")
  ```
- 新增查询：在 `query.py` 中封装函数，视图调用，保持视图简洁

参考文件：`app.py`、`models.py`、`events.py`、`chat.py`、`services.py`、`query.py`、`templates/` 与 `schema.sql`。遵循以上约定可避免金钱精度、房间命名、状态流转与通知一致性问题。