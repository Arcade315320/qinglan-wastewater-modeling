import json
import sqlite3
from pathlib import Path
from threading import RLock

from app.models.schemas import (
    MeasurementCreate,
    MeasurementRecord,
    ProjectCreate,
    ProjectRecord,
    ProjectUpdate,
    SimulationResult,
    SimulationJobRecord,
    SimulationJobStatus,
    ValidationRecord,
)
from app.core.config import settings
from app.core.time import utc_now


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
                CREATE TABLE IF NOT EXISTS validation_records (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS validation_records_project_time
                    ON validation_records(project_id, created_at);
                CREATE TABLE IF NOT EXISTS report_files (
                    filename TEXT PRIMARY KEY,
                    content_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data BLOB NOT NULL
                );
                """
            )

    @staticmethod
    def _serialize(
        record: ProjectRecord | MeasurementRecord | SimulationResult | SimulationJobRecord | ValidationRecord,
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

    def update_project(
        self, project_id: str, payload: ProjectUpdate
    ) -> ProjectRecord | None:
        current = self.get_project(project_id)
        if current is None:
            return None
        changes = payload.model_dump(exclude_unset=True)
        updated = current.model_copy(
            update={
                **changes,
                "updated_at": utc_now(),
                "revision": current.revision + 1,
            }
        )
        return self.save_project(updated)

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

    def list_measurements(self, project_id: str) -> list[MeasurementRecord]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM measurements WHERE project_id = ? ORDER BY sample_time DESC",
                (project_id,),
            ).fetchall()
        return [MeasurementRecord.model_validate(json.loads(row["data"])) for row in rows]

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

    def list_simulations(self, project_id: str) -> list[SimulationResult]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM simulations WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [SimulationResult.model_validate(json.loads(row["data"])) for row in rows]

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

    def list_recoverable_simulation_jobs(self) -> list[SimulationJobRecord]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM simulation_jobs WHERE status IN (?, ?)",
                (SimulationJobStatus.queued.value, SimulationJobStatus.running.value),
            ).fetchall()
        return [SimulationJobRecord.model_validate(json.loads(row["data"])) for row in rows]

    def list_simulation_jobs(self, project_id: str) -> list[SimulationJobRecord]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM simulation_jobs WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [SimulationJobRecord.model_validate(json.loads(row["data"])) for row in rows]

    def get_simulation_job_by_idempotency_key(
        self, idempotency_key: str
    ) -> SimulationJobRecord | None:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM simulation_jobs ORDER BY created_at DESC"
            ).fetchall()
        for row in rows:
            job = SimulationJobRecord.model_validate(json.loads(row["data"]))
            if job.idempotency_key == idempotency_key:
                return job
        return None

    def claim_simulation_job(self, job_id: str) -> SimulationJobRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM simulation_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            job = SimulationJobRecord.model_validate(json.loads(row["data"]))
            if job.status != SimulationJobStatus.queued or job.cancellation_requested:
                return None
            claimed = job.model_copy(
                update={
                    "status": SimulationJobStatus.running,
                    "attempt_count": job.attempt_count + 1,
                    "progress_percent": 5,
                    "started_at": utc_now(),
                    "completed_at": None,
                    "error": None,
                }
            )
            cursor = connection.execute(
                """
                UPDATE simulation_jobs
                SET status = ?, data = ?
                WHERE id = ? AND status = ?
                """,
                (
                    SimulationJobStatus.running.value,
                    self._serialize(claimed),
                    job_id,
                    SimulationJobStatus.queued.value,
                ),
            )
            if getattr(cursor, "rowcount", 1) != 1:
                return None
        return claimed

    def save_validation_record(self, record: ValidationRecord) -> ValidationRecord:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO validation_records(id, project_id, created_at, data)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_id = excluded.project_id,
                    created_at = excluded.created_at,
                    data = excluded.data
                """,
                (
                    record.id,
                    record.project_id,
                    record.created_at.isoformat(),
                    self._serialize(record),
                ),
            )
        return record

    def get_validation_record(
        self, project_id: str, record_id: str
    ) -> ValidationRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT data FROM validation_records
                WHERE project_id = ? AND id = ?
                """,
                (project_id, record_id),
            ).fetchone()
        return ValidationRecord.model_validate(json.loads(row["data"])) if row else None

    def list_validation_records(self, project_id: str) -> list[ValidationRecord]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT data FROM validation_records
                WHERE project_id = ? ORDER BY created_at DESC
                """,
                (project_id,),
            ).fetchall()
        return [
            ValidationRecord.model_validate(json.loads(row["data"]))
            for row in rows
        ]

    def save_report_file(
        self, filename: str, content_type: str, data: bytes
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO report_files(filename, content_type, created_at, data)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(filename) DO UPDATE SET
                    content_type = excluded.content_type,
                    created_at = excluded.created_at,
                    data = excluded.data
                """,
                (filename, content_type, utc_now().isoformat(), data),
            )

    def get_report_file(self, filename: str) -> tuple[str, bytes] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT content_type, data FROM report_files WHERE filename = ?",
                (filename,),
            ).fetchone()
        return (row["content_type"], bytes(row["data"])) if row else None

class _PostgreSQLConnectionAdapter:
    def __init__(self, connection) -> None:
        self.connection = connection

    @staticmethod
    def _sql(statement: str) -> str:
        return statement.replace("?", "%s")

    def execute(self, statement: str, parameters=()):
        if statement.lstrip().upper().startswith("PRAGMA "):
            return self.connection.execute("SELECT 1")
        return self.connection.execute(self._sql(statement), parameters)

    def executescript(self, script: str) -> None:
        postgres_script = script.replace(" BLOB ", " BYTEA ")
        for statement in postgres_script.split(";"):
            if statement.strip():
                self.connection.execute(statement)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()


class PostgreSQLProjectStore(SQLiteProjectStore):
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> _PostgreSQLConnectionAdapter:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as error:
            raise RuntimeError(
                "DATABASE_URL已配置，但未安装PostgreSQL数据库驱动。"
            ) from error
        connection = psycopg.connect(self.database_url, row_factory=dict_row)
        return _PostgreSQLConnectionAdapter(connection)


def create_project_store():
    if settings.database_url:
        return PostgreSQLProjectStore(settings.database_url)
    return SQLiteProjectStore()


project_store = create_project_store()
