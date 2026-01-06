from flask import render_template, request, abort, url_for
from flask_login import login_required, current_user
from flask_socketio import emit, join_room, leave_room
from models import User, Item, ChatSession
from extensions import db
from sqlalchemy import or_

def register_chat_routes(app):
    @app.route('/inbox')
    @login_required
    def inbox():
        # 获取我参与的所有会话，按时间倒序
        sessions = ChatSession.query.filter(
            or_(ChatSession.buyer_id == current_user.id, ChatSession.seller_id == current_user.id)
        ).order_by(ChatSession.updated_at.desc()).all()
        
        return render_template('inbox.html', sessions=sessions)

    @app.route('/chat/<int:item_id>/<int:other_user_id>')
    @login_required
    def start_chat(item_id, other_user_id):
        item = Item.query.get_or_404(item_id)
        other_user = User.query.get_or_404(other_user_id)
        
        # 确定买家和卖家身份
        # 如果当前用户是发布者，则当前用户是 seller，other 是 buyer
        # 否则当前用户是 buyer，other 是 seller
        if item.seller_id == current_user.id:
            seller_id = current_user.id
            buyer_id = other_user_id
        else:
            seller_id = other_user_id
            buyer_id = current_user.id
            
        # 查找或创建会话
        session = ChatSession.query.filter_by(item_id=item_id, buyer_id=buyer_id, seller_id=seller_id).first()
        if not session:
            session = ChatSession(item_id=item_id, buyer_id=buyer_id, seller_id=seller_id)
            db.session.add(session)
        
        # 清除未读计数
        if current_user.id == buyer_id:
            session.buyer_unread = 0
        else:
            session.seller_unread = 0
        
        db.session.commit()
        
        return render_template('chat.html', item=item, other_user=other_user, current_user=current_user)

def register_chat_events(socketio):
    @socketio.on('join_chat')
    def on_join_chat(data):
        room = data.get('room')
        if room:
            join_room(room)
            # 可以选择不广播进入消息，避免刷屏
            # emit('status', {'msg': f'{current_user.username} is connected'}, room=room)

    @socketio.on('send_message')
    def on_send_message(data):
        room = data.get('room')
        msg = data.get('msg')
        item_id = data.get('item_id')
        receiver_id = data.get('receiver_id')
        timestamp = data.get('timestamp')
        
        if room and msg:
            # 更新会话状态（持久化 Last Message 和未读计数）
            if item_id and receiver_id:
                try:
                    # 推断身份
                    # 注意：这里我们只能基于发来的 item_id 查库
                    item = Item.query.get(item_id)
                    if item:
                        buyer_id = None
                        seller_id = None
                        
                        # 逻辑：如果 sender 是 item.seller，那 receiver 是 buyer
                        if current_user.id == item.seller_id:
                            seller_id = current_user.id
                            buyer_id = receiver_id
                        else:
                            # 否则 sender 是 buyer（或潜在买家），receiver 是 seller
                            buyer_id = current_user.id
                            seller_id = item.seller_id # 确保 receiver 是真的 seller
                            
                        # 查找会话
                        session = ChatSession.query.filter_by(item_id=item_id, buyer_id=buyer_id, seller_id=seller_id).first()
                        if session:
                            session.last_message = msg[:250] # 截断防止溢出
                            # 增加对方未读数
                            if current_user.id == buyer_id:
                                session.seller_unread += 1
                            else:
                                session.buyer_unread += 1
                            db.session.commit()
                            
                            # 发送通知给接收者
                            emit('new_chat_notification', {'msg': '您有一条新私信'}, room=f"user_{receiver_id}")
                            
                except Exception as e:
                    print(f"Update chat session failed: {e}")

            emit('new_message', {
                'sender': current_user.username,
                'sender_id': current_user.id,
                'avatar': current_user.avatar,
                'msg': msg,
                'timestamp': timestamp
            }, room=room)
