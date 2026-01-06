from models import User, Item, ChatSession
from extensions import db, socketio
from datetime import datetime

def send_system_message(item_id, receiver_id, content):
    """
    发送系统消息（以管理员身份）到用户的收件箱
    """
    try:
        # 获取管理员账户
        admin = User.query.filter_by(role='admin').first()
        if not admin:
            print("System Message Error: No admin user found.")
            return

        # 如果接收者自己就是管理员，不需要发送系统消息给自己
        if receiver_id == admin.id:
            return

        item = Item.query.get(item_id)
        if not item:
            return

        # 确定会话双方
        # 逻辑：为了让接收者在收件箱看到"Admin"，我们需要创建一个会话
        # 其中一方是 receiver_id, 另一方是 admin.id
        
        # 为了保持一致性：
        # 如果 receiver 是该商品的 seller => session.seller_id=receiver, session.buyer_id=admin
        # 否则 (receiver是买家) => session.buyer_id=receiver, session.seller_id=admin
        
        if receiver_id == item.seller_id:
            s_id = receiver_id # User
            b_id = admin.id    # Admin
        else:
            b_id = receiver_id # User
            s_id = admin.id    # Admin
            
        session = ChatSession.query.filter_by(item_id=item_id, buyer_id=b_id, seller_id=s_id).first()
        
        if not session:
            session = ChatSession(item_id=item_id, buyer_id=b_id, seller_id=s_id)
            db.session.add(session)
            
        # 更新消息内容
        session.last_message = f"[系统通知] {content}"
        session.updated_at = datetime.now()
        
        # 增加未读计数
        if receiver_id == b_id:
            session.buyer_unread += 1
        else:
            session.seller_unread += 1
            
        db.session.commit()
        print(f"System message sent to User {receiver_id}: {content}")
        
        # 发送实时通知以更新前端红点和弹窗
        try:
            socketio.emit('new_chat_notification', {'msg': content}, room=f"user_{receiver_id}")
        except Exception as e:
            print(f"Socket emit in send_system_message failed: {e}")
            
    except Exception as e:
        print(f"Failed to send system message: {e}")
        db.session.rollback()
