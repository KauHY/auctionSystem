from datetime import datetime, timedelta
import threading
import time
import hashlib
from extensions import db, socketio
from models import Item, Bid, Deposit
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
                        
                        # Toast: Seller (Blue)
                        socketio.emit('auction_result_toast', {
                            'type': 'info',
                            'msg': f'拍卖结束: "{item.name}" 已被 {winner_name} 以 ¥{item.current_price} 中标。'
                        }, room=f"user_{item.seller_id}")
                        
                        # Toast: Winner (Green)
                        socketio.emit('auction_result_toast', {
                            'type': 'success',
                            'msg': f'【恭喜中标】您已成功拍下 "{item.name}"，成交价 ¥{item.current_price}！'
                        }, room=f"user_{item.highest_bidder_id}")
                        
                        # 保证金处理：未中标者自动退款
                        loser_deposits = Deposit.query.filter(
                            Deposit.item_id == item.id,
                            Deposit.user_id != item.highest_bidder_id,
                            Deposit.status == 'frozen'
                        ).all()
                        from models import WalletTransaction
                        for ld in loser_deposits:
                            ld.status = 'refunded'
                            # 退款到余额
                            user = ld.user
                            try:
                                from decimal import Decimal
                                amt = Decimal(ld.amount)
                                new_balance = (Decimal(user.wallet_balance) + amt)
                                user.wallet_balance = new_balance
                                db.session.add(WalletTransaction(
                                    user_id=user.id,
                                    item_id=item.id,
                                    type='refund',
                                    direction='credit',
                                    amount=amt,
                                    balance_after=new_balance,
                                    description=f'未中标退还保证金：{item.name}'
                                ))
                                # 发送系统消息提醒退款
                                send_system_message(item.id, user.id, f'拍品 "{item.name}" 竞拍失败，保证金 ¥{amt} 已退回您的钱包余额。')
                            except Exception:
                                pass
                        db.session.commit()

                        # Toast: Losers (Yellow)
                        # 查找所有出过价但未获胜的用户
                        loser_bids = Bid.query.filter(
                            Bid.item_id == item.id, 
                            Bid.user_id != item.highest_bidder_id
                        ).with_entities(Bid.user_id).distinct().all()
                        
                        for lb in loser_bids:
                            loser_id = lb.user_id
                            # 排除如果是卖家自己出价（虽然逻辑禁止，但防万一）
                            if loser_id != item.seller_id:
                                socketio.emit('auction_result_toast', {
                                    'type': 'warning',
                                    'msg': f'【遗憾离场】拍品 "{item.name}" 拍卖已结束，您未中标。成交价: ¥{item.current_price}。'
                                }, room=f"user_{loser_id}")

                    else:
                        send_system_message(item.id, item.seller_id, f'您的拍品 "{item.name}" 拍卖结束，遗憾的是无人出价。')
                        # Toast: Seller (Unsold - Blue/Info)
                        socketio.emit('auction_result_toast', {
                            'type': 'info',
                            'msg': f'拍卖结束: "{item.name}" 无人出价，已流拍。'
                        }, room=f"user_{item.seller_id}")
                        # 无人中标情况下，退还所有已缴保证金
                        unsold_deps = Deposit.query.filter(
                            Deposit.item_id == item.id,
                            Deposit.status == 'frozen'
                        ).all()
                        from models import WalletTransaction
                        for ld in unsold_deps:
                            ld.status = 'refunded'
                            user = ld.user
                            try:
                                from decimal import Decimal
                                amt = Decimal(ld.amount)
                                new_balance = (Decimal(user.wallet_balance) + amt)
                                user.wallet_balance = new_balance
                                db.session.add(WalletTransaction(
                                    user_id=user.id,
                                    item_id=item.id,
                                    type='refund',
                                    direction='credit',
                                    amount=amt,
                                    balance_after=new_balance,
                                    description=f'流拍退还保证金：{item.name}'
                                ))
                                send_system_message(item.id, user.id, f'拍品 "{item.name}" 流拍，保证金 ¥{amt} 已退回您的钱包余额。')
                            except Exception:
                                pass
                        db.session.commit()
                    
                # 2. 检查已到期的 'approved' 拍卖 (定时上架) -> 'active'

                # 2. 检查已到开拍时间的 'approved' 拍卖 -> 'active'
                starting_items = Item.query.filter(Item.status == 'approved', Item.start_time <= now).all()
                for item in starting_items:
                    item.status = 'active'
                    db.session.commit()
                    # 可选择通知首页刷新，或在该 Item 的房间里广播
                    print(f"Auction {item.id} started automatically at {now}")

                # 3. 检查已结束后24小时仍未付款的订单 -> 流拍 + 封禁买家
                # 条件: status='ended', payment_status='unpaid', end_time < now - 24h
                deadline = now - timedelta(hours=24)
                unpaid_items = Item.query.filter(
                    Item.status == 'ended',
                    Item.payment_status == 'unpaid',
                    Item.highest_bidder_id.isnot(None),
                    Item.end_time <= deadline
                ).all()

                for item in unpaid_items:
                    # 更新订单状态为“流拍/超时未付”
                    item.payment_status = 'timeout_cancelled'
                    # 可选择将 status 也改为 'unsold_timeout' 以便区分，但保持 ended 也没问题，主要靠 payment_status 区分
                    
                    # 封禁买家 30 天
                    bidder = item.highest_bidder
                    if bidder:
                        ban_until = now + timedelta(days=30)
                        bidder.banned_until = ban_until
                        # 违约：没收中标者保证金
                        winner_dep = Deposit.query.filter_by(item_id=item.id, user_id=bidder.id).filter(Deposit.status.in_(['frozen','applied'])).first()
                        if winner_dep:
                                winner_dep.status = 'forfeited'
                                # 保证金不返还，余额不变（已在缴纳时扣除）
                        
                        # 通知买家
                        send_system_message(item.id, bidder.id, f'【违规处罚】由于您在拍品 "{item.name}" 结束后24小时内未完成付款，系统判定为违约。您的账户已被禁止参与拍卖活动30天，解封时间：{ban_until.strftime("%Y-%m-%d %H:%M")}。')
                        
                        # 通知卖家
                        send_system_message(item.id, item.seller_id, f'很抱歉，拍品 "{item.name}" 的买家未在24小时内付款，交易已自动取消。您可以重新发布该商品。')
                        
                    db.session.commit()
                    print(f"Auction {item.id} cancelled due to non-payment. Buyer {item.highest_bidder_id} banned.")

        except Exception as e:
            print(f"Check auction error: {e}")
        time.sleep(10) 
