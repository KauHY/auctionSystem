from flask import render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, login_required, logout_user, current_user
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from decimal import Decimal, ROUND_HALF_UP
import os
import time
from sqlalchemy import text
from extensions import db, socketio
from models import User, Item, ItemImage, Post, Bid
import query
from services import send_system_message

import qrcode
from io import BytesIO
import base64

def register_views(app):
    # 保证金计算：分层额度
    def compute_deposit_amount(item: Item) -> Decimal:
        sp = Decimal(item.start_price)
        if sp <= Decimal('999'):
            amt = min(sp, Decimal('20'))
        elif sp <= Decimal('9999'):
            amt = Decimal('100')
        else:
            amt = (sp * Decimal('0.01'))
            if amt > Decimal('1000'):
                amt = Decimal('1000')
        return amt.quantize(Decimal('0.01'))

    # --- 全局 Context Processor ---
    @app.context_processor
    def inject_global_vars():
        context = {}
        if current_user.is_authenticated:
            # 管理员审核计数
            if current_user.role == 'admin':
                try:
                    from models import Appeal
                    item_count = Item.query.filter_by(status='pending').count()
                    appeal_count = Appeal.query.filter_by(status='pending').count()
                    context['pending_count'] = item_count + appeal_count
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
                
        # 资金类型与方向映射供模板使用
        def wallet_type_label(t: str) -> str:
            mapping = {
                'recharge': '充值',
                'deposit': '保证金缴纳',
                'refund': '保证金退款',
                'payment': '订单支付',
                'forfeit': '保证金没收',
                'payout': '卖家入账',
            }
            return mapping.get(t, t or '')

        def wallet_direction_label(d: str) -> str:
            return '入账' if d == 'credit' else ('扣款' if d == 'debit' else (d or ''))

        def wallet_type_badge(t: str) -> str:
            classes = {
                'recharge': 'bg-primary',
                'deposit': 'bg-warning text-dark',
                'refund': 'bg-success',
                'payment': 'bg-danger',
                'forfeit': 'bg-dark',
                'payout': 'bg-success',
            }
            return classes.get(t, 'bg-secondary')

        context['wallet_type_label'] = wallet_type_label
        context['wallet_direction_label'] = wallet_direction_label
        context['wallet_type_badge'] = wallet_type_badge

        return context

    @app.route('/')
    @login_required
    def index():
        try:
            search_q = request.args.get('q', '')
            category = request.args.get('category', '')
            
            # 使用 query 模块获取数据，传入 Model 类以避免循环导入
            active_items, upcoming_items, ended_items = query.get_index_items(Item, User, search_q, category)
            
            # 搜索卖家
            matched_sellers = query.get_search_users(User, search_q) if search_q else []

            # 分类列表
            categories = [
                '二手数码产品', '宠物用品', '户外装备', '动漫潮玩 & 手办盲盒',
                '健身器材', '生活日用品', '高价值收藏品', '摄影与电子设备',
                '服饰鞋包', '美妆个护', '图书音像', '乐器设备',
                '家居装饰', '母婴用品', '汽车/骑行周边', '虚拟物品/服务类'
            ]

            return render_template('index.html', 
                                active_items=active_items, 
                                upcoming_items=upcoming_items, 
                                ended_items=ended_items,
                                matched_sellers=matched_sellers,
                                search_query=search_q,
                                current_category=category,
                                categories=categories)
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
            email = request.form.get('email') # 获取邮箱
            role = request.form.get('role')
            
            import re
            email_pattern = r'^[\w\.-]+@[\w\.-]+\.\w+$'

            if password != confirm_password:
                flash('两次输入的密码不一致')
            elif email and not re.match(email_pattern, email):
                flash('邮箱格式不正确')
            elif User.query.filter_by(username=username).first():
                flash('用户名已存在')
            elif role not in ['buyer', 'seller']:
                flash('无效的角色选择')
            else:
                # 同样，这里存入 password_hash 字段的是明文，正式项目请加密
                new_user = User(username=username, password_hash=password, role=role, email=email)
                db.session.add(new_user)
                db.session.commit()
                login_user(new_user)
                return redirect(url_for('index'))
        return render_template('register.html')

    @app.route('/verify', methods=['GET', 'POST'])
    @login_required
    def verify_identity():
        if current_user.role == 'admin':
            flash('管理员无需实名认证')
            return redirect(url_for('index'))

        if request.method == 'POST':
            real_name = request.form.get('real_name', '').strip()
            id_card = request.form.get('id_card', '').strip()

            if not real_name or not id_card:
                flash('请填写真实姓名和身份证号')
                return render_template('verify.html')

            import re
            if not re.match(r'^\d{17}[\dXx]$', id_card):
                flash('身份证号格式不正确')
                return render_template('verify.html')

            existing = User.query.filter_by(id_card=id_card, role=current_user.role, is_verified=True).first()
            if existing and existing.id != current_user.id:
                flash('该身份证号在当前角色已完成实名认证，无法重复认证')
                return render_template('verify.html')

            user = User.query.get(current_user.id)
            user.real_name = real_name
            user.id_card = id_card
            user.is_verified = True
            user.verified_at = datetime.now()
            db.session.commit()
            flash('实名认证成功')
            return redirect(url_for('index'))

        return render_template('verify.html')

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        return redirect(url_for('index'))

    @app.route('/publish', methods=['GET', 'POST'])
    @login_required
    def publish():
        # 分类列表 (保持与 index 一致)
        categories = [
            '二手数码产品', '宠物用品', '户外装备', '动漫潮玩 & 手办盲盒',
            '健身器材', '生活日用品', '高价值收藏品', '摄影与电子设备',
            '服饰鞋包', '美妆个护', '图书音像', '乐器设备',
            '家居装饰', '母婴用品', '汽车/骑行周边', '虚拟物品/服务类'
        ]

        if current_user.role != 'seller':
            flash('只有卖家可以发布商品')
            return redirect(url_for('index'))
        if not getattr(current_user, 'is_verified', False):
            flash('您尚未完成实名认证。<a href="' + url_for('verify_identity') + '" class="btn btn-sm btn-primary ms-2">现在去实名</a> <button type="button" class="btn btn-sm btn-secondary ms-2" data-bs-dismiss="alert">明白了，稍后再去</button>')
            return redirect(url_for('verify_identity'))
        # 封禁期间禁止缴纳保证金
        if getattr(current_user, 'banned_until', None) and current_user.banned_until > datetime.now():
            flash(f'由于未付款记录，您的账户已被封禁至 {current_user.banned_until.strftime("%Y-%m-%d %H:%M")}，暂无法缴纳保证金或参与拍卖。')
            return redirect(url_for('item_detail', item_id=item_id))
        
        if request.method == 'POST':
            name = request.form.get('name')
            description = request.form.get('description')
            category = request.form.get('category') # 获取分类
            start_price_val = request.form.get('start_price')
            increment_val = request.form.get('increment', '10')
            duration_val = request.form.get('duration')

            if not name or not description or not start_price_val or not duration_val:
                flash('请填写所有必填字段（名称、描述、起拍价、时长）')
                return redirect(request.url)

            try:
                # 强制保留两位小数，通过 ROUND_HALF_UP 四舍五入，防止浮点数精度问题
                start_price = Decimal(start_price_val).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                increment = Decimal(increment_val).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
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
                category=category, # 保存分类
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
                'msg': f'新拍品待审核: {new_item.name} (卖家: {current_user.username})',
                'type': 'audit'
            }, room='admin_room')
            
            flash('拍品已提交，等待管理员审核')
            return redirect(url_for('index'))
            
        return render_template('publish.html', categories=categories)

    @app.route('/my_auctions')
    @login_required
    def my_auctions():
        if current_user.role != 'seller':
            flash('只有卖家可以查看发布历史')
            return redirect(url_for('index'))
        if not getattr(current_user, 'is_verified', False):
            flash('您尚未完成实名认证。<a href="' + url_for('verify_identity') + '" class="btn btn-sm btn-primary ms-2">现在去实名</a> <button type="button" class="btn btn-sm btn-secondary ms-2" data-bs-dismiss="alert">明白了，稍后再去</button>')
            return redirect(url_for('verify_identity'))
        
        search_q = request.args.get('q', '')
        # 移至 query.py
        my_items = query.get_seller_items(Item, User, current_user.id, search_q)
        return render_template('my_auctions.html', items=my_items, search_query=search_q)

    @app.route('/my_orders')
    @login_required
    def my_orders():
        if not getattr(current_user, 'is_verified', False):
            flash('您尚未完成实名认证。<a href="' + url_for('verify_identity') + '" class="btn btn-sm btn-primary ms-2">现在去实名</a> <button type="button" class="btn btn-sm btn-secondary ms-2" data-bs-dismiss="alert">明白了，稍后再去</button>')
            return redirect(url_for('verify_identity'))
        search_q = request.args.get('q', '')
        # 移至 query.py
        orders = query.get_buyer_won_items(Item, User, current_user.id, search_q)
        return render_template('my_orders.html', items=orders, search_query=search_q)

    @app.route('/wallet', methods=['GET', 'POST'])
    @login_required
    def wallet():
        if request.method == 'POST':
            amount_str = request.form.get('amount', '0').strip()
            try:
                amount = Decimal(amount_str).quantize(Decimal('0.01'))
            except Exception:
                flash('充值金额格式不正确')
                return redirect(url_for('wallet'))
            if amount <= Decimal('0.00'):
                flash('充值金额必须大于 0')
                return redirect(url_for('wallet'))
            
            # 生成支付二维码
            def generate_qr(data):
                qr = qrcode.QRCode(version=1, box_size=10, border=4)
                qr.add_data(data)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                buffered = BytesIO()
                img.save(buffered, format="PNG")
                return base64.b64encode(buffered.getvalue()).decode('utf-8')

            order_no = f"RECHARGE{int(time.time())}{current_user.id}"
            # 模拟支付链接
            qr_wechat = generate_qr(f"wxp://f2f0/{order_no}?amt={amount}")
            qr_alipay = generate_qr(f"https://qr.alipay.com/{order_no}?amt={amount}")
            
            return render_template('recharge_payment.html', amount=amount, qr_wechat=qr_wechat, qr_alipay=qr_alipay)
        # 列出最近交易
        from models import WalletTransaction
        txs = WalletTransaction.query.filter_by(user_id=current_user.id).order_by(WalletTransaction.created_at.desc()).limit(50).all()
        return render_template('wallet.html', balance=Decimal(current_user.wallet_balance), transactions=txs)

    @app.route('/wallet/confirm_recharge', methods=['POST'])
    @login_required
    def confirm_recharge():
        amount_str = request.form.get('amount', '0').strip()
        try:
            amount = Decimal(amount_str).quantize(Decimal('0.01'))
        except Exception:
             flash('支付异常')
             return redirect(url_for('wallet'))
        
        user = User.query.get(current_user.id)
        from models import WalletTransaction
        new_balance = (Decimal(user.wallet_balance) + amount).quantize(Decimal('0.01'))
        user.wallet_balance = new_balance
        tx = WalletTransaction(
            user_id=user.id,
            type='recharge',
            direction='credit',
            amount=amount,
            balance_after=new_balance,
            description='用户充值'
        )
        db.session.add(tx)
        db.session.commit()
        flash(f'充值成功！已到账 ¥{amount}')
        return redirect(url_for('wallet'))

    @app.route('/item/<int:item_id>')
    @login_required
    def item_detail(item_id):
        item = Item.query.get_or_404(item_id)
        deposit_amount = None
        has_deposit = False
        is_banned = False
        if current_user.is_authenticated and current_user.role == 'buyer':
            try:
                from models import Deposit
                deposit_amount = compute_deposit_amount(item)
                dep = Deposit.query.filter_by(item_id=item.id, user_id=current_user.id).filter(Deposit.status.in_(['frozen','applied'])).first()
                has_deposit = dep is not None
            except Exception:
                has_deposit = False
            # 计算封禁状态
            try:
                if getattr(current_user, 'banned_until', None) and current_user.banned_until > datetime.now():
                    is_banned = True
            except Exception:
                is_banned = False
        return render_template('item_detail.html', item=item, deposit_amount=deposit_amount, has_deposit=has_deposit, is_banned=is_banned)

    @app.route('/item/<int:item_id>/deposit', methods=['GET', 'POST'])
    @login_required
    def deposit_item(item_id):
        item = Item.query.get_or_404(item_id)
        if current_user.role != 'buyer':
            flash('仅买家可缴纳保证金')
            return redirect(url_for('item_detail', item_id=item_id))
        if not getattr(current_user, 'is_verified', False):
            flash('您尚未完成实名认证。<a href="' + url_for('verify_identity') + '" class="btn btn-sm btn-primary ms-2">现在去实名</a> <button type="button" class="btn btn-sm btn-secondary ms-2" data-bs-dismiss="alert">明白了，稍后再去</button>')
            return redirect(url_for('verify_identity'))

        from models import Deposit
        deposit_amount = compute_deposit_amount(item)
        existing = Deposit.query.filter_by(item_id=item.id, user_id=current_user.id).filter(Deposit.status.in_(['frozen','applied'])).first()
        if existing:
            flash('您已为该拍品缴纳保证金，无需重复缴纳')
            return redirect(url_for('item_detail', item_id=item_id))

        # 钱包扣款冻结保证金
        user = User.query.get(current_user.id)
        balance = Decimal(user.wallet_balance).quantize(Decimal('0.01'))

        if request.method == 'POST':
            if balance < deposit_amount:
                flash('钱包余额不足，无法冻结保证金。请先充值。')
                return redirect(url_for('wallet'))
            # 扣款（不再使用冻结）
            from models import WalletTransaction
            new_balance = (balance - deposit_amount).quantize(Decimal('0.01'))
            user.wallet_balance = new_balance
            dep = Deposit(item_id=item.id, user_id=current_user.id, amount=deposit_amount, status='frozen')
            db.session.add(dep)
            db.session.add(WalletTransaction(
                user_id=user.id,
                item_id=item.id,
                type='deposit',
                direction='debit',
                amount=deposit_amount,
                balance_after=new_balance,
                description=f'缴纳拍品保证金: {item.name}'
            ))
            db.session.commit()
            flash('若您已缴纳保证金，则最终付款时将无需支付此部分。若竞拍失败，保证金将会降退还给您。')
            return redirect(url_for('item_detail', item_id=item_id))

        return render_template('deposit.html', item=item, amount=deposit_amount, balance=balance)

    @app.route('/item/<int:item_id>/confirm_deposit', methods=['POST'])
    @login_required
    def confirm_deposit(item_id):
        item = Item.query.get_or_404(item_id)
        from models import Deposit
        dep = Deposit.query.filter_by(item_id=item.id, user_id=current_user.id).filter(Deposit.status=='frozen').order_by(Deposit.created_at.desc()).first()
        if not dep:
            flash('未找到待确认的保证金记录')
            return redirect(url_for('deposit_item', item_id=item_id))
        # 钱包流已在缴纳时完成，保持幂等提示一致
        flash('若您已缴纳保证金，则最终付款时将无需支付此部分。若竞拍失败，保证金将会降退还给您。')
        return redirect(url_for('item_detail', item_id=item_id))

    @app.route('/admin')
    @login_required
    def admin_dashboard():
        return redirect(url_for('admin_audit'))

    @app.route('/admin/audit')
    @login_required
    def admin_audit():
        if current_user.role != 'admin':
            flash('权限不足')
            return redirect(url_for('index'))
        
        # 仅获取待审核
        from models import Item, Appeal
        pending_items = Item.query.filter_by(status='pending').order_by(Item.created_at.desc()).all()
        
        # 为了计算 Badge，也需要其他数量 (或者只计算 audit_count)
        # Context Processor 已经有了 global pending_count (sum)
        # 这里特别传 audit_count 和 appeal_pending_count 给 nav_tabs
        audit_count = len(pending_items)
        appeal_pending_count = Appeal.query.filter_by(status='pending').count()

        return render_template('admin/audit.html', 
                               items=pending_items,
                               active_tab='audit',
                               audit_count=audit_count,
                               appeal_pending_count=appeal_pending_count)

    @app.route('/admin/active')
    @login_required
    def admin_active_items():
        if current_user.role != 'admin':
            return redirect(url_for('index'))
        
        # Active and Approved (Upcoming)
        from models import Item, Appeal
        active_items = Item.query.filter(Item.status.in_(['active', 'approved'])).order_by(Item.start_time).all()
        
        # Counts for tabs
        audit_count = Item.query.filter_by(status='pending').count()
        appeal_pending_count = Appeal.query.filter_by(status='pending').count()

        return render_template('admin/active_items.html', 
                               active_items=active_items,
                               active_tab='active_items',
                               audit_count=audit_count,
                               appeal_pending_count=appeal_pending_count)

    @app.route('/admin/wallet_transactions')
    @login_required
    def admin_wallet_transactions():
        if current_user.role != 'admin':
            return redirect(url_for('index'))
        from models import WalletTransaction, User, Appeal
        # Filters
        q_user = request.args.get('user', '').strip()
        q_type = request.args.get('type', '').strip()
        q_start = request.args.get('start', '').strip()
        q_end = request.args.get('end', '').strip()
        page = request.args.get('page', '1')
        per_page = request.args.get('per_page', '50')
        try:
            page = int(page) if str(page).isdigit() else 1
        except:
            page = 1
        try:
            per_page = int(per_page) if str(per_page).isdigit() else 50
        except:
            per_page = 50

        query_tx = WalletTransaction.query
        if q_user:
            if q_user.isdigit():
                query_tx = query_tx.filter(WalletTransaction.user_id == int(q_user))
            else:
                # 模糊匹配用户名
                users = User.query.filter(User.username.like(f"%{q_user}%")).all()
                user_ids = [u.id for u in users]
                if user_ids:
                    query_tx = query_tx.filter(WalletTransaction.user_id.in_(user_ids))
                else:
                    query_tx = query_tx.filter(WalletTransaction.user_id == -1)  # 返回空
        if q_type:
            query_tx = query_tx.filter(WalletTransaction.type == q_type)
        if q_start:
            try:
                start_dt = datetime.strptime(q_start, '%Y-%m-%d')
                query_tx = query_tx.filter(WalletTransaction.created_at >= start_dt)
            except:
                pass
        if q_end:
            try:
                end_dt = datetime.strptime(q_end, '%Y-%m-%d') + timedelta(days=1) - timedelta(seconds=1)
                query_tx = query_tx.filter(WalletTransaction.created_at <= end_dt)
            except:
                pass

        total = query_tx.count()
        query_tx = query_tx.order_by(WalletTransaction.created_at.desc())
        transactions = query_tx.offset((page - 1) * per_page).limit(per_page).all()
        pages = (total + per_page - 1) // per_page if per_page > 0 else 1
        has_prev = page > 1
        has_next = page < pages

        # 复用 admin nav 模板结构
        audit_count = Item.query.filter_by(status='pending').count()
        appeal_pending_count = Appeal.query.filter_by(status='pending').count()
        return render_template(
            'admin/wallet_transactions.html',
            transactions=transactions,
            active_tab='wallet',
            audit_count=audit_count,
            appeal_pending_count=appeal_pending_count,
            # filters
            f_user=q_user,
            f_type=q_type,
            f_start=q_start,
            f_end=q_end,
            per_page=per_page,
            # pagination
            page=page,
            pages=pages,
            total=total,
            has_prev=has_prev,
            has_next=has_next
        )

    @app.route('/admin/appeals')
    @login_required
    def admin_appeals():
        if current_user.role != 'admin':
            return redirect(url_for('index'))
            
        from models import Appeal, Item
        pending_appeals, history_appeals = query.get_appeal_list(Appeal)
        
        # Counts for tabs
        audit_count = Item.query.filter_by(status='pending').count()
        appeal_pending_count = len(pending_appeals)

        return render_template('admin/appeals.html', 
                               appeal_items=pending_appeals,
                               appeal_history=history_appeals,
                               active_tab='appeals',
                               audit_count=audit_count,
                               appeal_pending_count=appeal_pending_count)

    @app.route('/admin/history')
    @login_required
    def admin_history():
        if current_user.role != 'admin':
            return redirect(url_for('index'))
            
        from models import Item, Appeal
        # Ended items (stopped, rejected, ended)
        ended_items = Item.query.filter(Item.status.in_(['ended', 'stopped', 'rejected'])).order_by(Item.end_time.desc()).limit(50).all()
        
        # Counts for tabs
        audit_count = Item.query.filter_by(status='pending').count()
        appeal_pending_count = Appeal.query.filter_by(status='pending').count()

        return render_template('admin/history.html', 
                               ended_items=ended_items,
                               now=datetime.now(),
                               active_tab='history',
                               audit_count=audit_count,
                               appeal_pending_count=appeal_pending_count)

    @app.route('/approve_action/<int:item_id>', methods=['POST'])
    @login_required
    def approve_item_action(item_id):
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
        
        # 发送系统私信 (跳过默认通知，因为已经发送了 auction_approved)
        send_system_message(item.id, item.seller_id, msg_content, skip_notification=True)
        
        return redirect(url_for('admin_audit'))

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
        
        # 发送系统私信 (跳过默认通知，因为已经发送了 auction_rejected)
        send_system_message(item.id, item.seller_id, msg_content, skip_notification=True)
        
        flash('已拒绝并在卖家端发送通知')
        return redirect(url_for('admin_audit'))

    @app.route('/admin/stop/<int:item_id>', methods=['GET', 'POST'])
    @login_required
    def stop_auction(item_id):
        """管理员强制停止拍卖"""
        if current_user.role != 'admin':
            return redirect(url_for('index'))
        
        # 如果是旧的 GET 请求链接，重定向回管理页面并提示
        if request.method == 'GET':
            flash('请刷新页面后，使用“强制下架”按钮填写原因')
            # 兼容性重定向，假设来自活跃列表
            return redirect(url_for('admin_active_items'))
            
        item = Item.query.get_or_404(item_id)
        reason = request.form.get('reason')

        if not reason:
            flash('下架必须填写原因')
            return redirect(url_for('admin_active_items'))
        
        # 允许停止 active 或 approved 状态的商品
        if item.status in ['active', 'approved']:
            item.status = 'stopped' # 强制下架状态
            item.rejection_reason = reason # 下架原因
            db.session.commit()
            
            # 如果正在进行，通知房间内用户
            socketio.emit('error', {'msg': f'管理员已强制终止此拍卖，原因：{reason}'}, room=f"item_{item.id}")
            socketio.emit('auction_ended', {'item_id': item.id, 'winner': '管理员终止'}, room=f"item_{item.id}")
            
            # 生成申诉链接 (指向新的申诉表单页面)
            appeal_url = url_for('submit_appeal', item_id=item.id, _external=True)
            
            # 通知卖家 (Yellow Toast)
            # 使用 HTML <a> 标签包裹链接，配合前端 innerHTML 显示
            msg_content = f'您的拍品 "{item.name}" 已被管理员强制下架。原因：{reason}。如果您对此操作有任何异议，可以<a href="{appeal_url}" class="text-white fw-bold" style="text-decoration: underline;">点击此处</a>进行申诉'
            
            socketio.emit('auction_stopped', {
                'item_name': item.name,
                'reason': reason,
                'msg': msg_content
            }, room=f"user_{item.seller_id}")

            # 发送系统私信给卖家 (跳过通用通知) - 私信中存储完整链接供点击
            chat_msg_content = f'您的拍品 "{item.name}" 已被管理员强制下架。原因：{reason}。如果您对此操作有任何异议，可以点击链接进行申诉: {appeal_url}'
            send_system_message(item.id, item.seller_id, chat_msg_content, skip_notification=True)
            
            flash(f'已强制停止拍品: {item.name}')
        else:
            flash('该拍品当前状态无法停止')
            
        return redirect(url_for('admin_active_items'))

    @app.route('/admin/restore/<int:item_id>', methods=['POST'])
    @login_required
    def restore_auction(item_id):
        """管理员恢复被误操作强制停止的拍卖"""
        if current_user.role != 'admin':
            return redirect(url_for('index'))
            
        item = Item.query.get_or_404(item_id)
        
        if item.status == 'stopped':
            if item.end_time > datetime.now():
                item.status = 'active'
                item.rejection_reason = None 
                
                # Update pending appeals to 'approved' (resolved)
                from models import Appeal
                pending_appeals = Appeal.query.filter_by(item_id=item.id, status='pending').all()
                for appeal in pending_appeals:
                    appeal.status = 'approved'
                    appeal.handled_at = datetime.now()
                    appeal.admin_reply = '管理员主动恢复'

                db.session.commit()
                
                # Notify seller via SocketIO (Green Toast)
                msg_content = f'您的拍品 "{item.name}" 已被管理员恢复上架！'
                socketio.emit('auction_restored', {
                    'item_name': item.name,
                    'msg': msg_content
                }, room=f"user_{item.seller_id}")

                # 发送系统私信 (跳过默认通知)
                send_system_message(item.id, item.seller_id, msg_content, skip_notification=True)

                flash(f'已恢复拍品: {item.name}')
            else:
                flash('该拍品原定结束时间已过，无法恢复')
        else:
            flash('只有被强制下架的拍品才能恢复')
            
        return redirect(url_for('admin_appeals'))

    @app.route('/item/<int:item_id>/appeal', methods=['GET', 'POST'])
    @login_required
    def submit_appeal(item_id):
        item = Item.query.get_or_404(item_id)
        
        if item.seller_id != current_user.id:
            flash('无权操作')
            return redirect(url_for('inbox'))
        if not getattr(current_user, 'is_verified', False):
            flash('您尚未完成实名认证。<a href="' + url_for('verify_identity') + '" class="btn btn-sm btn-primary ms-2">现在去实名</a> <button type="button" class="btn btn-sm btn-secondary ms-2" data-bs-dismiss="alert">明白了，稍后再去</button>')
            return redirect(url_for('verify_identity'))
            
        if item.status != 'stopped':
            flash('该拍品未处于强制下架状态，无需申诉')
            return redirect(url_for('inbox'))
        
        # 处理 GET 请求：显示申诉表单
        if request.method == 'GET':
            return render_template('appeal.html', item=item)
            
        # 处理 POST 请求：提交申诉
        reason = request.form.get('reason')
        if not reason:
            flash('请填写申诉理由')
            return render_template('appeal.html', item=item)
            
        # Create new Appeal record
        from models import Appeal
        new_appeal = Appeal(
            item_id=item.id,
            user_id=current_user.id,
            content=reason,
            status='pending',
            rejection_reason_snapshot=item.rejection_reason
        )
        db.session.add(new_appeal)
        db.session.commit()
        
        flash('申诉提交成功，请等待管理员处理')
        
        # Notify Admin
        socketio.emit('new_pending_item', {
                'msg': f'收到新申诉: {item.name} (卖家: {current_user.username})',
                'type': 'appeal'
            }, room='admin_room')
        
        # 跳转回消息列表 or Chat
        return redirect(url_for('inbox'))

    @app.route('/admin/reject_appeal/<int:item_id>', methods=['POST'])
    @login_required
    def reject_appeal(item_id):
        if current_user.role != 'admin':
            return redirect(url_for('index'))
            
        # item_id is passed, but we should probably reject by appeal_id now?
        # But UI sends item_id currently. Let's find pending appeals for this item.
        # Ideally UI should send appeal_id.
        # Let's support item_id for now to handle "reject all pending appeals for this item" or 
        # modify template to pass appeal_id. modifying template is better.
        
        # But wait, the prompt is to fix bug and support history. 
        # I will update template to iterate appeals, so I should update route to take appeal_id.
        return redirect(url_for('admin_dashboard'))

    @app.route('/admin/reject_appeal_action/<int:appeal_id>', methods=['POST'])
    @login_required
    def reject_appeal_action(appeal_id):
        if current_user.role != 'admin':
            return redirect(url_for('index'))
            
        from models import Appeal
        appeal = Appeal.query.get_or_404(appeal_id)
        
        reason = request.form.get('reason')
        if not reason:
            reason = '申诉被驳回'

        if appeal.status == 'pending':
            appeal.status = 'rejected'
            appeal.handled_at = datetime.now()
            appeal.admin_reply = reason
            
            db.session.commit()
            
            # Notify seller
            item = appeal.item
            msg = f'关于拍品 "{item.name}" 的申诉已被驳回。理由: {reason}。维持下架决定。'
            send_system_message(item.id, item.seller_id, msg)
            socketio.emit('auction_rejected', { 
                'item_name': item.name,
                'reason': reason,
                'msg': msg
            }, room=f"user_{item.seller_id}")
            
            flash('已驳回申诉')
        
        return redirect(url_for('admin_appeals'))

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

    @app.route('/admin/view_identity/<int:user_id>')
    @login_required
    def admin_view_identity(user_id):
        if current_user.role != 'admin':
            flash('权限不足')
            return redirect(url_for('index'))
        user = User.query.get_or_404(user_id)
        return render_template('verify.html', view_only=True, real_name=user.real_name, id_card=user.id_card, is_verified=getattr(user, 'is_verified', False))

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

    @app.route('/item/<int:item_id>/pay', methods=['GET', 'POST'])
    @login_required
    def pay_item(item_id):
        item = Item.query.get_or_404(item_id)
        if not getattr(current_user, 'is_verified', False):
            flash('您尚未完成实名认证。<a href="' + url_for('verify_identity') + '" class="btn btn-sm btn-primary ms-2">现在去实名</a> <button type="button" class="btn btn-sm btn-secondary ms-2" data-bs-dismiss="alert">明白了，稍后再去</button>')
            return redirect(url_for('verify_identity'))
        
        # Security checks
        if item.highest_bidder_id != current_user.id:
            flash('您不是该拍品的获胜者，无法进行支付')
            return redirect(url_for('item_detail', item_id=item_id))
            
        if item.status != 'ended':
            flash('拍卖尚未结束')
            return redirect(url_for('item_detail', item_id=item_id))
            
        if item.payment_status == 'paid':
            flash('该订单已支付')
            return redirect(url_for('item_detail', item_id=item_id))
        show_qr = False
        deposit_amount = Decimal('0.00')
        payable = Decimal(item.current_price).quantize(Decimal('0.01'))
        from models import Deposit
        dep = Deposit.query.filter_by(item_id=item.id, user_id=current_user.id).filter(Deposit.status.in_(['frozen','applied'])).first()
        if dep:
            deposit_amount = Decimal(dep.amount).quantize(Decimal('0.01'))
            payable = (payable - deposit_amount)
            if payable < Decimal('0.00'):
                payable = Decimal('0.00')

        if request.method == 'POST':
            shipping_name = request.form.get('shipping_name')
            shipping_phone = request.form.get('shipping_phone')
            shipping_address = request.form.get('shipping_address')
            
            if shipping_name and shipping_phone and shipping_address:
                # 保存收货信息
                item.shipping_name = shipping_name
                item.shipping_phone = shipping_phone
                item.shipping_address = shipping_address
                db.session.commit()
                show_qr = True
            else:
                flash('请填写所有地址信息')

        # 在模板中展示钱包支付信息，点击确认将调用 confirm_payment
        user = User.query.get(current_user.id)
        balance = Decimal(user.wallet_balance).quantize(Decimal('0.01'))
        return render_template('payment.html', item=item, show_qr=show_qr, deposit_amount=deposit_amount, payable=payable, balance=balance)

    @app.route('/item/<int:item_id>/confirm_payment', methods=['POST'])
    @login_required
    def confirm_payment(item_id):
        item = Item.query.get_or_404(item_id)
        if not getattr(current_user, 'is_verified', False):
            flash('您尚未完成实名认证。<a href="' + url_for('verify_identity') + '" class="btn btn-sm btn-primary ms-2">现在去实名</a> <button type="button" class="btn btn-sm btn-secondary ms-2" data-bs-dismiss="alert">明白了，稍后再去</button>')
            return redirect(url_for('verify_identity'))
        if item.highest_bidder_id != current_user.id:
            return redirect(url_for('index'))
        if item.status != 'ended':
            flash('拍卖尚未结束')
            return redirect(url_for('item_detail', item_id=item_id))
        if item.payment_status == 'paid':
            flash('该订单已支付')
            return redirect(url_for('item_detail', item_id=item_id))

        # 计算应付金额 (应用保证金)
        total = Decimal(item.current_price).quantize(Decimal('0.01'))
        from models import Deposit
        dep = Deposit.query.filter_by(item_id=item.id, user_id=current_user.id).filter(Deposit.status.in_(['frozen','applied'])).first()
        deposit_amount = Decimal('0.00')
        if dep:
            deposit_amount = Decimal(dep.amount).quantize(Decimal('0.01'))
        payable = (total - deposit_amount)
        if payable < Decimal('0.00'):
            payable = Decimal('0.00')

        # 检查钱包余额
        user = User.query.get(current_user.id)
        balance = Decimal(user.wallet_balance).quantize(Decimal('0.01'))
        if balance < payable:
            flash('钱包余额不足，无法完成支付。请先充值。')
            return redirect(url_for('wallet'))

        # 扣除需支付金额，并标记保证金已使用
        from models import WalletTransaction
        new_balance = (balance - payable).quantize(Decimal('0.01'))
        user.wallet_balance = new_balance
        if dep and dep.status == 'frozen':
            dep.status = 'applied'
        item.payment_status = 'paid'

        # 记录支付交易（买家）
        db.session.add(WalletTransaction(
            user_id=user.id,
            item_id=item.id,
            type='payment',
            direction='debit',
            amount=payable,
            balance_after=new_balance,
            description=f'支付订单：{item.order_hash}'
        ))
        db.session.commit()

        # Notify Seller
        send_system_message(item.id, item.seller_id, f"订单 {item.order_hash} 已付款。请尽快安排发货。收货人：{item.shipping_name}，地址：{item.shipping_address}")

        flash('支付确认成功！')
        return redirect(url_for('item_detail', item_id=item_id))

    @app.route('/item/<int:item_id>/ship', methods=['POST'])
    @login_required
    def ship_item(item_id):
        item = Item.query.get_or_404(item_id)
        if not getattr(current_user, 'is_verified', False):
            flash('您尚未完成实名认证。<a href="' + url_for('verify_identity') + '" class="btn btn-sm btn-primary ms-2">现在去实名</a> <button type="button" class="btn btn-sm btn-secondary ms-2" data-bs-dismiss="alert">明白了，稍后再去</button>')
            return redirect(url_for('verify_identity'))
        
        # 验证权限：只有卖家能发货
        if item.seller_id != current_user.id:
            flash('您无权操作此订单')
            return redirect(url_for('my_auctions'))
            
        if item.payment_status != 'paid':
            flash('买家尚未付款，无法发货')
            return redirect(url_for('my_auctions'))
            
        tracking_number = request.form.get('tracking_number')
        if not tracking_number:
            flash('请输入快递单号')
            return redirect(url_for('my_auctions'))
            
        item.tracking_number = tracking_number
        item.shipping_status = 'shipped'
        db.session.commit()
        
        # 通知买家
        send_system_message(item.id, item.highest_bidder_id, f"您的订单 {item.order_hash} 已发货！快递单号：{tracking_number}")
        
        flash('发货成功')
        return redirect(url_for('my_auctions'))

    @app.route('/item/<int:item_id>/confirm_receipt', methods=['POST'])
    @login_required
    def confirm_receipt(item_id):
        item = Item.query.get_or_404(item_id)
        if not getattr(current_user, 'is_verified', False):
            flash('您尚未完成实名认证。<a href="' + url_for('verify_identity') + '" class="btn btn-sm btn-primary ms-2">现在去实名</a> <button type="button" class="btn btn-sm btn-secondary ms-2" data-bs-dismiss="alert">明白了，稍后再去</button>')
            return redirect(url_for('verify_identity'))
        
        # 验证权限：只有买家能收货
        if item.highest_bidder_id != current_user.id:
            flash('您无权操作此订单')
            return redirect(url_for('my_orders'))
            
        if item.shipping_status != 'shipped':
            flash('订单状态不正确，无法确认收货')
            return redirect(url_for('my_orders'))
            
        item.shipping_status = 'received'
        
        # 为卖家入账成交总额
        seller = item.seller
        seller_balance = Decimal(seller.wallet_balance).quantize(Decimal('0.01'))
        sale_total = Decimal(item.current_price).quantize(Decimal('0.01'))
        seller_new_balance = (seller_balance + sale_total).quantize(Decimal('0.01'))
        seller.wallet_balance = seller_new_balance

        from models import WalletTransaction
        db.session.add(WalletTransaction(
            user_id=seller.id,
            item_id=item.id,
            type='payout',
            direction='credit',
            amount=sale_total,
            balance_after=seller_new_balance,
            description=f'出售拍品入账：{item.name}'
        ))

        db.session.commit()
        
        # 通知卖家
        send_system_message(item.id, item.seller_id, f"买家已确认收货，订单 {item.order_hash} 完成。资金已转入您的钱包。")
        
        flash('确认收货成功')
        return redirect(url_for('my_orders'))
