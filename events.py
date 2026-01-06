from flask import request
from flask_socketio import emit, join_room
from flask_login import current_user
from datetime import datetime, timedelta
from extensions import db, socketio
from models import Item, Bid

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
            
        item_id = data['item_id']
        amount = float(data['amount'])
        
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
            'new_price': amount,
            'bidder_name': current_user.username,
            'new_end_time': item.end_time.isoformat(), 
            'extended': extended
        }
        emit('price_update', response, room=f"item_{item_id}")
