from app.models.schemas import (
    MeasurementCreate,
    MeasurementRecord,
    ProjectCreate,
    ProjectRecord,
    SimulationResult,
)


class InMemoryProjectStore:
    def __init__(self) -> None:
        self._projects: dict[str, ProjectRecord] = {}
        self._measurements: dict[str, MeasurementRecord] = {}
        self._simulations: dict[str, SimulationResult] = {}

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

    def get_project(self, project_id: str) -> ProjectRecord | None:
        return self._projects.get(project_id)

    def add_simulation(self, result: SimulationResult) -> SimulationResult:
        self._simulations[result.simulation_id] = result
        return result

    def get_simulation(
        self, project_id: str, simulation_id: str | None = None
    ) -> SimulationResult | None:
        if simulation_id:
            result = self._simulations.get(simulation_id)
            return result if result and result.project_id == project_id else None
        matches = [
            result
            for result in self._simulations.values()
            if result.project_id == project_id
        ]
        return max(matches, key=lambda item: item.created_at) if matches else None


project_store = InMemoryProjectStore()
