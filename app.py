from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from sqlalchemy import text
import os
import threading
import time
import pymysql

# 确保pymysql可以被SQLAlchemy作为mysqldb使用
pymysql.install_as_MySQLdb()

# 初始化应用
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'

# 使用绝对路径配置上传文件夹，确保文件持久化存储
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['UPLOAD_FOLDER'] = os.path.join(basedir, 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max-limit

# --- 数据库配置 (请根据实际情况修改) ---
# 格式: mysql+pymysql://用户名:密码@主机地址:端口/数据库名
# 假设您的MySQL用户名是 root，密码是 123456，数据库是 Auction
# 您可能需要根据您的真实配置修改这里的密码
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:12345@localhost/Auction'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*") 
login_manager = LoginManager(app)
login_manager.login_view = 'login'

@socketio.on('connect')
def handle_connect():
    if current_user.is_authenticated:
        join_room(f"user_{current_user.id}")
        print(f"User {current_user.username} (ID: {current_user.id}) joined room user_{current_user.id}")

# --- 全局 Context Processor ---
@app.context_processor
def inject_pending_count():
    if current_user.is_authenticated and current_user.role == 'admin':
        try:
            count = Item.query.filter_by(status='pending').count()
            return dict(pending_count=count)
        except:
            return dict(pending_count=0)
    return dict(pending_count=0)

# --- 数据库模型 (与 schema.sql 对应) ---

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    # schema.sql 中字段名为 password_hash
    password_hash = db.Column(db.String(128), nullable=False) 
    role = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

class Item(db.Model):
    __tablename__ = 'items'
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    start_price = db.Column(db.Numeric(10, 2), nullable=False)
    current_price = db.Column(db.Numeric(10, 2), nullable=False)
    increment = db.Column(db.Numeric(10, 2), default=10.0)
    start_time = db.Column(db.DateTime, default=datetime.now)
    end_time = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='pending') 
    rejection_reason = db.Column(db.String(255), nullable=True) # 拒绝理由
    highest_bidder_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
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

# --- 辅助函数 ---

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def check_auctions():
    """后台任务：检查拍卖状态"""
    while True:
        try:
            with app.app_context():
                now = datetime.now()
                
                # 1. 检查已到期的 'active' 拍卖 -> 'ended'
                expired_items = Item.query.filter(Item.status == 'active', Item.end_time <= now).all()
                for item in expired_items:
                    item.status = 'ended'
                    db.session.commit()
                    winner_name = item.highest_bidder.username if item.highest_bidder else '无人出价'
                    socketio.emit('auction_ended', {'item_id': item.id, 'winner': winner_name}, room=f"item_{item.id}")

                # 2. 检查已到开拍时间的 'approved' 拍卖 -> 'active'
                starting_items = Item.query.filter(Item.status == 'approved', Item.start_time <= now).all()
                for item in starting_items:
                    item.status = 'active'
                    db.session.commit()
                    # 可选择通知首页刷新，或在该 Item 的房间里广播
                    print(f"Auction {item.id} started automatically at {now}")

        except Exception as e:
            print(f"Check auction error: {e}")
        time.sleep(10) 

# --- 路由 ---

@app.route('/')
@login_required
def index():
    try:
        # 分类获取不同状态的拍卖物品
        active_items = Item.query.filter_by(status='active').order_by(Item.end_time).all()
        upcoming_items = Item.query.filter_by(status='approved').order_by(Item.start_time).all()
        ended_items = Item.query.filter_by(status='ended').order_by(Item.end_time.desc()).limit(12).all() # 限制显示最近结束的12个
        
        return render_template('index.html', 
                             active_items=active_items, 
                             upcoming_items=upcoming_items, 
                             ended_items=ended_items)
    except Exception as e:
        return f"<h3>数据库连接失败</h3><p>请检查 app.py 中的数据库密码配置。</p><p>错误详情: {e}</p>"

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        # 注意：实际生产中应使用 werkzeug.security.check_password_hash
        # 这里为了演示，直接比对（数据库里存明文）
        if user and user.password_hash == password:
            login_user(user)
            return redirect(url_for('index'))
        flash('用户名或密码错误')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        role = request.form.get('role')
        
        if password != confirm_password:
             flash('两次输入的密码不一致')
        elif User.query.filter_by(username=username).first():
            flash('用户名已存在')
        elif role not in ['buyer', 'seller']:
             flash('无效的角色选择')
        else:
            # 同样，这里存入 password_hash 字段的是明文，正式项目请加密
            new_user = User(username=username, password_hash=password, role=role)
            db.session.add(new_user)
            db.session.commit()
            login_user(new_user)
            return redirect(url_for('index'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/publish', methods=['GET', 'POST'])
@login_required
def publish():
    if current_user.role != 'seller':
        flash('只有卖家可以发布商品')
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        start_price = float(request.form.get('start_price'))
        duration = int(request.form.get('duration')) 
        
        start_time_str = request.form.get('start_time')
        if start_time_str:
            try:
                # datetime-local format: YYYY-MM-DDTHH:MM
                start_time = datetime.strptime(start_time_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                # Fallback if format is wrong
                start_time = datetime.now()
        else:
            start_time = datetime.now()

        end_time = start_time + timedelta(minutes=duration)
        
        new_item = Item(
            seller_id=current_user.id,
            name=name,
            description=description,
            start_price=start_price,
            current_price=start_price,
            start_time=start_time,
            end_time=end_time,
            status='pending' 
        )
        db.session.add(new_item)
        db.session.flush() # 获取 new_item.id

        # 处理图片上传
        files = request.files.getlist('images')
        for file in files:
            if file and file.filename:
                filename = secure_filename(file.filename)
                # 防止重名
                unique_filename = f"{int(time.time())}_{filename}"
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                
                # 确保目录存在
                if not os.path.exists(app.config['UPLOAD_FOLDER']):
                    os.makedirs(app.config['UPLOAD_FOLDER'])
                    
                file.save(file_path)
                
                # 存相对路径到数据库 (注意 path separator)
                db_image_path = f"uploads/{unique_filename}"
                new_image = ItemImage(item_id=new_item.id, image_url=db_image_path)
                db.session.add(new_image)

        db.session.commit()
        
        # 通知管理员有新审核
        socketio.emit('new_pending_item', {
            'msg': f'新拍品待审核: {new_item.name} (卖家: {current_user.username})'
        }, room='admin_room')
        
        flash('拍品已提交，等待管理员审核')
        return redirect(url_for('index'))
        
    return render_template('publish.html')

@app.route('/item/<int:item_id>')
@login_required
def item_detail(item_id):
    item = Item.query.get_or_404(item_id)
    return render_template('item_detail.html', item=item)

@app.route('/admin')
@login_required
def admin_dashboard():
    if current_user.role != 'admin':
        flash('权限不足')
        return redirect(url_for('index'))
    pending_items = Item.query.filter_by(status='pending').all()
    # 获取正在进行或即将开始的拍卖，以便管理员管理
    active_items = Item.query.filter(Item.status.in_(['active', 'approved'])).order_by(Item.start_time).all()
    # 获取已结束的拍卖
    ended_items = Item.query.filter_by(status='ended').order_by(Item.end_time.desc()).all()
    return render_template('admin_dashboard.html', items=pending_items, active_items=active_items, ended_items=ended_items)

@app.route('/approve/<int:item_id>')
@login_required
def approve_item(item_id):
    if current_user.role != 'admin':
        return redirect(url_for('index'))
    item = Item.query.get_or_404(item_id)
    
    # Check if this is a future scheduled item
    if item.start_time > datetime.now():
        item.status = 'approved' # Waiting for start time
        flash(f'已批准。拍卖将于 {item.start_time.strftime("%Y-%m-%d %H:%M")} 自动开始')
    else:
        # If immediate or time passed, start now
        item.status = 'active'
        # Reset start time to now to ensure full duration (if desirable for delayed approval)
        # Assuming if user wanted 8:00 but admin approves 9:00, we shift to 9:00
        original_duration = item.end_time - item.start_time
        if original_duration.total_seconds() < 60:
             original_duration = timedelta(hours=1)
        item.start_time = datetime.now()
        item.end_time = item.start_time + original_duration
        flash('已批准并立即开拍')
    
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/reject/<int:item_id>', methods=['POST'])
@login_required
def reject_item(item_id):
    if current_user.role != 'admin':
        return redirect(url_for('index'))
    
    item = Item.query.get_or_404(item_id)
    reason = request.form.get('reason', '')
    
    item.status = 'rejected'
    item.rejection_reason = reason
    db.session.commit()
    
    # Notify seller via SocketIO
    socketio.emit('auction_rejected', {
        'item_name': item.name,
        'reason': reason,
        'msg': f'您的拍品 "{item.name}" 已被拒绝。理由: {reason}'
    }, room=f"user_{item.seller_id}")
    
    flash('已拒绝并在卖家端发送通知')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/stop/<int:item_id>')
@login_required
def stop_auction(item_id):
    """管理员强制停止拍卖"""
    if current_user.role != 'admin':
        return redirect(url_for('index'))
    item = Item.query.get_or_404(item_id)
    
    # 允许停止 active 或 approved 状态的商品
    if item.status in ['active', 'approved']:
        item.status = 'rejected' # 或者 'stopped'
        db.session.commit()
        
        # 如果正在进行，通知房间内用户
        socketio.emit('error', {'msg': '管理员已强制终止此拍卖'}, room=f"item_{item.id}")
        socketio.emit('auction_ended', {'item_id': item.id, 'winner': '管理员终止'}, room=f"item_{item.id}")
        
        flash(f'已强制停止拍品: {item.name}')
    else:
        flash('该拍品当前状态无法停止')
        
    return redirect(url_for('admin_dashboard'))

# --- SocketIO 事件 ---

@socketio.on('join_check')
def on_join_check(data):
    """前端连接后发送此事件，用于加入特定权限房间"""
    if current_user.is_authenticated and current_user.role == 'admin':
        join_room('admin_room')

@socketio.on('join')
def on_join(data):
    room = data['room']
    join_room(room)

@socketio.on('bid')
def on_bid(data):
    if not current_user.is_authenticated:
        return
        
    item_id = data['item_id']
    amount = float(data['amount'])
    
    item = Item.query.get(item_id)
    
    if not item or item.status != 'active':
        return
        
    if datetime.now() > item.end_time:
        item.status = 'ended'
        db.session.commit()
        emit('error', {'msg': '拍卖已结束'}, room=f"item_{item_id}")
        return

    # Decimal 转 float 比较
    current_price_float = float(item.current_price)
    increment_float = float(item.increment)
    start_price_float = float(item.start_price)

    min_bid = current_price_float + (increment_float if item.highest_bidder else 0)
    if item.highest_bidder is None:
        min_bid = start_price_float
    else:
        min_bid = current_price_float + increment_float

    if amount < min_bid:
        emit('error', {'msg': f'出价必须高于 {min_bid}'}, room=request.sid)
        return

    # 防狙击
    time_left = item.end_time - datetime.now()
    extended = False
    if time_left < timedelta(minutes=3):
        item.end_time += timedelta(minutes=5)
        extended = True

    item.current_price = amount
    item.highest_bidder_id = current_user.id
    
    new_bid = Bid(item_id=item.id, user_id=current_user.id, amount=amount)
    db.session.add(new_bid)
    db.session.commit()
    
    response = {
        'new_price': amount,
        'bidder_name': current_user.username,
        'new_end_time': item.end_time.isoformat(), 
        'extended': extended
    }
    emit('price_update', response, room=f"item_{item_id}")

# --- 启动 ---

if __name__ == '__main__':
    with app.app_context():
        # Ensure tables exist
        db.create_all()
        
        # 尝试自动迁移添加 rejection_reason 字段 (如果不存在)
        try:
            db.session.execute(text("ALTER TABLE items ADD COLUMN rejection_reason VARCHAR(255)"))
            db.session.commit()
            print(">>> 成功添加 rejection_reason 字段")
        except Exception as e:
            # 忽略错误，假设字段已存在
            print(f">>> 尝试添加字段跳过 (可能已存在): {e}")

        try:
            # 尝试创建一个默认管理员，防止数据库是空的
            if not User.query.filter_by(username='admin').first():
                admin = User(username='admin', password_hash='123', role='admin')
                db.session.add(admin)
                db.session.commit()
                print(">>> 检测到数据库中没有管理员，已自动创建: admin / 123")
        except Exception as e:
            # 捕获连接错误打印出来，不中断主进程，但用户必须处理
            print(f">>> 连接数据库失败或查询出错: {e}")
            print(">>> 请检查 app.py 中的 SQLALCHEMY_DATABASE_URI配置，并确保MySQL服务已运行")

    bg_thread = threading.Thread(target=check_auctions)
    bg_thread.daemon = True
    bg_thread.start()
    
    # host='0.0.0.0' 使其他设备可访问
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
