"""
反思相关数据仓库

提供任务评估、反思日志、用户反馈的数据访问接口
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from sqlalchemy import desc, and_, or_
from db.local_db import get_db_session
from db.models import TaskEvaluation, ReflectionLog, UserFeedback
import logging

logger = logging.getLogger(__name__)


class EvaluationRepository:
    """任务评估数据仓库"""

    def save_evaluation(self, session_id: str, task_type: str, success: int,
                       success_rate: float = 0.0, completion_time: float = None,
                       turns_count: int = 0, error_type: str = None,
                       error_message: str = None, action_data: Dict = None) -> Optional[int]:
        """保存任务评估"""
        try:
            with get_db_session() as session:
                evaluation = TaskEvaluation(
                    session_id=session_id,
                    task_type=task_type,
                    success=success,
                    success_rate=success_rate,
                    completion_time=completion_time,
                    turns_count=turns_count,
                    error_type=error_type,
                    error_message=error_message,
                    action_data=action_data
                )
                session.add(evaluation)
                session.commit()
                return evaluation.id
        except Exception as e:
            logger.error(f"保存任务评估失败: {e}")
            return None

    def update_reflection_triggered(self, evaluation_id: int) -> bool:
        """更新反思触发标记"""
        try:
            with get_db_session() as session:
                evaluation = session.query(TaskEvaluation).get(evaluation_id)
                if evaluation:
                    evaluation.reflection_triggered = 1
                    session.commit()
                    return True
                return False
        except Exception as e:
            logger.error(f"更新反思标记失败: {e}")
            return False

    def get_recent_evaluations(self, task_type: str = None, days: int = 7,
                              limit: int = 100) -> List[Dict[str, Any]]:
        """获取最近的任务评估"""
        try:
            with get_db_session() as session:
                query = session.query(TaskEvaluation)
                
                if task_type:
                    query = query.filter(TaskEvaluation.task_type == task_type)
                
                cutoff_date = datetime.utcnow() - timedelta(days=days)
                query = query.filter(TaskEvaluation.created_at >= cutoff_date)
                
                evaluations = query.order_by(desc(TaskEvaluation.created_at)).limit(limit).all()
                
                return [self._to_dict(e) for e in evaluations]
        except Exception as e:
            logger.error(f"获取最近评估失败: {e}")
            return []

    def get_success_rate_stats(self, task_type: str = None, days: int = 30) -> Dict[str, Any]:
        """获取成功率统计"""
        try:
            with get_db_session() as session:
                query = session.query(TaskEvaluation)
                
                if task_type:
                    query = query.filter(TaskEvaluation.task_type == task_type)
                
                cutoff_date = datetime.utcnow() - timedelta(days=days)
                query = query.filter(TaskEvaluation.created_at >= cutoff_date)
                
                evaluations = query.all()
                
                if not evaluations:
                    return {"total": 0, "success_rate": 0, "avg_turns": 0}
                
                total = len(evaluations)
                success_count = sum(1 for e in evaluations if e.success == 1)
                avg_turns = sum(e.turns_count for e in evaluations) / total
                avg_rate = sum(e.success_rate for e in evaluations) / total
                
                return {
                    "total": total,
                    "success": success_count,
                    "failure": total - success_count,
                    "success_rate": success_count / total if total > 0 else 0,
                    "avg_success_rate": avg_rate,
                    "avg_turns": avg_turns,
                    "period_days": days
                }
        except Exception as e:
            logger.error(f"获取成功率统计失败: {e}")
            return {"total": 0, "success_rate": 0, "error": str(e)}

    def get_failed_evaluations(self, task_type: str = None, days: int = 7,
                              limit: int = 50) -> List[Dict[str, Any]]:
        """获取失败的任务评估（用于坏case分析）"""
        try:
            with get_db_session() as session:
                query = session.query(TaskEvaluation).filter(
                    or_(TaskEvaluation.success == 0, TaskEvaluation.success == 2)
                )
                
                if task_type:
                    query = query.filter(TaskEvaluation.task_type == task_type)
                
                cutoff_date = datetime.utcnow() - timedelta(days=days)
                query = query.filter(TaskEvaluation.created_at >= cutoff_date)
                
                evaluations = query.order_by(desc(TaskEvaluation.created_at)).limit(limit).all()
                
                return [self._to_dict(e) for e in evaluations]
        except Exception as e:
            logger.error(f"获取失败评估失败: {e}")
            return []

    def _to_dict(self, evaluation: TaskEvaluation) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "id": evaluation.id,
            "session_id": evaluation.session_id,
            "task_type": evaluation.task_type,
            "success": evaluation.success,
            "success_rate": evaluation.success_rate,
            "completion_time": evaluation.completion_time,
            "turns_count": evaluation.turns_count,
            "error_type": evaluation.error_type,
            "error_message": evaluation.error_message,
            "action_data": evaluation.action_data,
            "reflection_triggered": evaluation.reflection_triggered,
            "created_at": evaluation.created_at.isoformat() if evaluation.created_at else None
        }


class ReflectionRepository:
    """反思日志数据仓库"""

    def save_reflection(self, session_id: str, evaluation_id: int = None,
                       reflection_type: str = "post_task",
                       findings: Dict = None, recommendations: List = None,
                       patterns_discovered: List = None, bad_cases: List = None,
                       improvement_actions: List = None) -> Optional[int]:
        """保存反思日志"""
        try:
            with get_db_session() as session:
                log = ReflectionLog(
                    session_id=session_id,
                    evaluation_id=evaluation_id,
                    reflection_type=reflection_type,
                    findings=findings or {},
                    recommendations=recommendations or [],
                    patterns_discovered=patterns_discovered or [],
                    bad_cases=bad_cases or [],
                    improvement_actions=improvement_actions or []
                )
                session.add(log)
                session.commit()
                return log.id
        except Exception as e:
            logger.error(f"保存反思日志失败: {e}")
            return None

    def get_recent_reflections(self, days: int = 7, limit: int = 50) -> List[Dict[str, Any]]:
        """获取最近的反思日志"""
        try:
            with get_db_session() as session:
                cutoff_date = datetime.utcnow() - timedelta(days=days)
                reflections = session.query(ReflectionLog).filter(
                    ReflectionLog.created_at >= cutoff_date
                ).order_by(desc(ReflectionLog.created_at)).limit(limit).all()
                
                return [self._to_dict(r) for r in reflections]
        except Exception as e:
            logger.error(f"获取反思日志失败: {e}")
            return []

    def get_reflection_by_evaluation(self, evaluation_id: int) -> Optional[Dict[str, Any]]:
        """根据评估ID获取反思日志"""
        try:
            with get_db_session() as session:
                reflection = session.query(ReflectionLog).filter(
                    ReflectionLog.evaluation_id == evaluation_id
                ).first()
                
                return self._to_dict(reflection) if reflection else None
        except Exception as e:
            logger.error(f"获取评估关联的反思失败: {e}")
            return None

    def get_all_bad_cases(self, days: int = 30) -> List[Dict[str, Any]]:
        """获取所有坏case用于分析"""
        try:
            with get_db_session() as session:
                cutoff_date = datetime.utcnow() - timedelta(days=days)
                reflections = session.query(ReflectionLog).filter(
                    ReflectionLog.created_at >= cutoff_date
                ).all()
                
                all_bad_cases = []
                for r in reflections:
                    if r.bad_cases:
                        for bc in r.bad_cases:
                            bc['reflection_id'] = r.id
                            bc['created_at'] = r.created_at.isoformat() if r.created_at else None
                            all_bad_cases.append(bc)
                
                return all_bad_cases
        except Exception as e:
            logger.error(f"获取坏case失败: {e}")
            return []

    def get_actionable_recommendations(self) -> List[Dict[str, Any]]:
        """获取可执行的改进建议"""
        try:
            with get_db_session() as session:
                reflections = session.query(ReflectionLog).order_by(
                    desc(ReflectionLog.created_at)
                ).limit(20).all()
                
                recommendations = []
                for r in reflections:
                    if r.recommendations:
                        for rec in r.recommendations:
                            rec['reflection_id'] = r.id
                            rec['created_at'] = r.created_at.isoformat() if r.created_at else None
                            recommendations.append(rec)
                
                return recommendations
        except Exception as e:
            logger.error(f"获取改进建议失败: {e}")
            return []

    def _to_dict(self, reflection: ReflectionLog) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "id": reflection.id,
            "session_id": reflection.session_id,
            "evaluation_id": reflection.evaluation_id,
            "reflection_type": reflection.reflection_type,
            "findings": reflection.findings,
            "recommendations": reflection.recommendations,
            "patterns_discovered": reflection.patterns_discovered,
            "bad_cases": reflection.bad_cases,
            "improvement_actions": reflection.improvement_actions,
            "created_at": reflection.created_at.isoformat() if reflection.created_at else None
        }


class FeedbackRepository:
    """用户反馈数据仓库"""

    def save_feedback(self, session_id: str, user_id: str = "default_user",
                     feedback_type: str = "implicit", rating: int = None,
                     content: str = None, source: str = "explicit",
                     action_data: Dict = None) -> Optional[int]:
        """保存用户反馈"""
        try:
            with get_db_session() as session:
                feedback = UserFeedback(
                    session_id=session_id,
                    user_id=user_id,
                    feedback_type=feedback_type,
                    rating=rating,
                    content=content,
                    source=source,
                    action_data=action_data
                )
                session.add(feedback)
                session.commit()
                return feedback.id
        except Exception as e:
            logger.error(f"保存用户反馈失败: {e}")
            return None

    def get_user_feedbacks(self, user_id: str = "default_user",
                          feedback_type: str = None, days: int = 30) -> List[Dict[str, Any]]:
        """获取用户反馈"""
        try:
            with get_db_session() as session:
                query = session.query(UserFeedback).filter(
                    UserFeedback.user_id == user_id
                )
                
                if feedback_type:
                    query = query.filter(UserFeedback.feedback_type == feedback_type)
                
                cutoff_date = datetime.utcnow() - timedelta(days=days)
                query = query.filter(UserFeedback.created_at >= cutoff_date)
                
                feedbacks = query.order_by(desc(UserFeedback.created_at)).all()
                
                return [self._to_dict(f) for f in feedbacks]
        except Exception as e:
            logger.error(f"获取用户反馈失败: {e}")
            return []

    def get_rating_stats(self, user_id: str = "default_user", days: int = 30) -> Dict[str, Any]:
        """获取评分统计"""
        try:
            with get_db_session() as session:
                query = session.query(UserFeedback).filter(
                    UserFeedback.user_id == user_id,
                    UserFeedback.rating.isnot(None)
                )
                
                cutoff_date = datetime.utcnow() - timedelta(days=days)
                query = query.filter(UserFeedback.created_at >= cutoff_date)
                
                feedbacks = query.all()
                
                if not feedbacks:
                    return {"count": 0, "avg_rating": 0}
                
                ratings = [f.rating for f in feedbacks if f.rating]
                return {
                    "count": len(ratings),
                    "avg_rating": sum(ratings) / len(ratings) if ratings else 0,
                    "min_rating": min(ratings) if ratings else 0,
                    "max_rating": max(ratings) if ratings else 0
                }
        except Exception as e:
            logger.error(f"获取评分统计失败: {e}")
            return {"count": 0, "avg_rating": 0}

    def _to_dict(self, feedback: UserFeedback) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "id": feedback.id,
            "session_id": feedback.session_id,
            "user_id": feedback.user_id,
            "feedback_type": feedback.feedback_type,
            "rating": feedback.rating,
            "content": feedback.content,
            "source": feedback.source,
            "action_data": feedback.action_data,
            "created_at": feedback.created_at.isoformat() if feedback.created_at else None
        }
