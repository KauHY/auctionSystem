from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import os
import threading
import time
import pymysql

# 确保pymysql可以被SQLAlchemy作为mysqldb使用
pymysql.install_as_MySQLdb()

# 初始化应用
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max-limit

# --- 数据库配置 (请根据实际情况修改) ---
# 格式: mysql+pymysql://用户名:密码@主机地址:端口/数据库名
# 假设您的MySQL用户名是 root，密码是 123456，数据库是 Auction
# 您可能需要根据您的真实配置修改这里的密码
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:123456@localhost/Auction'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*") 
login_manager = LoginManager(app)
login_manager.login_view = 'login'

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
    """后台任务：检查拍卖是否结束"""
    while True:
        try:
            with app.app_context():
                now = datetime.now()
                # 查找已到期且仍在进行中的拍卖
                expired_items = Item.query.filter(Item.status == 'active', Item.end_time <= now).all()
                for item in expired_items:
                    item.status = 'ended'
                    db.session.commit()
                    # 通知房间内的用户拍卖结束
                    winner_name = item.highest_bidder.username if item.highest_bidder else '无人出价'
                    socketio.emit('auction_ended', {'item_id': item.id, 'winner': winner_name}, room=f"item_{item.id}")
        except Exception as e:
            print(f"Check auction error: {e}")
        time.sleep(10) 

# --- 路由 ---

@app.route('/')
def index():
    try:
        items = Item.query.filter_by(status='active').all()
        return render_template('index.html', items=items)
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
        role = request.form.get('role')
        if User.query.filter_by(username=username).first():
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
        
        end_time = datetime.now() + timedelta(minutes=duration)
        
        new_item = Item(
            seller_id=current_user.id,
            name=name,
            description=description,
            start_price=start_price,
            current_price=start_price,
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
        flash('拍品已提交，等待管理员审核')
        return redirect(url_for('index'))
        
    return render_template('publish.html')

@app.route('/item/<int:item_id>')
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
    return render_template('admin_dashboard.html', items=pending_items)

@app.route('/approve/<int:item_id>')
@login_required
def approve_item(item_id):
    if current_user.role != 'admin':
        return redirect(url_for('index'))
    item = Item.query.get_or_404(item_id)
    item.status = 'active'
    
    # 重新计算结束时间
    original_duration = item.end_time - item.start_time
    if original_duration.total_seconds() < 60:
         original_duration = timedelta(hours=1)
         
    item.start_time = datetime.now()
    item.end_time = item.start_time + original_duration
    
    db.session.commit()
    flash('已批准上架')
    return redirect(url_for('admin_dashboard'))

@app.route('/reject/<int:item_id>')
@login_required
def reject_item(item_id):
    if current_user.role != 'admin':
        return redirect(url_for('index'))
    item = Item.query.get_or_404(item_id)
    item.status = 'rejected'
    db.session.commit()
    flash('已拒绝')
    return redirect(url_for('admin_dashboard'))

# --- SocketIO 事件 ---

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
