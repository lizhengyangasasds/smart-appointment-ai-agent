"""
反思相关数据仓库

提供任务评估、反思日志、用户反馈的数据访问接口
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from sqlalchemy import desc, and_, or_
from db.local_db import get_db_session
from db.models import TaskEvaluation, ReflectionLog, UserFeedback, StrategyVersion
import logging

logger = logging.getLogger(__name__)


def _make_json_safe(obj: Any) -> Any:
    """递归把对象转成 json.dumps 可处理的类型

    处理 datetime / set / tuple / 自定义对象等不可直接序列化对象。
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_make_json_safe(v) for v in obj]
    if hasattr(obj, "isoformat"):
        try:
            return obj.isoformat()
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return _make_json_safe(obj.__dict__)
    return str(obj)


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
                # SuccessLevel: 0=FAILED, 1=PARTIAL, 2=SUCCESS
                # 成功率统计：FAILED 视为失败，其余（PARTIAL + SUCCESS）均计入成功
                success_count = sum(1 for e in evaluations if e.success >= 1)
                failure_count = sum(1 for e in evaluations if e.success == 0)
                avg_turns = sum(e.turns_count for e in evaluations) / total
                avg_rate = sum(e.success_rate for e in evaluations) / total

                return {
                    "total": total,
                    "success": success_count,
                    "failure": failure_count,
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
                # 只保留真正失败的评估（success==0 即 SuccessLevel.FAILED）
                # 之前误写成 success==0 OR success==2，把完全成功的也当成坏 case，已修正
                query = session.query(TaskEvaluation).filter(
                    TaskEvaluation.success == 0
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

    def get_evaluations_by_task_type(self, task_type: str, start_time: datetime = None, end_time: datetime = None, limit: int = 1000) -> List[Dict[str, Any]]:
        """按任务类型 + 时间窗获取评估（用于闭环 Before/After 对比）

        与 get_recent_evaluations 的区别：支持显式 start_time/end_time，
        而不仅以 days 回溯。
        """
        try:
            with get_db_session() as session:
                query = session.query(TaskEvaluation).filter(
                    TaskEvaluation.task_type == task_type
                )

                if start_time is not None:
                    query = query.filter(TaskEvaluation.created_at >= start_time)
                if end_time is not None:
                    query = query.filter(TaskEvaluation.created_at <= end_time)

                query = query.order_by(desc(TaskEvaluation.created_at)).limit(limit)
                return [self._to_dict(e) for e in query.all()]
        except Exception as e:
            logger.error(f"按任务类型获取评估失败: {e}")
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
                    findings=_make_json_safe(findings) if findings else {},
                    recommendations=_make_json_safe(recommendations) if recommendations else [],
                    patterns_discovered=_make_json_safe(patterns_discovered) if patterns_discovered else [],
                    bad_cases=_make_json_safe(bad_cases) if bad_cases else [],
                    improvement_actions=_make_json_safe(improvement_actions) if improvement_actions else []
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
                        recs = r.recommendations if isinstance(r.recommendations, list) else [r.recommendations]
                        for rec in recs:
                            if not isinstance(rec, dict):
                                continue
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


class StrategyRepository:
    """策略版本数据仓库

    负责 strategy_versions 表的持久化与状态同步。

    设计要点：
    1. 内存版 StrategyUpdater 是热数据；本仓库只做增量写入和回读
    2. 每次 activate_strategy 时把对应版本 is_active=1，同 type 的其他版本 is_active=0
       —— 保证"同 type 下唯一活跃"的约束由 DB 层兜底
    3. 提供 load_all_active() 让 StrategyUpdater 在启动时恢复状态
    """

    def save_version(
        self,
        version_id: str,
        strategy_type: str,
        name: str,
        config: Dict[str, Any],
        priority: int = 0,
        trigger_reason: str = "",
        status: str = "pending",
        created_by: str = "system",
        meta: Dict[str, Any] = None,
    ) -> Optional[int]:
        """插入一条策略版本（idempotent：同 version_id 已存在则跳过）"""
        try:
            with get_db_session() as session:
                existing = session.query(StrategyVersion).filter(
                    StrategyVersion.version_id == version_id
                ).first()
                if existing:
                    return existing.id
                row = StrategyVersion(
                    version_id=version_id,
                    strategy_type=strategy_type,
                    name=name,
                    config=_make_json_safe(config),
                    priority=priority,
                    trigger_reason=trigger_reason,
                    status=status,
                    created_by=created_by,
                    meta=_make_json_safe(meta) if meta else None,
                    is_active=1 if status == "active" else 0,
                )
                session.add(row)
                session.commit()
                return row.id
        except Exception as e:
            logger.error(f"保存策略版本失败: {e}")
            return None

    def activate(self, version_id: str, strategy_type: str) -> bool:
        """把 version_id 标记为活跃（同 type 下其他版本归 archived）"""
        try:
            with get_db_session() as session:
                target = session.query(StrategyVersion).filter(
                    StrategyVersion.version_id == version_id
                ).first()
                if not target:
                    logger.warning(f"激活策略失败：未找到 version_id={version_id}")
                    return False
                # 同 type 下的其他版本全部归档
                session.query(StrategyVersion).filter(
                    StrategyVersion.strategy_type == strategy_type,
                    StrategyVersion.version_id != version_id,
                ).update({StrategyVersion.is_active: 0, StrategyVersion.status: "archived"})
                target.is_active = 1
                target.status = "active"
                session.commit()
                return True
        except Exception as e:
            logger.error(f"激活策略失败: {e}")
            return False

    def rollback(self, strategy_type: str) -> Optional[str]:
        """把 strategy_type 回滚到默认版本（version_id 含 'default_' 前缀）"""
        try:
            with get_db_session() as session:
                default = session.query(StrategyVersion).filter(
                    StrategyVersion.strategy_type == strategy_type,
                    StrategyVersion.version_id.like("default_%"),
                ).first()
                if not default:
                    logger.warning(f"回滚失败：未找到默认策略 {strategy_type}")
                    return None
                # 把同 type 其他版本归档
                session.query(StrategyVersion).filter(
                    StrategyVersion.strategy_type == strategy_type,
                    StrategyVersion.version_id != default.version_id,
                ).update({StrategyVersion.is_active: 0, StrategyVersion.status: "rolled_back"})
                default.is_active = 1
                default.status = "active"
                session.commit()
                return default.version_id
        except Exception as e:
            logger.error(f"回滚策略失败: {e}")
            return None

    def load_all_active(self) -> Dict[str, Dict[str, Any]]:
        """启动时回读所有活跃策略，{ strategy_type: {...} }"""
        try:
            with get_db_session() as session:
                rows = session.query(StrategyVersion).filter(
                    StrategyVersion.is_active == 1
                ).all()
                return {
                    r.strategy_type: self._to_dict(r)
                    for r in rows
                }
        except Exception as e:
            logger.error(f"加载活跃策略失败: {e}")
            return {}

    def get_versions_by_type(self, strategy_type: str) -> List[Dict[str, Any]]:
        """获取某类型下的所有版本（按时间倒序）"""
        try:
            with get_db_session() as session:
                rows = session.query(StrategyVersion).filter(
                    StrategyVersion.strategy_type == strategy_type
                ).order_by(desc(StrategyVersion.created_at)).all()
                return [self._to_dict(r) for r in rows]
        except Exception as e:
            logger.error(f"获取策略版本列表失败: {e}")
            return []

    def _to_dict(self, row: StrategyVersion) -> Dict[str, Any]:
        return {
            "id": row.id,
            "version_id": row.version_id,
            "strategy_type": row.strategy_type,
            "name": row.name,
            "config": row.config,
            "priority": row.priority,
            "trigger_reason": row.trigger_reason,
            "status": row.status,
            "created_by": row.created_by,
            "meta": row.meta,
            "is_active": row.is_active,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
