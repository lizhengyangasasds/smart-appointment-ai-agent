"""
用户行为服务层

职责：
1. 封装用户行为相关的数据库操作
2. 处理用户行为分析业务逻辑
3. 提供用户偏好管理服务
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from db.db_router import DatabaseRouter
import logging

logger = logging.getLogger(__name__)

class UserBehaviorService:
    """用户行为服务类"""
    
    def __init__(self, db_path: str = 'sqlite:///data/smart_appointment.db'):
        self.db_router = DatabaseRouter(db_path)
        self.user_behavior_repo = self.db_router.user_behavior
    
    def record_behavior(self, user_id: str, action_type: str, action_data: Dict[str, Any] = None,
                       technician_id: str = None, session_id: str = "default_session") -> bool:
        """记录用户行为"""
        try:
            behavior_id = self.user_behavior_repo.record_behavior(
                user_id=user_id,
                action_type=action_type,
                action_data=action_data,
                technician_id=technician_id,
                session_id=session_id
            )
            
            if behavior_id:
                logger.info(f"用户行为记录成功：用户={user_id}, 行为={action_type}, ID={behavior_id}")
                return True
            return False
            
        except Exception as e:
            logger.error(f"记录用户行为失败：{e}")
            return False
    
    def get_user_behaviors(self, user_id: str, action_type: str = None, 
                          days_back: int = None) -> List[Dict[str, Any]]:
        """获取用户行为记录"""
        try:
            return self.user_behavior_repo.get_user_behaviors(user_id, action_type, days_back)
        except Exception as e:
            logger.error(f"获取用户行为记录失败：{e}")
            return []
    
    def get_user_preferences(self, user_id: str) -> List[Dict[str, Any]]:
        """获取用户偏好"""
        try:
            return self.user_behavior_repo.get_user_preferences(user_id)
        except Exception as e:
            logger.error(f"获取用户偏好失败：{e}")
            return []
    
    def update_user_preference(self, user_id: str, preference_type: str, 
                             preference_value: str, confidence_score: int = 1) -> bool:
        """更新用户偏好"""
        try:
            return self.user_behavior_repo.update_user_preference(
                user_id, preference_type, preference_value, confidence_score
            )
        except Exception as e:
            logger.error(f"更新用户偏好失败：{e}")
            return False
    
    def analyze_user_patterns(self, user_id: str) -> Dict[str, Any]:
        """分析用户行为模式"""
        try:
            behaviors = self.get_user_behaviors(user_id, days_back=30)
            
            if not behaviors:
                return {"pattern": "no_data", "recommendation": "需要更多数据"}
            
            # 分析预约频率
            appointment_behaviors = [b for b in behaviors if b.get('action_type') == 'appointment']
            freq_analysis = self._analyze_frequency(appointment_behaviors)
            
            # 分析偏好技师
            preferred_technician = self._analyze_preferred_technician(appointment_behaviors)
            
            # 分析时间偏好
            time_preference = self._analyze_time_preference(appointment_behaviors)
            
            return {
                "pattern": "active_user" if len(appointment_behaviors) > 2 else "occasional_user",
                "frequency_analysis": freq_analysis,
                "preferred_technician": preferred_technician,
                "time_preference": time_preference,
                "total_appointments": len(appointment_behaviors),
                "analysis_period_days": 30
            }
            
        except Exception as e:
            logger.error(f"分析用户行为模式失败：{e}")
            return {"pattern": "analysis_error", "error": str(e)}
    
    def _analyze_frequency(self, appointment_behaviors: List[Dict[str, Any]]) -> Dict[str, Any]:
        """分析预约频率"""
        if not appointment_behaviors:
            return {"frequency": "no_appointments", "days_between": 0}
        
        if len(appointment_behaviors) < 2:
            return {"frequency": "single_appointment", "days_between": 0}
        
        # 计算平均间隔天数
        dates = []
        for behavior in appointment_behaviors:
            if 'created_at' in behavior:
                try:
                    date = datetime.fromisoformat(behavior['created_at'].replace('Z', '+00:00'))
                    dates.append(date)
                except:
                    continue
        
        if len(dates) < 2:
            return {"frequency": "insufficient_data", "days_between": 0}
        
        dates.sort()
        intervals = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
        avg_interval = sum(intervals) / len(intervals)
        
        if avg_interval < 7:
            frequency = "very_frequent"
        elif avg_interval < 14:
            frequency = "frequent"
        elif avg_interval < 30:
            frequency = "regular"
        else:
            frequency = "occasional"
        
        return {"frequency": frequency, "days_between": avg_interval}
    
    def _analyze_preferred_technician(self, appointment_behaviors: List[Dict[str, Any]]) -> Optional[str]:
        """分析偏好技师"""
        technician_counts = {}
        
        for behavior in appointment_behaviors:
            technician_id = behavior.get('technician_id')
            if technician_id:
                technician_counts[technician_id] = technician_counts.get(technician_id, 0) + 1
        
        if not technician_counts:
            return None
        
        # 返回预约次数最多的技师
        most_frequent_technician = max(technician_counts, key=technician_counts.get)
        return most_frequent_technician if technician_counts[most_frequent_technician] > 1 else None
    
    def _analyze_time_preference(self, appointment_behaviors: List[Dict[str, Any]]) -> Dict[str, Any]:
        """分析时间偏好"""
        hours = []
        weekdays = []
        
        for behavior in appointment_behaviors:
            action_data = behavior.get('action_data', {})
            if isinstance(action_data, dict) and 'start_time' in action_data:
                try:
                    start_time = datetime.fromisoformat(action_data['start_time'])
                    hours.append(start_time.hour)
                    weekdays.append(start_time.weekday())
                except:
                    continue
        
        if not hours:
            return {"preferred_hour": None, "preferred_weekday": None}
        
        # 找出最常见的小时和星期
        from collections import Counter
        hour_counter = Counter(hours)
        weekday_counter = Counter(weekdays)
        
        preferred_hour = hour_counter.most_common(1)[0][0] if hour_counter else None
        preferred_weekday = weekday_counter.most_common(1)[0][0] if weekday_counter else None
        
        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        preferred_weekday_name = weekday_names[preferred_weekday] if preferred_weekday is not None else None
        
        return {
            "preferred_hour": preferred_hour,
            "preferred_weekday": preferred_weekday_name,
            "hour_distribution": dict(hour_counter),
            "weekday_distribution": dict(weekday_counter)
        }
