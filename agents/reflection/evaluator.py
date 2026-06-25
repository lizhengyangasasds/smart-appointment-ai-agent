"""
任务评估器 - 评估每次任务执行的质量

核心功能：
1. 评估任务是否成功
2. 计算成功率评分
3. 记录错误类型和消息
4. 触发反思机制
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import IntEnum
import logging


class SuccessLevel(IntEnum):
    """任务成功级别"""
    FAILED = 0       # 完全失败
    PARTIAL = 1      # 部分成功
    SUCCESS = 2      # 完全成功


class TaskEvaluator:
    """任务评估器"""

    # 反思触发阈值配置
    DEFAULT_THRESHOLDS = {
        'success_rate': 0.7,        # 成功率低于70%触发反思
        'turns_high': 10,           # 对话轮数超过10轮触发反思
        'completion_time': 120,     # 完成时间超过120秒触发反思
    }

    def __init__(self, evaluation_repo=None, threshold_config: Dict = None):
        self.evaluation_repo = evaluation_repo
        self.thresholds = threshold_config or self.DEFAULT_THRESHOLDS.copy()
        self.logger = logging.getLogger(__name__)

    def evaluate_appointment_task(
        self,
        session_id: str,
        appointment_history: Dict[str, Any],
        turns_count: int,
        completion_time: float = None,
        error: Exception = None
    ) -> Dict[str, Any]:
        """
        评估预约任务

        Args:
            session_id: 会话ID
            appointment_history: 预约历史数据
            turns_count: 对话轮数
            completion_time: 完成耗时（秒）
            error: 异常信息

        Returns:
            评估结果字典
        """
        # 判断任务是否完成
        required_fields = ['gender', 'start_time', 'duration', 'project']
        completed_fields = [f for f in required_fields if appointment_history.get(f)]
        completion_rate = len(completed_fields) / len(required_fields)

        # 判断成功级别
        if error:
            success = SuccessLevel.FAILED
            success_rate = 0.0
            error_type = self._classify_error(error)
        elif completion_rate >= 1.0:
            success = SuccessLevel.SUCCESS
            success_rate = 1.0
            error_type = None
        elif completion_rate >= 0.5:
            success = SuccessLevel.PARTIAL
            success_rate = completion_rate
            error_type = 'incomplete_info'
        else:
            success = SuccessLevel.FAILED
            success_rate = completion_rate
            error_type = 'low_completion'

        # 判断是否触发反思
        should_reflect = self._should_trigger_reflection(
            success_rate=success_rate,
            turns_count=turns_count,
            completion_time=completion_time,
            success=success
        )

        # 保存评估记录
        evaluation_id = None
        if self.evaluation_repo:
            evaluation_id = self.evaluation_repo.save_evaluation(
                session_id=session_id,
                task_type='appointment',
                success=int(success),
                success_rate=success_rate,
                completion_time=completion_time,
                turns_count=turns_count,
                error_type=error_type,
                error_message=str(error) if error else None,
                action_data=appointment_history
            )

        return {
            'evaluation_id': evaluation_id,
            'success': int(success),
            'success_level': success.name,
            'success_rate': success_rate,
            'completion_rate': completion_rate,
            'completed_fields': completed_fields,
            'missing_fields': [f for f in required_fields if f not in completed_fields],
            'turns_count': turns_count,
            'completion_time': completion_time,
            'error_type': error_type,
            'should_reflect': should_reflect,
            'timestamp': datetime.now().isoformat()
        }

    def evaluate_consultation_task(
        self,
        session_id: str,
        consultation_data: Dict[str, Any],
        turns_count: int,
        completion_time: float = None,
        error: Exception = None
    ) -> Dict[str, Any]:
        """
        评估咨询任务

        Args:
            session_id: 会话ID
            consultation_data: 咨询数据
            turns_count: 对话轮数
            completion_time: 完成耗时（秒）
            error: 异常信息

        Returns:
            评估结果字典
        """
        # 判断咨询是否成功
        has_answer = consultation_data.get('has_answer', False)
        answer_quality = consultation_data.get('answer_quality', 0.0)
        knowledge_hit = consultation_data.get('knowledge_hit', False)

        if error:
            success = SuccessLevel.FAILED
            success_rate = 0.0
            error_type = self._classify_error(error)
        elif knowledge_hit and answer_quality >= 0.7:
            success = SuccessLevel.SUCCESS
            success_rate = max(answer_quality, 0.8)
            error_type = None
        elif has_answer:
            success = SuccessLevel.PARTIAL
            success_rate = answer_quality
            error_type = None
        else:
            success = SuccessLevel.FAILED
            success_rate = 0.0
            error_type = 'no_answer'

        should_reflect = self._should_trigger_reflection(
            success_rate=success_rate,
            turns_count=turns_count,
            completion_time=completion_time,
            success=success
        )

        evaluation_id = None
        if self.evaluation_repo:
            evaluation_id = self.evaluation_repo.save_evaluation(
                session_id=session_id,
                task_type='consultation',
                success=int(success),
                success_rate=success_rate,
                completion_time=completion_time,
                turns_count=turns_count,
                error_type=error_type,
                error_message=str(error) if error else None,
                action_data=consultation_data
            )

        return {
            'evaluation_id': evaluation_id,
            'success': int(success),
            'success_level': success.name,
            'success_rate': success_rate,
            'has_answer': has_answer,
            'answer_quality': answer_quality,
            'knowledge_hit': knowledge_hit,
            'turns_count': turns_count,
            'completion_time': completion_time,
            'error_type': error_type,
            'should_reflect': should_reflect,
            'timestamp': datetime.now().isoformat()
        }

    def evaluate_classification_task(
        self,
        session_id: str,
        classification_data: Dict[str, Any],
        turns_count: int,
        error: Exception = None
    ) -> Dict[str, Any]:
        """
        评估任务分类任务

        Args:
            session_id: 会话ID
            classification_data: 分类数据
            turns_count: 对话轮数
            error: 异常信息

        Returns:
            评估结果字典
        """
        correctly_classified = classification_data.get('correctly_classified', True)

        if error:
            success = SuccessLevel.FAILED
            success_rate = 0.0
            error_type = self._classify_error(error)
        elif correctly_classified:
            success = SuccessLevel.SUCCESS
            success_rate = 1.0
            error_type = None
        else:
            success = SuccessLevel.PARTIAL
            success_rate = 0.5
            error_type = 'misclassification'

        # 分类任务一般不触发反思，除非出错
        should_reflect = success == SuccessLevel.FAILED

        evaluation_id = None
        if self.evaluation_repo:
            evaluation_id = self.evaluation_repo.save_evaluation(
                session_id=session_id,
                task_type='classification',
                success=int(success),
                success_rate=success_rate,
                turns_count=turns_count,
                error_type=error_type,
                error_message=str(error) if error else None,
                action_data=classification_data
            )

        return {
            'evaluation_id': evaluation_id,
            'success': int(success),
            'success_level': success.name,
            'success_rate': success_rate,
            'correctly_classified': correctly_classified,
            'turns_count': turns_count,
            'error_type': error_type,
            'should_reflect': should_reflect,
            'timestamp': datetime.now().isoformat()
        }

    def _should_trigger_reflection(
        self,
        success_rate: float,
        turns_count: int,
        completion_time: float = None,
        success: SuccessLevel = None
    ) -> bool:
        """判断是否应该触发反思"""
        # 失败任务总是触发反思
        if success == SuccessLevel.FAILED:
            return True

        # 成功率低于阈值
        if success_rate < self.thresholds['success_rate']:
            return True

        # 对话轮数过多
        if turns_count > self.thresholds['turns_high']:
            return True

        # 完成时间过长
        if completion_time and completion_time > self.thresholds['completion_time']:
            return True

        return False

    def _classify_error(self, error: Exception) -> str:
        """分类错误类型"""
        error_msg = str(error).lower()

        if 'timeout' in error_msg or 'timed out' in error_msg:
            return 'timeout'
        elif 'slot' in error_msg or 'taken' in error_msg:
            return 'slot_unavailable'
        elif 'parse' in error_msg or 'json' in error_msg:
            return 'parse_error'
        elif 'database' in error_msg or 'db' in error_msg:
            return 'database_error'
        elif 'llm' in error_msg or 'model' in error_msg or 'api' in error_msg:
            return 'llm_error'
        elif 'permission' in error_msg or 'auth' in error_msg:
            return 'auth_error'
        else:
            return 'unknown_error'

    def update_thresholds(self, **kwargs):
        """更新反思触发阈值"""
        self.thresholds.update(kwargs)

    def get_statistics(self, days: int = 30) -> Dict[str, Any]:
        """获取评估统计"""
        if not self.evaluation_repo:
            return {}

        stats = {}
        for task_type in ['appointment', 'consultation', 'classification']:
            stats[task_type] = self.evaluation_repo.get_success_rate_stats(
                task_type=task_type,
                days=days
            )

        return stats
