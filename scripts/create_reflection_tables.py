"""
反思相关数据库表创建脚本

用于创建 TaskEvaluation、ReflectionLog、UserFeedback 表
"""

import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from db.models import Base
from config.settings import get_settings


def create_reflection_tables():
    """创建反思相关的数据库表"""
    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL)

    print("开始创建反思相关的数据库表...")

    # 创建所有表
    Base.metadata.create_all(engine)

    # 验证表是否创建成功
    with engine.connect() as conn:
        # 检查表是否存在
        tables_to_check = ['task_evaluations', 'reflection_logs', 'user_feedbacks']
        
        for table_name in tables_to_check:
            result = conn.execute(text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'"))
            if result.fetchone():
                print(f"✓ 表 '{table_name}' 已存在或创建成功")
            else:
                print(f"✗ 表 '{table_name}' 创建失败")

    print("\n数据库表创建完成！")
    
    # 显示表结构
    show_table_structure()


def show_table_structure():
    """显示表结构"""
    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL)

    print("\n" + "=" * 60)
    print("表结构说明")
    print("=" * 60)

    tables_info = {
        'task_evaluations': {
            'description': '任务评估表 - 记录每次任务执行的评估结果',
            'columns': [
                'id (PK) - 主键',
                'session_id - 会话ID',
                'task_type - 任务类型 (appointment/consultation/classification)',
                'success - 成功标志 (0=失败, 1=部分成功, 2=成功)',
                'success_rate - 成功率评分 (0-1)',
                'completion_time - 完成耗时(秒)',
                'turns_count - 对话轮数',
                'error_type - 错误类型',
                'error_message - 错误信息',
                'action_data - 任务相关数据 (JSON)',
                'reflection_triggered - 是否触发反思',
                'created_at - 创建时间'
            ]
        },
        'reflection_logs': {
            'description': '反思日志表 - 记录反思过程和结论',
            'columns': [
                'id (PK) - 主键',
                'session_id - 会话ID',
                'evaluation_id (FK) - 关联的评估ID',
                'reflection_type - 反思类型 (post_task/periodic/threshold)',
                'findings - 反思发现 (JSON)',
                'recommendations - 改进建议 (JSON)',
                'patterns_discovered - 发现的模式 (JSON)',
                'bad_cases - 坏case记录 (JSON)',
                'improvement_actions - 已采取的改进措施 (JSON)',
                'created_at - 创建时间'
            ]
        },
        'user_feedbacks': {
            'description': '用户反馈表 - 记录用户的显式和隐式反馈',
            'columns': [
                'id (PK) - 主键',
                'session_id - 会话ID',
                'user_id - 用户ID',
                'feedback_type - 反馈类型 (rating/correction/complaint/praise)',
                'rating - 评分 (1-5)',
                'content - 反馈内容',
                'source - 来源 (explicit=显式, implicit=隐式)',
                'action_data - 相关行为数据 (JSON)',
                'created_at - 创建时间'
            ]
        }
    }

    for table_name, info in tables_info.items():
        print(f"\n【{table_name}】")
        print(f"说明: {info['description']}")
        print("字段:")
        for col in info['columns']:
            print(f"  - {col}")


def drop_reflection_tables():
    """删除反思相关的数据库表（用于重建）"""
    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL)

    print("警告: 即将删除反思相关的数据库表！")
    confirm = input("确认删除? (yes/no): ")

    if confirm.lower() != 'yes':
        print("取消删除操作")
        return

    tables_to_drop = ['task_evaluations', 'reflection_logs', 'user_feedbacks']

    with engine.connect() as conn:
        for table_name in tables_to_drop:
            try:
                conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
                conn.commit()
                print(f"✓ 表 '{table_name}' 已删除")
            except Exception as e:
                print(f"✗ 删除表 '{table_name}' 失败: {e}")

    print("\n数据库表删除完成！")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='反思数据库表管理脚本')
    parser.add_argument('action', choices=['create', 'drop', 'show'],
                       help='操作: create(创建), drop(删除), show(显示结构)')
    args = parser.parse_args()

    if args.action == 'create':
        create_reflection_tables()
    elif args.action == 'drop':
        drop_reflection_tables()
    elif args.action == 'show':
        show_table_structure()
