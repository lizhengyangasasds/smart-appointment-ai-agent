from contextlib import contextmanager
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import NullPool
import threading
from ..models import Base
from ..models_memory import Base as MemoryBase


# 进程级写锁，防止多线程/多协程并发写入 SQLite
_write_lock = threading.RLock()


class SessionManager:
    """
    数据库会话管理器
    
    职责：
    1. 管理数据库连接和会话
    2. 提供统一的会话上下文管理
    3. 处理事务和异常回滚
    """
    
    def __init__(self, db_path='sqlite:///data/smart_appointment.db'):
        """
        初始化会话管理器
        
        Args:
            db_path: 数据库连接路径
        """
        self.engine = create_engine(
            db_path,
            connect_args={"timeout": 30, "check_same_thread": False},
            pool_pre_ping=True,
            poolclass=NullPool,
        )

        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-64000")
            cursor.execute("PRAGMA wal_autocheckpoint=100")
            cursor.close()

        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA busy_timeout=30000"))
            conn.commit()
        Base.metadata.create_all(self.engine)
        MemoryBase.metadata.create_all(self.engine)
        self.Session = scoped_session(
            sessionmaker(bind=self.engine, expire_on_commit=False)
        )

    @contextmanager
    def session_scope(self, exclusive: bool = False):
        """
        提供会话上下文管理
        
        Args:
            exclusive: 是否获取写锁（所有写操作应传 True）
            
        自动处理：
        - 会话创建和关闭
        - 事务提交和回滚
        - 异常处理
        - 写锁获取与释放
        """
        if exclusive:
            acquired = _write_lock.acquire(timeout=60)
            if not acquired:
                raise TimeoutError("Could not acquire database write lock within 60s")
        try:
            session = self.Session()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        finally:
            if exclusive:
                _write_lock.release()

    def close(self):
        """关闭会话管理器"""
        self.Session.remove()
        self.engine.dispose()
