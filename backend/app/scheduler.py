import asyncio
import logging
import sqlite3
from datetime import datetime

from .core.mainland import inside_mainland
from .query_store import QueryStore
from .schemas import Parameters
from .tasks import Jobs, estimate, now

LOG = logging.getLogger(__name__)


def dispatch_due(store: QueryStore, jobs: Jobs, current: datetime) -> None:
    if jobs.active and not jobs.active.done():
        return
    schedule = store.due(current)
    if schedule is None:
        return
    params = Parameters.model_validate(schedule["params"])
    if not store.dispatch(schedule, current):
        return
    if jobs.amap is None:
        store.finish_schedule(schedule["id"], "skipped", "请配置高德 Web 服务 Key")
        return
    if not inside_mainland(params.destination.lng, params.destination.lat):
        store.finish_schedule(schedule["id"], "skipped", "目的地已不在算路范围内")
        return
    budget = estimate(params)
    if not budget["fits_budget"]:
        store.finish_schedule(schedule["id"], "skipped", "超出计算预算")
        return
    jobs.prune()
    jobs.create(params, budget["total_points"], schedule_id=schedule["id"], schedule_name=schedule["name"])


async def run_scheduler(store: QueryStore, jobs: Jobs) -> None:
    while True:
        try:
            dispatch_due(store, jobs, now())
        except (ValueError, sqlite3.Error):
            LOG.error("Could not dispatch a TISO schedule")
        await asyncio.sleep(10)
