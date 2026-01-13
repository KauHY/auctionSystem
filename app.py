from flask import Flask
from extensions import db, socketio, login_manager
from models import User
from views import register_views
from events import register_events
from chat import register_chat_routes, register_chat_events
from tasks import check_auctions
import threading
import pymysql
import os
from sqlalchemy import text
import logging
from logging.handlers import RotatingFileHandler

# 确保pymysql可以被SQLAlchemy作为mysqldb使用
pymysql.install_as_MySQLdb()

# 使用绝对路径配置上传文件夹，确保文件持久化存储
basedir = os.path.abspath(os.path.dirname(__file__))

def create_app():
    # 初始化应用
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'your_secret_key'
    
    app.config['UPLOAD_FOLDER'] = os.path.join(basedir, 'static', 'uploads')
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max-limit
    
    # --- 数据库配置 (请根据实际情况修改) ---
    app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:123456@localhost/Auction'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # 日志目录与文件
    logs_dir = os.path.join(basedir, 'logs')
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)

    # 配置文件滚动日志（最大2MB，保留5个备份）
    file_handler = RotatingFileHandler(os.path.join(logs_dir, 'app.log'), maxBytes=2 * 1024 * 1024, backupCount=5, encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s %(levelname)s [%(name)s] %(message)s')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    # 根据调试模式调整日志级别
    app.logger.setLevel(logging.DEBUG if app.debug else logging.INFO)

    db.init_app(app)
    # 启用 Socket.IO 日志，便于排查实时事件问题
    socketio.init_app(app, logger=True, engineio_logger=True)
    login_manager.init_app(app)
    login_manager.login_view = 'login'
    login_manager.login_message = '请先登录以访问本系统'
    
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # --- 注册全站通用过滤器 (Localization) ---
    @app.template_filter('localize')
    def localize_filter(value):
        if not value:
            return ""
        translations = {
            # 角色
            'buyer': '买家',
            'seller': '卖家',
            'admin': '管理员',
            
            # 物品状态
            'pending': '待审核',
            'approved': '即将开始',
            'active': '进行中',
            'ended': '已结束',
            'rejected': '已驳回',
            'stopped': '强制下架',
            
            # 支付状态
            'unpaid': '未支付',
            'paid': '已支付',
            'timeout_cancelled': '超时取消',
            
            # 物流状态
            'unshipped': '待发货',
            'shipped': '已发货',
            'received': '已收货',

            # 申诉状态
            'resolved': '已解决',
            
            # 钱包/交易类型
            'credit': '入账',
            'debit': '扣款',
            'recharge': '充值',
            'deposit': '保证金',
            'refund': '退款',
            'payment': '支付货款',
            'forfeit': '罚没/扣除',
            'payout': '货款结算',
            'frozen': '冻结',
            'applied': '已应用',
            'refunded': '已退还',
            'forfeited': '已罚没'
        }
        return translations.get(str(value), value)
        
    register_views(app)
    register_chat_routes(app)
    register_events(socketio)
    register_chat_events(socketio)
    
    return app

if __name__ == '__main__':
    app = create_app()
    with app.app_context():        
        try:
            # 尝试创建一个默认管理员，防止数据库是空的
            if not User.query.filter_by(username='admin').first():
                admin = User(username='admin', password_hash='123', role='admin')
                db.session.add(admin)
                db.session.commit()
                app.logger.info("检测到数据库中没有管理员，已自动创建: admin / 123")
        except Exception as e:
            # 捕获连接错误打印出来，不中断主进程，但用户必须处理
            app.logger.error(f"连接数据库失败或查询出错: {e}")
            app.logger.error("请检查 app.py 中的 SQLALCHEMY_DATABASE_URI配置，并确保MySQL服务已运行")

        # 尝试自动创建 Post 表 (如果是之前创建的数据库)
        try:
            db.create_all() # create_all 只会创建不存在的表
        except:
            pass
        
        # 尝试自动创建 ChatSession 表 (手动检查)
        try:
             db.session.execute(text("SELECT 1 FROM chat_sessions LIMIT 1"))
        except:
             try:
                 db.create_all()
                 app.logger.info("尝试创建新表 chat_sessions")
             except:
                 pass

    bg_thread = threading.Thread(target=check_auctions, args=(app,))
    bg_thread.daemon = True
    bg_thread.start()
    
    # host='0.0.0.0' 使其他设备可访问
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
