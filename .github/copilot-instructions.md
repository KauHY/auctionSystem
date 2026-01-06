# Copilot Instructions for auctionSystem_sun

These instructions make AI coding agents productive quickly in this Flask + Socket.IO + MySQL auction platform.

## Big Picture
- Architecture: Flask app with SQLAlchemy + Socket.IO + Login. Core modules: `app.py` (bootstrap), `extensions.py` (shared instances), `models.py` (ORM), `views.py` (HTTP routes), `events.py` (auction realtime), `chat.py` (DM inbox + events), `services.py` (system notifications), `tasks.py` (background scheduler), `query.py` (query helpers), `templates/` (UI), `static/uploads/` (images).
- Realtime model: Socket.IO rooms for targeted broadcast:
  - `user_{id}` per-user notifications, `item_{id}` per-auction room, `admin_room` for admin notices.
- Data flow: HTTP routes render pages and perform CRUD, realtime events update prices and notify users, `tasks.check_auctions()` advances auction states and enforces payment rules; `services.send_system_message()` writes chat session notifications and emits alerts.

## Developer Workflow
- Configure DB in `app.py` (`SQLALCHEMY_DATABASE_URI`), default is MySQL `Auction`.
- Install deps: `pip install -r requirements.txt` and `pip install qrcode[pil]`.
- Run: `python app.py` (creates tables, performs lightweight migrations, starts Socket.IO server).
- First-run admin: username `admin`, password `123`.
- Image uploads persist under `static/uploads/` with timestamped filenames; templates reference `uploads/<name>`.

## Key Conventions
- Route registration: functions `register_views(app)`, `register_chat_routes(app)` called in `create_app()`; add new endpoints by extending these registrars instead of creating blueprints.
- Event registration: `register_events(socketio)` and `register_chat_events(socketio)`; follow existing event names and payload shapes.
- Room naming: emit to `user_{user_id}`, `item_{item_id}`, or `admin_room` for audience targeting.
- Status fields:
  - `items.status`: `pending` → `approved` → `active` → `ended` (or `rejected`).
  - `items.payment_status`: `unpaid`, `paid`, `timeout_cancelled`.
- Auto migrations: startup in `app.py` runs `ALTER TABLE ...` via `sqlalchemy.text()` guarded by try/except. Prefer adding new columns here in the same style and updating `models.py` accordingly; also reflect in `schema.sql`.
- Queries: use `query.py` helpers for filtered lists and search (seller/buyer dashboards, index sections) to avoid duplicating ORM filters in views.
- Auth and roles: protect endpoints with `login_required`; enforce role checks (`admin`, `seller`, `buyer`) in views before actions.
- Passwords: current implementation compares `password_hash` as cleartext (demo). Preserve behavior unless explicitly changing auth.

## Realtime Patterns
- Bidding (`events.py`): validate user auth, ban window (`users.banned_until`), prevent consecutive bids by same user, enforce min bid (`current_price` + `increment`), anti-sniping: extend `end_time` by 5 minutes when there are ≥3 bids in last 3 minutes.
- Chat (`chat.py`): join `join_chat`, send messages via `send_message`; update `ChatSession.last_message` and unread counters, notify receiver on `user_{receiver_id}`.
- System notifications (`services.py`): create/update `ChatSession` between admin and user for item-specific notices, increment unread, emit toast notification.

## Background Scheduler
- `tasks.check_auctions(app)`: runs every ~10s.
  - Transitions `approved` → `active` at `start_time`.
  - Ends `active` auctions at `end_time`; generates `order_hash` like `ORDYYYYMMDDHHMMSS####` and emits `auction_ended`.
  - Enforces payment: after 24h unpaid, sets `payment_status=timeout_cancelled`, bans buyer for 30 days (`users.banned_until`), notifies parties.

## Payment Flow
- In `views.py`: `pay_item()` saves shipping info, generates base64 PNG QR codes for mock WeChat/Alipay, then `confirm_payment()` sets `payment_status='paid'` and notifies seller via `send_system_message()`.

## Adding Features (Examples)
- New DB column: add `db.session.execute(text("ALTER TABLE <table> ADD COLUMN <col> ..."))` in `app.py` within a try/except block; update `models.py`, then adjust queries/templates.
- New Socket.IO event: define in `events.py` or `chat.py`, join appropriate room(s), emit payloads consistent with existing handlers (e.g., `{ 'msg': ..., 'item_id': ... }`).
- New filtered list: add helper to `query.py` and reuse in views to keep filtering logic centralized.

## Integration Touchpoints
- Templates rely on `@app.context_processor` in `views.py` to inject `pending_count` (admin) and `unread_chats_count` for badges.
- File storage path comes from `app.config['UPLOAD_FOLDER']` set in `create_app()`.
- Socket.IO is initialized in `extensions.py` with permissive CORS; server started via `socketio.run()` in `app.py`.

Keep changes minimal and consistent with these patterns. When touching auctions, ensure updates propagate: models → query helpers → views/templates → events → scheduler.