from app.models.schemas import MeasurementCreate, MeasurementRecord, ProjectCreate, ProjectRecord


class InMemoryProjectStore:
    def __init__(self) -> None:
        self._projects: dict[str, ProjectRecord] = {}
        self._measurements: dict[str, MeasurementRecord] = {}

    def create_project(self, payload: ProjectCreate) -> ProjectRecord:
        project = ProjectRecord(**payload.model_dump())
        self._projects[project.id] = project
        return project

    def list_projects(self) -> list[ProjectRecord]:
        return list(self._projects.values())

    def exists(self, project_id: str) -> bool:
        return project_id in self._projects

    def add_measurement(self, payload: MeasurementCreate) -> MeasurementRecord:
        measurement = MeasurementRecord(**payload.model_dump())
        self._measurements[measurement.id] = measurement
        return measurement


project_store = InMemoryProjectStore()
