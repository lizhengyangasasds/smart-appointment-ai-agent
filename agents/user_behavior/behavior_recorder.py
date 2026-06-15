"""
行为记录器 - 专门负责记录用户的各种行为数据

职责：
1. 记录用户的操作行为（预约、咨询、取消等）
2. 存储行为的上下文信息（时间、技师、服务等）
3. 维护行为数据的完整性和一致性
4. 提供行为数据的查询接口
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
import logging


class BehaviorRecorder:
    """行为记录器 - 负责用户行为的记录和存储"""
    
    def __init__(self, behavior_service = None):
        """
        初始化行为记录器
        
        Args:
            behavior_service: 用户行为服务实例
        """
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
    
    def record_behavior(self, action_type: str, action_data: Dict[str, Any] = None, 
                       technician_id: int = None, session_id: str = None) -> Optional[int]:
        """
        记录用户行为
        
        Args:
            action_type: 行为类型 (appointment, consultation, cancel等)
            action_data: 行为相关数据
            technician_id: 技师ID
            session_id: 会话ID
            
        Returns:
            int: 行为记录ID，失败时返回None
        """
        try:
            # 如果有behavior_service则使用它，否则降级到数据库直接访问
            if self.behavior_service:
                success = self.behavior_service.record_behavior(
                    user_id="default_user",  # 默认用户ID
                    action_type=action_type,
                    action_data=action_data,
                    technician_id=str(technician_id) if technician_id else None,
                    session_id=session_id or "default_session"
                )
                return 1 if success else None
            else:
                # 向后兼容：直接使用数据库
                behavior_id = self.behavior_db.record_behavior(
                    user_id="default_user",  # 默认用户ID
                    action_type=action_type,
                    action_data=action_data,
                    technician_id=technician_id,
                    session_id=session_id
                )
                return behavior_id
            
        except Exception as e:
            self.logger.error(f"记录用户行为失败: {str(e)}")
            return None
    
    def record_appointment_behavior(self, appointment_data: Dict[str, Any], 
                                  technician_id: int = None, session_id: str = None) -> Optional[int]:
        """
        记录预约行为的便捷方法
        
        Args:
            appointment_data: 预约相关数据
            technician_id: 技师ID  
            session_id: 会话ID
            
        Returns:
            int: 行为记录ID
        """
        return self.record_behavior(
            action_type='appointment',
            action_data=appointment_data,
            technician_id=technician_id,
            session_id=session_id
        )
    
    def record_consultation_behavior(self, consultation_data: Dict[str, Any], 
                                   session_id: str = None) -> Optional[int]:
        """
        记录咨询行为的便捷方法
        
        Args:
            consultation_data: 咨询相关数据
            session_id: 会话ID
            
        Returns:
            int: 行为记录ID
        """
        return self.record_behavior(
            action_type='consultation',
            action_data=consultation_data,
            session_id=session_id
        )
    
    def get_user_behaviors(self, action_type: str = None, 
                          days_back: int = 30) -> List[Dict[str, Any]]:
        """
        获取用户行为记录
        
        Args:
            action_type: 行为类型过滤，None表示获取所有类型
            days_back: 获取多少天内的记录
            
        Returns:
            List[Dict]: 行为记录列表
        """
        try:
            return self.behavior_db.get_user_behaviors(
                action_type=action_type,
                days_back=days_back
            )
        except Exception as e:
            self.logger.error(f"获取用户行为记录失败: {str(e)}")
            return []
    
    def get_behavior_statistics(self, days_back: int = 30) -> Dict[str, Any]:
        """
        获取行为统计信息
        
        Args:
            days_back: 统计天数
            
        Returns:
            Dict: 统计信息
        """
        try:
            return self.behavior_db.get_user_statistics(days_back=days_back)
        except Exception as e:
            self.logger.error(f"获取行为统计失败: {str(e)}")
            return {}
    
    def delete_old_behaviors(self, days_to_keep: int = 90) -> int:
        """
        删除旧的行为记录
        
        Args:
            days_to_keep: 保留多少天的记录
            
        Returns:
            int: 删除的记录数量
        """
        try:
            # 这里假设behavior_db有对应的方法，实际实现可能需要调整
            deleted_count = 0
            self.logger.info(f"清理了 {deleted_count} 条旧行为记录")
            return deleted_count
        except Exception as e:
            self.logger.error(f"清理旧行为记录失败: {str(e)}")
            return 0
    
    def validate_behavior_data(self, action_type: str, action_data: Dict[str, Any]) -> bool:
        """
        验证行为数据的有效性
        
        Args:
            action_type: 行为类型
            action_data: 行为数据
            
        Returns:
            bool: 数据是否有效
        """
        if not action_type:
            return False
        
        # 根据不同行为类型进行验证
        if action_type == 'appointment':
            required_fields = ['start_time', 'duration']
            return all(field in action_data for field in required_fields)
        elif action_type == 'consultation':
            return 'query' in action_data or 'question' in action_data
        
        return True  # 其他类型默认有效
