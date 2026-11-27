import json
import os
import sqlite3
from datetime import UTC, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .schemas import Parameters

BEIJING = timezone(timedelta(hours=8))


def next_daily_run(clock: str, current: datetime) -> str:
    local = current.astimezone(BEIJING)
    due = datetime.combine(local.date(), time.fromisoformat(clock), BEIJING)
    if due <= local:
        due += timedelta(days=1)
    return due.astimezone(UTC).isoformat()


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(default="", max_length=80)
    time: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    params: Parameters


class ScheduleState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class QueryStore:
    def __init__(self) -> None:
        path = os.getenv("TISO_RESULTS_FILE")
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path or ":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA secure_delete = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS history (
                id TEXT PRIMARY KEY, finished_at TEXT NOT NULL, summary TEXT NOT NULL, result TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS history_date ON history(finished_at DESC, id DESC);
            CREATE TABLE IF NOT EXISTS schedules (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, clock TEXT NOT NULL, params TEXT NOT NULL,
                enabled INTEGER NOT NULL, next_run TEXT, last_run TEXT, last_status TEXT, last_error TEXT
            );
        """)
        with self.db:
            self.db.execute("UPDATE schedules SET last_status='interrupted' WHERE last_status='running'")

    def close(self) -> None:
        self.db.close()

    def save_result(
        self, job_id: str, params: Parameters, result: dict, status: str,
        schedule_id: str | None, schedule_name: str | None,
    ) -> None:
        summary = {
            "id": job_id, "params": params.model_dump(mode="json"), "status": status,
            "finished_at": result["finished_at"], "schedule_id": schedule_id, "schedule_name": schedule_name,
            "request_count": result["request_count"],
        }
        # Persist the derived map and metadata; keep individual routing observations in the job only.
        saved = {**result, "samples": []}
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO history VALUES (?, ?, ?, ?)",
                            (job_id, summary["finished_at"], json.dumps(summary), json.dumps(saved)))

    def history(self, limit: int, offset: int) -> list[dict]:
        rows = self.db.execute("SELECT summary FROM history ORDER BY finished_at DESC, id DESC LIMIT ? OFFSET ?",
                               (limit, offset))
        return [json.loads(row["summary"]) for row in rows]

    def result(self, job_id: str) -> dict | None:
        row = self.db.execute("SELECT summary, result FROM history WHERE id=?", (job_id,)).fetchone()
        return {"summary": json.loads(row["summary"]), "result": json.loads(row["result"])} if row else None

    def delete_result(self, job_id: str) -> bool:
        with self.db:
            return bool(self.db.execute("DELETE FROM history WHERE id=?", (job_id,)).rowcount)

    @staticmethod
    def schedule_record(row: sqlite3.Row) -> dict:
        record = dict(row)
        record["params"] = json.loads(record["params"])
        record["enabled"] = bool(record["enabled"])
        return record

    def schedules(self) -> list[dict]:
        return [self.schedule_record(row) for row in self.db.execute(
            "SELECT * FROM schedules ORDER BY enabled DESC, clock, id")]

    def create_schedule(self, settings: ScheduleInput, current: datetime) -> dict:
        schedule_id = uuid4().hex
        name = settings.name or "驾车等时圈"
        with self.db:
            self.db.execute("INSERT INTO schedules (id,name,clock,params,enabled,next_run) VALUES (?,?,?,?,1,?)",
                            (schedule_id, name, settings.time, settings.params.model_dump_json(),
                             next_daily_run(settings.time, current)))
        return self.schedule_record(self.db.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone())

    def set_schedule(self, schedule_id: str, enabled: bool, current: datetime) -> dict | None:
        row = self.db.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
        if not row:
            return None
        if bool(row["enabled"]) != enabled:
            with self.db:
                self.db.execute("UPDATE schedules SET enabled=?, next_run=? WHERE id=?",
                                (enabled, next_daily_run(row["clock"], current) if enabled else None, schedule_id))
            row = self.db.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
        return self.schedule_record(row)

    def delete_schedule(self, schedule_id: str) -> bool:
        with self.db:
            return bool(self.db.execute("DELETE FROM schedules WHERE id=?", (schedule_id,)).rowcount)

    def due(self, current: datetime) -> dict | None:
        row = self.db.execute("SELECT * FROM schedules WHERE enabled=1 AND next_run<=? ORDER BY next_run, id LIMIT 1",
                              (current.astimezone(UTC).isoformat(),)).fetchone()
        return self.schedule_record(row) if row else None

    def dispatch(self, schedule: dict, current: datetime) -> bool:
        with self.db:
            updated = self.db.execute("""
                UPDATE schedules SET next_run=?,last_run=?,last_status='running',last_error=NULL
                WHERE id=? AND enabled=1 AND next_run=?
            """, (next_daily_run(schedule["clock"], current), current.astimezone(UTC).isoformat(),
                  schedule["id"], schedule["next_run"]))
            return bool(updated.rowcount)

    def finish_schedule(self, schedule_id: str, status: str, error: str | None) -> None:
        with self.db:
            self.db.execute("UPDATE schedules SET last_status=?,last_error=? WHERE id=?", (status, error, schedule_id))
