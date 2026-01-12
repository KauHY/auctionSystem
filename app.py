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
        
    register_views(app)
    register_chat_routes(app)
    register_events(socketio)
    register_chat_events(socketio)
    
    return app

if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        # Ensure tables exist
        db.create_all()
        
        # 尝试自动迁移添加 rejection_reason 字段 (如果不存在)
        try:
            db.session.execute(text("ALTER TABLE items ADD COLUMN rejection_reason VARCHAR(255)"))
            db.session.commit()
            app.logger.info("成功添加 rejection_reason 字段")
        except Exception as e:
            # 忽略错误，假设字段已存在
            app.logger.debug(f"尝试添加字段跳过 (可能已存在): {e}")

        # 尝试自动迁移添加 order_hash 字段 (如果不存在)
        try:
            db.session.execute(text("ALTER TABLE items ADD COLUMN order_hash VARCHAR(64)"))
            db.session.commit()
            app.logger.info("成功添加 order_hash 字段")
        except Exception as e:
            pass 

        # 尝试自动迁移添加 email 字段
        try:
            db.session.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(120)"))
            db.session.commit()
            app.logger.info("成功添加 email 字段")
        except Exception as e:
            pass

        # 尝试自动迁移删除 phone 字段
        try:
            db.session.execute(text("ALTER TABLE users DROP COLUMN phone"))
            db.session.commit()
            app.logger.info("成功删除 phone 字段")
        except Exception as e:
            pass

        # 尝试自动迁移添加 category 字段
        try:
            db.session.execute(text("ALTER TABLE items ADD COLUMN category VARCHAR(50)"))
            db.session.commit()
            app.logger.info("成功添加 category 字段")
        except Exception as e:
            pass

        # 尝试自动迁移添加 avatar 字段
        try:
            db.session.execute(text("ALTER TABLE users ADD COLUMN avatar VARCHAR(200)"))
            db.session.commit()
            app.logger.info("成功添加 avatar 字段")
        except Exception as e:
            pass

        # 尝试自动迁移添加 banned_until 字段
        try:
            db.session.execute(text("ALTER TABLE users ADD COLUMN banned_until DATETIME"))
            db.session.commit()
            app.logger.info("成功添加 banned_until 字段")
        except Exception as e:
            pass

        # 尝试自动迁移添加实名相关字段
        try:
            db.session.execute(text("ALTER TABLE users ADD COLUMN real_name VARCHAR(80)"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN id_card VARCHAR(20)"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN is_verified BOOLEAN DEFAULT FALSE"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN verified_at DATETIME"))
            db.session.commit()
            app.logger.info("成功添加实名相关字段")
        except Exception as e:
            pass

        # 钱包字段
        try:
            db.session.execute(text("ALTER TABLE users ADD COLUMN wallet_balance DECIMAL(10,2) DEFAULT 0.00"))
            db.session.execute(text("ALTER TABLE users ADD COLUMN wallet_frozen DECIMAL(10,2) DEFAULT 0.00"))
            db.session.commit()
            app.logger.info("成功添加钱包字段")
        except Exception as e:
            pass
            
        # 尝试自动迁移添加 payment_status 字段
        try:
            db.session.execute(text("ALTER TABLE items ADD COLUMN payment_status VARCHAR(20) DEFAULT 'unpaid'"))
            db.session.execute(text("ALTER TABLE items ADD COLUMN shipping_name VARCHAR(80)"))
            db.session.execute(text("ALTER TABLE items ADD COLUMN shipping_phone VARCHAR(20)"))
            db.session.execute(text("ALTER TABLE items ADD COLUMN shipping_address VARCHAR(255)"))
            db.session.commit()
            app.logger.info("成功添加支付和物流字段")
        except Exception as e:
            app.logger.debug(f"尝试添加支付物流字段跳过 (可能已存在): {e}")

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
