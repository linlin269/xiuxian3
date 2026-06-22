"""宗门战定时任务。"""

from __future__ import annotations

from launch.schedulers import Scheduler

from .service import service


@Scheduler._sync("cron", day_of_week="sun,mon", hour=0, minute=5, id="sect_war_generate_rewards")
def sect_war_generate_rewards() -> None:
    """周日生成本期奖励；周一兜底生成上一周期奖励。"""

    service.ensure_claimable_rewards()
