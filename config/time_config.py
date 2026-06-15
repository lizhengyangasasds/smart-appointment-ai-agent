"""
时间配置模块

统一管理系统时间，确保所有组件使用相同的时间基准
"""

from datetime import datetime, timezone, timedelta
from typing import Optional


class TimeConfig:
    """时间配置类"""
    
    # 设置北京时区 (UTC+8)
    BEIJING_TZ = timezone(timedelta(hours=8))
    
    @classmethod
    def now(cls) -> datetime:
        """获取当前北京时间"""
        # 先获取UTC时间，然后转换为北京时间
        utc_now = datetime.now(timezone.utc)
        beijing_now = utc_now.astimezone(cls.BEIJING_TZ)
        return beijing_now
    
    @classmethod
    def today(cls) -> datetime:
        """获取今天的日期（北京时间）"""
        return cls.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    @classmethod
    def current_date_str(cls, format_str: str = "%Y年%m月%d日") -> str:
        """获取当前日期字符串（北京时间）"""
        return cls.now().strftime(format_str)
    
    @classmethod
    def current_datetime_str(cls, format_str: str = "%Y-%m-%d %H:%M") -> str:
        """获取当前日期时间字符串（北京时间）"""
        return cls.now().strftime(format_str)
    
    @classmethod
    def parse_datetime(cls, date_str: str, format_str: str = "%Y-%m-%d %H:%M") -> Optional[datetime]:
        """解析日期时间字符串为北京时间的datetime对象"""
        try:
            dt = datetime.strptime(date_str, format_str)
            # 如果没有时区信息，假设是北京时间
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=cls.BEIJING_TZ)
            return dt
        except ValueError:
            return None
    
    @classmethod
    def format_datetime(cls, dt: datetime, format_str: str = "%Y-%m-%d %H:%M") -> str:
        """格式化datetime对象为字符串"""
        if dt.tzinfo is None:
            # 如果没有时区信息，假设是北京时间
            dt = dt.replace(tzinfo=cls.BEIJING_TZ)
        elif dt.tzinfo != cls.BEIJING_TZ:
            # 如果是其他时区，转换为北京时间
            dt = dt.astimezone(cls.BEIJING_TZ)
        
        return dt.strftime(format_str)
    
    @classmethod
    def get_business_hours(cls) -> tuple:
        """获取营业时间范围"""
        return (12, 22)  # 12:00 - 22:00
    
    @classmethod
    def is_business_time(cls, dt: Optional[datetime] = None) -> bool:
        """检查是否在营业时间内"""
        if dt is None:
            dt = cls.now()
        
        hour = dt.hour
        start_hour, end_hour = cls.get_business_hours()
        return start_hour <= hour < end_hour


# 创建全局实例
time_config = TimeConfig()
