from flask import render_template, request, abort, url_for
from flask_login import login_required, current_user
from flask_socketio import emit, join_room, leave_room
from models import User, Item, ChatSession, Message
from extensions import db
from sqlalchemy import or_
from datetime import datetime

def register_chat_routes(app):
    @app.route('/inbox')
    @login_required
    def inbox():
        # 未实名认证仅能浏览首页，禁止查看私信
        if not getattr(current_user, 'is_verified', False) and current_user.role != 'admin':
            flash('您尚未完成实名认证。<a href="' + url_for('verify_identity') + '" class="btn btn-sm btn-primary ms-2">现在去实名</a> <button type="button" class="btn btn-sm btn-secondary ms-2" data-bs-dismiss="alert">明白了，稍后再去</button>')
            return redirect(url_for('verify_identity'))
        # 获取我参与的所有会话，按时间倒序
        sessions = ChatSession.query.filter(
            or_(ChatSession.buyer_id == current_user.id, ChatSession.seller_id == current_user.id)
        ).order_by(ChatSession.updated_at.desc()).all()
        
        return render_template('inbox.html', sessions=sessions)

    @app.route('/chat/<int:item_id>/<int:other_user_id>')
    @login_required
    def start_chat(item_id, other_user_id):
        if not getattr(current_user, 'is_verified', False) and current_user.role != 'admin':
            flash('您尚未完成实名认证。<a href="' + url_for('verify_identity') + '" class="btn btn-sm btn-primary ms-2">现在去实名</a> <button type="button" class="btn btn-sm btn-secondary ms-2" data-bs-dismiss="alert">明白了，稍后再去</button>')
            return redirect(url_for('verify_identity'))
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
        history_messages = []
        
        if not session:
            session = ChatSession(item_id=item_id, buyer_id=buyer_id, seller_id=seller_id)
            db.session.add(session)
            db.session.commit() # 需要 commit 获取 id 以便关联 message
        else:
            # 加载历史消息
            msgs = Message.query.filter_by(chat_session_id=session.id).order_by(Message.timestamp).all()
            for m in msgs:
                sender = User.query.get(m.sender_id)
                history_messages.append({
                    'sender': sender.username,
                    'sender_id': m.sender_id,
                    'msg': m.content,
                    'timestamp': m.timestamp.isoformat(),
                    'avatar': sender.avatar
                })

        # 清除未读计数
        if current_user.id == buyer_id:
            session.buyer_unread = 0
        else:
            session.seller_unread = 0
        
        db.session.commit()
        
        return render_template('chat.html', item=item, other_user=other_user, current_user=current_user, history_messages=history_messages)

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
        # 未实名认证禁止发送消息
        if not getattr(current_user, 'is_verified', False) and current_user.role != 'admin':
            return
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
                            
                            # 保存消息记录
                            new_msg = Message(
                                chat_session_id=session.id, 
                                sender_id=current_user.id, 
                                content=msg,
                                timestamp=datetime.now()
                            )
                            db.session.add(new_msg)
                            
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
