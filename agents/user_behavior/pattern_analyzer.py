"""
简化版用户行为分析器

核心功能：
1. 分析用户最喜欢的技师
2. 分析用户常用的服务项目和时长
3. 判断用户是否需要回访邀请
4. 生成个性化回访消息
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import logging


class PatternAnalyzer:
    """简化版用户行为分析器"""
    
    def __init__(self, behavior_service = None):
        self.behavior_service = behavior_service
        self.logger = logging.getLogger(__name__)
    
    @property
    def behavior_db(self):
        """为了向后兼容，提供behavior_db属性"""
        if hasattr(self, 'behavior_service') and self.behavior_service:
            # 返回一个适配器，将调用转发到behavior_service
            return self.behavior_service.user_behavior_repo
        else:
            # 如果没有service，返回None（应该在组件初始化时提供适当的处理）
            return None
    
    def analyze_user_preferences(self, user_id: str = "default_user") -> Optional[Dict[str, Any]]:
        """分析用户偏好：最喜欢的技师、常用服务、常用时长"""
        try:
            # 获取用户所有预约历史
            if self.behavior_service:
                appointments = self.behavior_service.get_user_behaviors(
                    user_id=user_id,
                    action_type='appointment'
                )
            else:
                # 向后兼容
                appointments = self.behavior_db.get_user_behaviors(
                    user_id=user_id,
                    action_type='appointment'
                )
            
            if not appointments:
                return None
            
            # 统计技师偏好
            technician_counts = {}
            service_counts = {}
            duration_counts = {}
            
            for appointment in appointments:
                data = appointment.get('action_data', {})
                
                # 统计技师 - 技师ID存储在单独字段中
                tech_id = appointment.get('technician_id')  # 从记录本身获取
                if tech_id:
                    technician_counts[tech_id] = technician_counts.get(tech_id, 0) + 1
                
                # 统计服务项目
                service = data.get('project')
                if service:
                    service_counts[service] = service_counts.get(service, 0) + 1
                
                # 统计时长
                duration = data.get('duration')
                if duration:
                    duration_counts[duration] = duration_counts.get(duration, 0) + 1
            
            # 找出最偏爱的选项
            favorite_technician = max(technician_counts, key=technician_counts.get) if technician_counts else None
            favorite_service = max(service_counts, key=service_counts.get) if service_counts else None
            favorite_duration = max(duration_counts, key=duration_counts.get) if duration_counts else None
            
            return {
                'favorite_technician_id': favorite_technician,
                'favorite_service': favorite_service,
                'favorite_duration': favorite_duration,
                'total_appointments': len(appointments),
                'last_appointment_date': appointments[0]['created_at'] if appointments else None
            }
            
        except Exception as e:
            self.logger.error(f"分析用户偏好失败: {str(e)}")
            return None
    
    def should_send_return_reminder(self, user_id: str = "default_user", days_threshold: int = 30) -> bool:
        """判断是否应该发送回访提醒"""
        try:
            preferences = self.analyze_user_preferences(user_id)
            if not preferences or preferences['total_appointments'] < 2:
                return False
            
            last_appointment = preferences['last_appointment_date']
            if not last_appointment:
                return False
            
            # 确保是datetime对象
            if isinstance(last_appointment, str):
                last_appointment = datetime.fromisoformat(last_appointment.replace('Z', '+00:00'))
            
            # 计算距离上次预约的天数
            days_since_last = (datetime.now() - last_appointment).days
            
            # 如果超过阈值天数，则需要发送提醒
            return days_since_last >= days_threshold
            
        except Exception as e:
            self.logger.error(f"判断回访提醒失败: {str(e)}")
            return False
    
    def generate_return_message(self, user_id: str = "default_user") -> Optional[str]:
        """生成个性化回访消息"""
        try:
            preferences = self.analyze_user_preferences(user_id)
            if not preferences:
                return "您好！好久没见了，要不要预约一个按摩放松一下？"
            
            # 获取技师信息
            tech_id = preferences.get('favorite_technician_id')
            service = preferences.get('favorite_service', '按摩')
            duration = preferences.get('favorite_duration', 60)
            
            # 构建个性化消息
            if tech_id:
                from db import TechnicianDBRouter
                db = TechnicianDBRouter()
                tech_info = db.get_technician_by_id(tech_id)
                tech_name = tech_info.get('name', '您偏爱的技师') if tech_info else '您偏爱的技师'
                
                message = f"您好！{tech_name}最近有空档，您之前很喜欢他/她的{service}服务。"
                if duration:
                    message += f"按照您习惯的{duration}分钟，"
                message += "要不要预约一下放松一下？"
            else:
                message = f"您好！好久没见了，要不要预约一个{service}服务放松一下？"
                if duration:
                    message += f"按您习惯的{duration}分钟怎么样？"
            
            return message
            
        except Exception as e:
            self.logger.error(f"生成回访消息失败: {str(e)}")
            return "您好！好久没见了，要不要预约一个按摩放松一下？"
