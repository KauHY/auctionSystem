from flask import request
from flask_socketio import emit, join_room
from flask_login import current_user
from datetime import datetime, timedelta
from extensions import db, socketio
from models import Item, Bid, Deposit
from decimal import Decimal

def register_events(socketio):
    @socketio.on('connect')

    def handle_connect():
        if current_user.is_authenticated:
            join_room(f"user_{current_user.id}")
            print(f"User {current_user.username} (ID: {current_user.id}) joined room user_{current_user.id}")

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
        # 未实名认证限制出价
        if not getattr(current_user, 'is_verified', False):
            emit('error', {'msg': '请先完成实名认证后再参与出价'}, room=request.sid)
            return
        # 未缴纳保证金限制出价
        item_id = data['item_id']
        dep = Deposit.query.filter_by(item_id=item_id, user_id=current_user.id, status='frozen').first()
        if dep is None:
            emit('error', {'msg': '参与竞价需先缴纳保证金，请前往拍品页面缴纳后再试。'}, room=request.sid)
            return
        
        # 使用 Decimal 处理金额
        try:
            amount = Decimal(str(data['amount']))
        except:
            emit('error', {'msg': '无效的金额格式'}, room=request.sid)
            return
        
        item = Item.query.get(item_id)
        
        if not item or item.status != 'active':
            return
            
        # 检查封禁状态
        if current_user.banned_until and current_user.banned_until > datetime.now():
            emit('error', {'msg': f'由于未付款记录，您的账户已被封禁至 {current_user.banned_until.strftime("%Y-%m-%d %H:%M")}，暂无法出价。'}, room=request.sid)
            return
            
        # 禁止连续出价
        if item.highest_bidder_id == current_user.id:
            emit('error', {'msg': '您已经是当前最高出价者，不可重复出价'}, room=request.sid)
            return

        if datetime.now() > item.end_time:
            item.status = 'ended'
            db.session.commit()
            emit('error', {'msg': '拍卖已结束'}, room=f"item_{item_id}")
            return

        # 直接使用 Decimal 比较，无需转换 float
        # item.current_price, item.increment, item.start_price 应该是 Decimal 类型

        if item.highest_bidder is None:
            min_bid = item.start_price
        else:
            min_bid = item.current_price + item.increment

        if amount < min_bid:
            emit('error', {'msg': f'出价必须高于 {min_bid}'}, room=request.sid)
            return

        # 防狙击: 只有在最后3分钟内出现第3次及以上出价时才延长
        time_left = item.end_time - datetime.now()
        extended = False
        if time_left < timedelta(minutes=3):
            # 统计当前截止时间前3分钟内的已有出价数量
            window_start = item.end_time - timedelta(minutes=3)
            recent_bids_count = Bid.query.filter(
                Bid.item_id == item_id,
                Bid.timestamp >= window_start
            ).count()
            
            # 当前出价是第 (recent_bids_count + 1) 笔，如果达到3笔则延长
            if recent_bids_count + 1 >= 3:
                item.end_time += timedelta(minutes=5)
                extended = True

        item.current_price = amount
        item.highest_bidder_id = current_user.id
        
        new_bid = Bid(item_id=item.id, user_id=current_user.id, amount=amount)
        db.session.add(new_bid)
        db.session.commit()
        
        response = {
            'new_price': float(amount), # JSON响应转回float方便前端与JSON兼容
            'bidder_name': current_user.username,
            'new_end_time': item.end_time.isoformat(), 
            'extended': extended
        }
        emit('price_update', response, room=f"item_{item_id}")
