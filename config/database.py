"""
数据库配置模块
"""
import os
from typing import Optional

class DatabaseConfig:
    """数据库配置类"""
    
    def __init__(self):
        self.db_path = os.getenv('DATABASE_URL', 'sqlite:///data/smart_appointment.db')
        self.echo = os.getenv('DB_ECHO', 'false').lower() == 'true'
        self.pool_size = int(os.getenv('DB_POOL_SIZE', '10'))
        self.max_overflow = int(os.getenv('DB_MAX_OVERFLOW', '20'))
    
    @property
    def connection_string(self) -> str:
        """获取数据库连接字符串"""
        return self.db_path
    
    @property
    def is_sqlite(self) -> bool:
        """判断是否为SQLite数据库"""
        return self.db_path.startswith('sqlite')
    
    def get_engine_kwargs(self) -> dict:
        """获取数据库引擎参数"""
        kwargs = {
            'echo': self.echo,
        }
        
        # SQLite不支持连接池
        if not self.is_sqlite:
            kwargs.update({
                'pool_size': self.pool_size,
                'max_overflow': self.max_overflow,
            })
        
        return kwargs

# 全局数据库配置实例
db_config = DatabaseConfig()
