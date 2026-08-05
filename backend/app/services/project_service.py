import json
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock

from app.models.schemas import (
    MeasurementCreate,
    MeasurementRecord,
    ProjectCreate,
    ProjectRecord,
    SimulationResult,
    SimulationJobRecord,
    SimulationJobStatus,
)
from app.core.config import settings


DEFAULT_DATABASE_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "qinglan.sqlite3"
)


class SQLiteProjectStore:
    def __init__(self, database_path: Path | str | None = None) -> None:
        self.database_path = Path(
            database_path or settings.database_path or DEFAULT_DATABASE_PATH
        )
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS measurements (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    sample_time TEXT NOT NULL,
                    data TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS measurements_project_time
                    ON measurements(project_id, sample_time);
                CREATE TABLE IF NOT EXISTS simulations (
                    simulation_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS simulations_project_time
                    ON simulations(project_id, created_at);
                CREATE TABLE IF NOT EXISTS simulation_jobs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS simulation_jobs_project_time
                    ON simulation_jobs(project_id, created_at);
                """
            )

    @staticmethod
    def _serialize(
        record: ProjectRecord | MeasurementRecord | SimulationResult | SimulationJobRecord,
    ) -> str:
        return json.dumps(record.model_dump(mode="json"), ensure_ascii=False)

    def save_project(self, project: ProjectRecord) -> ProjectRecord:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO projects(id, created_at, data) VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    created_at = excluded.created_at,
                    data = excluded.data
                """,
                (project.id, project.created_at.isoformat(), self._serialize(project)),
            )
        return project

    def create_project(self, payload: ProjectCreate) -> ProjectRecord:
        return self.save_project(ProjectRecord(**payload.model_dump()))

    def list_projects(self) -> list[ProjectRecord]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM projects ORDER BY created_at"
            ).fetchall()
        return [ProjectRecord.model_validate(json.loads(row["data"])) for row in rows]

    def exists(self, project_id: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return row is not None

    def add_measurement(self, payload: MeasurementCreate) -> MeasurementRecord:
        measurement = MeasurementRecord(**payload.model_dump())
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO measurements(id, project_id, sample_time, data) VALUES (?, ?, ?, ?)",
                (
                    measurement.id,
                    measurement.project_id,
                    measurement.sample_time.isoformat(),
                    self._serialize(measurement),
                ),
            )
        return measurement

    def get_project(self, project_id: str) -> ProjectRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return ProjectRecord.model_validate(json.loads(row["data"])) if row else None

    def add_simulation(self, result: SimulationResult) -> SimulationResult:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO simulations(simulation_id, project_id, created_at, data)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(simulation_id) DO UPDATE SET
                    project_id = excluded.project_id,
                    created_at = excluded.created_at,
                    data = excluded.data
                """,
                (
                    result.simulation_id,
                    result.project_id,
                    result.created_at.isoformat(),
                    self._serialize(result),
                ),
            )
        return result

    def get_simulation(
        self, project_id: str, simulation_id: str | None = None
    ) -> SimulationResult | None:
        with self._lock, self._connect() as connection:
            if simulation_id:
                row = connection.execute(
                    "SELECT data FROM simulations WHERE project_id = ? AND simulation_id = ?",
                    (project_id, simulation_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT data FROM simulations
                    WHERE project_id = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (project_id,),
                ).fetchone()
        return SimulationResult.model_validate(json.loads(row["data"])) if row else None

    def save_simulation_job(self, job: SimulationJobRecord) -> SimulationJobRecord:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO simulation_jobs(
                    id, project_id, status, created_at, completed_at, data
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    completed_at = excluded.completed_at,
                    data = excluded.data
                """,
                (
                    job.id,
                    job.project_id,
                    job.status.value,
                    job.created_at.isoformat(),
                    job.completed_at.isoformat() if job.completed_at else None,
                    self._serialize(job),
                ),
            )
        return job

    def get_simulation_job(self, job_id: str) -> SimulationJobRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM simulation_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return SimulationJobRecord.model_validate(json.loads(row["data"])) if row else None

    def fail_interrupted_simulation_jobs(self) -> int:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM simulation_jobs WHERE status IN (?, ?)",
                (SimulationJobStatus.queued.value, SimulationJobStatus.running.value),
            ).fetchall()
            for row in rows:
                job = SimulationJobRecord.model_validate(json.loads(row["data"]))
                self.save_simulation_job(
                    job.model_copy(
                        update={
                            "status": SimulationJobStatus.failed,
                            "error": "服务重启中断了尚未完成的动态仿真，请重新提交。",
                            "completed_at": datetime.utcnow(),
                        }
                    )
                )
        return len(rows)


project_store = SQLiteProjectStore()
project_store.fail_interrupted_simulation_jobs()
