from extensions import db
from flask_login import UserMixin
from datetime import datetime
from decimal import Decimal

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    # schema.sql 中字段名为 password_hash
    password_hash = db.Column(db.String(128), nullable=False) 
    role = db.Column(db.String(20), nullable=False)
    phone = db.Column(db.String(20), nullable=True) # 新增：联系电话
    avatar = db.Column(db.String(200), nullable=True) # 新增：用户头像文件名
    banned_until = db.Column(db.DateTime, nullable=True) # 新增：封禁截止时间
    # 实名认证
    real_name = db.Column(db.String(80), nullable=True)
    id_card = db.Column(db.String(20), nullable=True)
    is_verified = db.Column(db.Boolean, default=False)
    verified_at = db.Column(db.DateTime, nullable=True)
    # 钱包
    wallet_balance = db.Column(db.Numeric(10, 2), default=Decimal('0.00'))
    wallet_frozen = db.Column(db.Numeric(10, 2), default=Decimal('0.00'))
    created_at = db.Column(db.DateTime, default=datetime.now)

class Item(db.Model):
    __tablename__ = 'items'
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    start_price = db.Column(db.Numeric(10, 2), nullable=False)
    current_price = db.Column(db.Numeric(10, 2), nullable=False)
    increment = db.Column(db.Numeric(10, 2), default=Decimal('10.00'))
    start_time = db.Column(db.DateTime, default=datetime.now)
    end_time = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='pending') 
    rejection_reason = db.Column(db.String(255), nullable=True) # 拒绝理由
    appeal_reason = db.Column(db.Text, nullable=True) # 申诉理由
    appeal_status = db.Column(db.String(20), nullable=True) # pending, resolved, rejected
    highest_bidder_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    order_hash = db.Column(db.String(64), nullable=True) # 订单哈希 (SHA256 hex digest is 64 chars)
    
    # 支付与物流
    payment_status = db.Column(db.String(20), default='unpaid') # unpaid, paid
    tracking_number = db.Column(db.String(100), nullable=True) # 快递单号
    shipping_status = db.Column(db.String(20), default='unshipped') # unshipped, shipped, received
    shipping_name = db.Column(db.String(80), nullable=True)
    shipping_phone = db.Column(db.String(20), nullable=True)
    shipping_address = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.now)

    seller = db.relationship('User', foreign_keys=[seller_id])
    highest_bidder = db.relationship('User', foreign_keys=[highest_bidder_id])
    images = db.relationship('ItemImage', backref='item', lazy=True)

class Bid(db.Model):
    __tablename__ = 'bids'
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('items.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.now)

    item = db.relationship('Item')
    user = db.relationship('User')

class ItemImage(db.Model):
    __tablename__ = 'item_images'
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('items.id'), nullable=False)
    image_url = db.Column(db.String(255), nullable=False)
    is_primary = db.Column(db.Boolean, default=False)

class Post(db.Model):
    __tablename__ = 'posts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    user = db.relationship('User', backref=db.backref('posts', lazy=True))

class ChatSession(db.Model):
    __tablename__ = 'chat_sessions'
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('items.id'), nullable=False)
    buyer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    last_message = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    # 简单的未读计数
    buyer_unread = db.Column(db.Integer, default=0)
    seller_unread = db.Column(db.Integer, default=0)
    
    # Relationships
    item = db.relationship('Item')
    buyer = db.relationship('User', foreign_keys=[buyer_id])
    seller = db.relationship('User', foreign_keys=[seller_id])

class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    chat_session_id = db.Column(db.Integer, db.ForeignKey('chat_sessions.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.now)

    session = db.relationship('ChatSession', backref=db.backref('messages', lazy=True))
    sender = db.relationship('User')

class Appeal(db.Model):
    __tablename__ = 'appeals'
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('items.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False) # 申诉人(卖家)
    content = db.Column(db.Text, nullable=False) # 申诉理由
    status = db.Column(db.String(20), default='pending') # pending, approved, rejected
    rejection_reason_snapshot = db.Column(db.String(255), nullable=True) # 申诉时的下架理由作为快照
    admin_reply = db.Column(db.Text, nullable=True) # 管理员回复/驳回理由
    created_at = db.Column(db.DateTime, default=datetime.now)
    handled_at = db.Column(db.DateTime, nullable=True) # 处理时间

    item = db.relationship('Item', backref=db.backref('appeals', lazy=True, order_by="desc(Appeal.created_at)"))
    user = db.relationship('User', backref=db.backref('my_appeals', lazy=True))

class Deposit(db.Model):
    __tablename__ = 'deposits'
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('items.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    status = db.Column(db.String(20), default='frozen')  # frozen, applied, refunded, forfeited
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    item = db.relationship('Item')
    user = db.relationship('User')

class WalletTransaction(db.Model):
    __tablename__ = 'wallet_transactions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey('items.id'), nullable=True)
    type = db.Column(db.String(30), nullable=False)  # recharge, deposit, refund, payment, forfeit
    direction = db.Column(db.String(10), nullable=False)  # credit, debit
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    balance_after = db.Column(db.Numeric(10, 2), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    user = db.relationship('User')
    item = db.relationship('Item')
