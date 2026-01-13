from sqlalchemy import or_

def get_index_items(Item, User, search_query='', category=None, sort_option='default'):
    """
    获首页所需的各类商品列表
    :param Item: Item 模型类
    :param User: User 模型类
    :param search_query: 搜索关键词
    :param category: 分类筛选
    :param sort_option: 排序选项
    :return: (active_items, upcoming_items, ended_items)
    """
    
    # 基础查询构造器
    def get_base_query(status_list):
        q_obj = Item.query.filter(Item.status.in_(status_list))
        if category:
            q_obj = q_obj.filter(Item.category == category)
        if search_query:
            # 联表查询：匹配商品名 或 卖家用户名
            q_obj = q_obj.join(User, Item.seller_id == User.id).filter(
                or_(Item.name.like(f'%{search_query}%'), User.username.like(f'%{search_query}%'))
            )
        return q_obj

    # 排序逻辑应用器
    def apply_sort(query_obj, default_sort):
        if sort_option == 'start_time_desc': # 上架时间 (最新)
            return query_obj.order_by(Item.start_time.desc())
        elif sort_option == 'end_time_asc': # 截止时间 (最近)
            return query_obj.order_by(Item.end_time.asc())
        elif sort_option == 'end_time_desc': # 截止时间 (最远)
            return query_obj.order_by(Item.end_time.desc())
        elif sort_option == 'price_asc': # 价格 (低到高) - 对active/ended是current_price, upcoming是start_price
            return query_obj.order_by(Item.current_price.asc())
        elif sort_option == 'price_desc': # 价格 (高到低)
            return query_obj.order_by(Item.current_price.desc())
        elif sort_option == 'start_price_asc': # 起拍价 (低到高)
            return query_obj.order_by(Item.start_price.asc())
        elif sort_option == 'start_price_desc': # 起拍价 (高到低)
            return query_obj.order_by(Item.start_price.desc())
        return default_sort(query_obj)

    # 默认排序定义
    # Active: 默认按结束时间升序 (快结束的在前)
    active_query = get_base_query(['active'])
    active_items = apply_sort(active_query, lambda q: q.order_by(Item.end_time)).all()

    # Upcoming: 默认按开始时间升序 (快开始的在前)
    # 起拍价排序对 Upcoming 也有效
    upcoming_query = get_base_query(['approved'])
    upcoming_items = apply_sort(upcoming_query, lambda q: q.order_by(Item.start_time)).all()
    
    # Ended: 默认按结束时间降序 (刚结束的在前)
    ended_query = get_base_query(['ended'])
    ended_items = apply_sort(ended_query, lambda q: q.order_by(Item.end_time.desc())).limit(12).all()
    
    return active_items, upcoming_items, ended_items

def get_admin_dashboard_items(Item):
    """
    获取管理员后台所需的商品列表
    """
    pending_items = Item.query.filter_by(status='pending').all()
    active_items = Item.query.filter(Item.status.in_(['active', 'approved'])).order_by(Item.start_time).all()
    # 历史记录包含已结束和被强制终止的拍品
    ended_items = Item.query.filter(Item.status.in_(['ended', 'stopped'])).order_by(Item.end_time.desc()).all()
    
    return pending_items, active_items, ended_items

def get_seller_items(Item, User, seller_id, search_query=''):
    """
    获取卖家发布的商品，支持搜索
    :param search_query: 订单号/商品名/买家ID/买家用户名
    """
    q = Item.query.filter_by(seller_id=seller_id)
    
    if search_query:
        # 尝试匹配: 订单号 OR 商品名 OR (买家ID 或 买家用户名)
        # 需 Join User 表来匹配买家用户名
        q = q.outerjoin(User, Item.highest_bidder_id == User.id).filter(
            or_(
                Item.order_hash.like(f'%{search_query}%'),
                Item.name.like(f'%{search_query}%'),
                User.username.like(f'%{search_query}%'),
                User.id == search_query if search_query.isdigit() else False
            )
        )
        
    return q.order_by(Item.created_at.desc()).all()

def get_buyer_won_items(Item, User, buyer_id, search_query=''):
    """
    获取买家赢得的商品，支持搜索
    :param search_query: 订单号/商品名/卖家ID/卖家用户名
    """
    q = Item.query.filter_by(status='ended', highest_bidder_id=buyer_id)
    
    if search_query:
        # Join User 表来匹配卖家用户名
        q = q.join(User, Item.seller_id == User.id).filter(
            or_(
                Item.order_hash.like(f'%{search_query}%'),
                Item.name.like(f'%{search_query}%'),
                User.username.like(f'%{search_query}%'),
                User.id == search_query if search_query.isdigit() else False
            )
        )
        
    return q.order_by(Item.end_time.desc()).all()

def get_user_posts(Post, user_id):
    """获取用户的动态列表"""
    return Post.query.filter_by(user_id=user_id).order_by(Post.created_at.desc()).all()

def get_user_public_items(Item, user_id):
    """获取用户(卖家)公开展示的拍品 (Active/Upcoming/Ended)"""
    return Item.query.filter(
        Item.seller_id == user_id, 
        Item.status.in_(['active', 'approved', 'ended'])
    ).order_by(Item.created_at.desc()).all()

def get_appeal_list(Appeal):
    """
    获取申诉列表 (替代原 get_appeal_items)
    """
    all_appeals = Appeal.query.order_by(Appeal.created_at.desc()).all()
    pending_appeals = [a for a in all_appeals if a.status == 'pending']
    history_appeals = [a for a in all_appeals if a.status != 'pending']
    return pending_appeals, history_appeals

def get_search_users(User, search_query):
    """
    搜索卖家
    :param User: User 模型类
    :param search_query: 搜索关键词
    """
    return User.query.filter(
        User.role == 'seller',
        User.username.like(f'%{search_query}%')
    ).all()

