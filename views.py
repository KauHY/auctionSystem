from flask import render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, login_required, logout_user, current_user
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import os
import time
from sqlalchemy import text
from extensions import db, socketio
from models import User, Item, ItemImage, Post, Bid
import query
from services import send_system_message

def register_views(app):

    # --- 全局 Context Processor ---
    @app.context_processor
    def inject_global_vars():
        context = {}
        if current_user.is_authenticated:
            # 管理员审核计数
            if current_user.role == 'admin':
                try:
                    count = Item.query.filter_by(status='pending').count()
                    context['pending_count'] = count
                except:
                    context['pending_count'] = 0
            
            # 未读私信计数
            try:
                # 需在函数内部导入避免循环依赖，或者假设 models 已加载
                from models import ChatSession
                from sqlalchemy import or_
                
                # 计算我作为 buyer 的未读 + 我作为 seller 的未读
                unread = 0
                buyer_sessions = ChatSession.query.filter_by(buyer_id=current_user.id).filter(ChatSession.buyer_unread > 0).count()
                seller_sessions = ChatSession.query.filter_by(seller_id=current_user.id).filter(ChatSession.seller_unread > 0).count()
                context['unread_chats_count'] = buyer_sessions + seller_sessions
            except:
                context['unread_chats_count'] = 0
                
        return context

    @app.route('/')
    @login_required
    def index():
        try:
            search_q = request.args.get('q', '')
            
            # 使用 query 模块获取数据，传入 Model 类以避免循环导入
            active_items, upcoming_items, ended_items = query.get_index_items(Item, User, search_q)
            
            # 搜索卖家
            matched_sellers = query.get_search_users(User, search_q) if search_q else []

            return render_template('index.html', 
                                active_items=active_items, 
                                upcoming_items=upcoming_items, 
                                ended_items=ended_items,
                                matched_sellers=matched_sellers,
                                search_query=search_q)
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
            phone = request.form.get('phone') # 获取电话
            role = request.form.get('role')
            
            if password != confirm_password:
                flash('两次输入的密码不一致')
            elif User.query.filter_by(username=username).first():
                flash('用户名已存在')
            elif role not in ['buyer', 'seller']:
                flash('无效的角色选择')
            else:
                # 同样，这里存入 password_hash 字段的是明文，正式项目请加密
                new_user = User(username=username, password_hash=password, role=role, phone=phone)
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
            start_price_val = request.form.get('start_price')
            increment_val = request.form.get('increment', '10')
            duration_val = request.form.get('duration')

            if not name or not description or not start_price_val or not duration_val:
                flash('请填写所有必填字段（名称、描述、起拍价、时长）')
                return redirect(request.url)

            try:
                start_price = float(start_price_val)
                increment = float(increment_val)
                duration = int(duration_val)
            except ValueError:
                flash('价格或时长格式无效')
                return redirect(request.url) 
            
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
            
            # 验证图片上传
            files = request.files.getlist('images')
            if not files or not any(f.filename for f in files):
                flash('请至少上传一张商品图片')
                return redirect(request.url)

            new_item = Item(
                seller_id=current_user.id,
                name=name,
                description=description,
                start_price=start_price,
                current_price=start_price,
                increment=increment,
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

    @app.route('/my_auctions')
    @login_required
    def my_auctions():
        if current_user.role != 'seller':
            flash('只有卖家可以查看发布历史')
            return redirect(url_for('index'))
        
        search_q = request.args.get('q', '')
        # 移至 query.py
        my_items = query.get_seller_items(Item, User, current_user.id, search_q)
        return render_template('my_auctions.html', items=my_items, search_query=search_q)

    @app.route('/my_orders')
    @login_required
    def my_orders():
        search_q = request.args.get('q', '')
        # 移至 query.py
        orders = query.get_buyer_won_items(Item, User, current_user.id, search_q)
        return render_template('my_orders.html', items=orders, search_query=search_q)

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
        
        # 移至 query.py
        pending_items, active_items, ended_items = query.get_admin_dashboard_items(Item)

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
        
        # Notify seller via SocketIO
        msg_content = f'您的拍品 "{item.name}" 已通过审核并上架！'
        socketio.emit('auction_approved', {
            'item_name': item.name,
            'msg': msg_content
        }, room=f"user_{item.seller_id}")
        
        # 发送系统私信
        send_system_message(item.id, item.seller_id, msg_content)
        
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
        msg_content = f'您的拍品 "{item.name}" 已被拒绝。理由: {reason}'
        socketio.emit('auction_rejected', {
            'item_name': item.name,
            'reason': reason,
            'msg': msg_content
        }, room=f"user_{item.seller_id}")
        
        # 发送系统私信
        send_system_message(item.id, item.seller_id, msg_content)
        
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
            
            # 发送系统私信给卖家
            send_system_message(item.id, item.seller_id, f'您的拍品 "{item.name}" 已被管理员强制终止。')
            
            flash(f'已强制停止拍品: {item.name}')
        else:
            flash('该拍品当前状态无法停止')
            
        return redirect(url_for('admin_dashboard'))

    @app.route('/user/<int:user_id>')
    @login_required
    def user_profile(user_id):
        user = User.query.get_or_404(user_id)
        posts = query.get_user_posts(Post, user_id)
        
        # 获取公开的商品 (如果是卖家)
        public_items = []
        if user.role == 'seller':
            public_items = query.get_user_public_items(Item, user_id)
            
        return render_template('user_profile.html', user=user, posts=posts, items=public_items)

    @app.route('/update_avatar', methods=['POST'])
    @login_required
    def update_avatar():
        if 'avatar' not in request.files:
            flash('没有选择文件')
            return redirect(url_for('user_profile', user_id=current_user.id))
        
        file = request.files['avatar']
        if file.filename == '':
            flash('没有选择文件')
            return redirect(url_for('user_profile', user_id=current_user.id))
            
        if file:
            filename = secure_filename(file.filename)
            # Add timestamp to filename
            extension = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
            if not extension:
                extension = 'jpg' # Default fallback
            unique_filename = f"avatar_{current_user.id}_{int(time.time())}.{extension}"
            
            file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(file_path)
            
            # Update user avatar
            user = User.query.get(current_user.id)
            user.avatar = unique_filename
            db.session.commit()
            
            flash('头像更新成功')
            return redirect(url_for('user_profile', user_id=current_user.id))

    @app.route('/post/create', methods=['POST'])
    @login_required
    def create_post():
        content = request.form.get('content')
        if not content:
            flash('内容不能为空')
            return redirect(url_for('user_profile', user_id=current_user.id))
            
        new_post = Post(user_id=current_user.id, content=content)
        db.session.add(new_post)
        db.session.commit()
        
        flash('动态发布成功')
        return redirect(url_for('user_profile', user_id=current_user.id))
