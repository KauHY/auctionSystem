from datetime import datetime, timedelta
import threading
import time
import hashlib
from extensions import db, socketio
from models import Item
from services import send_system_message

def check_auctions(app):
    """后台任务：检查拍卖状态"""
    while True:
        try:
            with app.app_context():
                now = datetime.now()
                
                # 1. 检查已到期的 'active' 拍卖 -> 'ended'
                expired_items = Item.query.filter(Item.status == 'active', Item.end_time <= now).all()
                for item in expired_items:
                    item.status = 'ended'
                    
                    # 如果有获胜者，生成订单哈希
                    if item.highest_bidder_id:
                        # 生成易读的订单编号：ORD + 年月日时分秒 + 4位商品ID (例: ORD202401011200000005)
                        # 这种格式方便后续检索和客服查询
                        timestamp_str = datetime.now().strftime('%Y%m%d%H%M%S')
                        item.order_hash = f"ORD{timestamp_str}{item.id:04d}"
                        
                        # 通知买家 (获胜)
                        send_system_message(item.id, item.highest_bidder_id, f'恭喜！您赢得了拍品 "{item.name}"，成交价 ¥{item.current_price}。订单号: {item.order_hash}')

                    db.session.commit()
                    winner_name = item.highest_bidder.username if item.highest_bidder else '无人出价'
                    socketio.emit('auction_ended', {
                        'item_id': item.id, 
                        'winner': winner_name,
                        'order_hash': item.order_hash if item.highest_bidder_id else None
                    }, room=f"item_{item.id}")
                    
                    # 通知卖家 (出售结果)
                    if item.highest_bidder_id:
                        send_system_message(item.id, item.seller_id, f'您的拍品 "{item.name}" 已成功售出！成交价 ¥{item.current_price}，买家: {winner_name}。订单号: {item.order_hash}')
                    else:
                        send_system_message(item.id, item.seller_id, f'您的拍品 "{item.name}" 拍卖结束，遗憾的是无人出价。')
                    
                # 2. 检查已到期的 'approved' 拍卖 (定时上架) -> 'active'

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
