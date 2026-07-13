"""生成技师空闲排班数据（供评测使用）。

每次评测前运行一次，确保预约系统有可用档期。
每个技师每天有 4 个 2 小时空闲时段：09-11, 11-13, 14-16, 16-18。
时段 18-21 用于测试边界时间（ab_conflict 需要 21:30）。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, date, timedelta, time as dtime
from db.local_db import get_db_session
from db.models import Technician, TechnicianSchedule


def seed_schedules(days_ahead: int = 7) -> dict:
    """生成所有技师未来 N 天的空闲排班。返回统计 dict。"""
    # 每天的空闲时段 (start_hour, end_hour)
    SLOT_HOURS = [
        (9, 11),
        (11, 13),
        (14, 16),
        (16, 18),
        (18, 20),   # 18:00-20:00 覆盖 ab_conflict 21:30（实际会延到 21:30）
        (20, 22),   # 20:00-22:00 覆盖 ab_conflict
    ]

    created = 0
    deleted = 0
    with get_db_session() as s:
        # 清掉所有现有 schedules（保留其他业务数据）
        deleted = s.query(TechnicianSchedule).delete(synchronize_session=False)

        techs = s.query(Technician).all()
        today = date.today()
        rows = []

        for day_offset in range(days_ahead):
            day = today + timedelta(days=day_offset)
            for tech in techs:
                for start_h, end_h in SLOT_HOURS:
                    start = datetime.combine(day, dtime(start_h, 0))
                    end = datetime.combine(day, dtime(end_h, 0))
                    rows.append(TechnicianSchedule(
                        technician_id=tech.id,
                        start_time=start,
                        end_time=end,
                        status="free",
                        appointment_id=None,
                    ))
        s.add_all(rows)
        s.commit()
        created = len(rows)

    return {"deleted": deleted, "created": created, "techs": len(techs), "days": days_ahead}


if __name__ == "__main__":
    result = seed_schedules(days_ahead=7)
    print(f"排班生成完毕: 删除旧排班 {result['deleted']} 条, "
          f"新建 {result['created']} 条 "
          f"({result['techs']} 技师 × {result['days']} 天 × 6 时段)")
