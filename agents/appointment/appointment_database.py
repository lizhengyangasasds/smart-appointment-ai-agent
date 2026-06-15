"""
预约数据库操作器

负责处理预约相关的数据库操作
注意：现在通过Services层访问数据库，符合分层架构
"""

from typing import Dict, Any
from datetime import datetime
from config.time_config import time_config
from config.constants import busy_periods_dict


class AppointmentDatabase:
    """预约数据库操作器"""
    
    def __init__(self):
        # 延迟导入Services避免循环依赖
        self._appointment_service = None
        self._user_behavior_service = None
    
    @property
    def appointment_service(self):
        """懒加载预约服务"""
        if self._appointment_service is None:
            from services.appointment_service import AppointmentService
            self._appointment_service = AppointmentService()
        return self._appointment_service
    
    @property 
    def user_behavior_service(self):
        """懒加载用户行为服务"""
        if self._user_behavior_service is None:
            from services.user_behavior_service import UserBehaviorService
            self._user_behavior_service = UserBehaviorService()
        return self._user_behavior_service
    
    def save_appointment(self, technician_id: str, start_time: datetime, 
                        end_time: datetime, appointment_history: Dict[str, Any], 
                        session_id: str) -> bool:
        """保存预约信息到数据库"""
        try:
            # 通过Services层保存预约
            success = self.appointment_service.save_appointment(
                technician_id, start_time, end_time, appointment_history, session_id
            )
            
            if success:
                # 记录用户行为
                self._record_user_behavior(start_time, end_time, technician_id, 
                                         appointment_history, session_id)
            
            return success
            
        except Exception as e:
            print(f"保存预约信息到数据库失败：{e}")
            return False
    
    def update_memory_schedule(self, technician_id: str, start_time: datetime, end_time: datetime):
        """更新内存中的技师忙碌时间段"""
        busy_period = {
            "start": time_config.format_datetime(start_time, "%H:%M"),
            "end": time_config.format_datetime(end_time, "%H:%M")
        }
        busy_periods_dict.setdefault(technician_id, []).append(busy_period)
    
    def _record_user_behavior(self, start_time: datetime, end_time: datetime,
                            technician_id: str, appointment_history: Dict[str, Any], 
                            session_id: str):
        """记录用户预约行为"""
        try:
            action_data = {
                'start_time': time_config.format_datetime(start_time, "%Y-%m-%d %H:%M:%S"),
                'end_time': time_config.format_datetime(end_time, "%Y-%m-%d %H:%M:%S"),
                'duration': int((end_time - start_time).total_seconds() / 60),
                'project': appointment_history.get('project', 'massage'),
                'preference': appointment_history.get('preference', ''),
                'technician_id': technician_id
            }
            
            # 通过Services层记录用户行为
            self.user_behavior_service.record_behavior(
                user_id="default_user",  # 统一使用default_user作为用户ID
                action_type='appointment',
                action_data=action_data,
                technician_id=str(technician_id),
                session_id=session_id
            )
            
        except Exception as behavior_error:
            print(f"记录用户行为失败（但预约仍然成功）：{behavior_error}")
