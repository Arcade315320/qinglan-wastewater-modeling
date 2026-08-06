import unittest
import time
from datetime import datetime
from threading import Lock
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import (
    ModelType,
    ProcessType,
    ProjectCreate,
    ProjectRecord,
    SimulationJobRecord,
    SimulationJobStatus,
    SimulationRequest,
    SimulationResult,
    ValidationRecord,
)
from app.api.routes import _apply_server_validation
from app.services.simulation_job_service import (
    cancel_simulation_job,
    recover_simulation_jobs,
    retry_simulation_job,
)
from app.services.project_service import SQLiteProjectStore
from app.services.report_service import _compliance_label
from backend.tests.test_models import bsm1_payload


def fake_result(project_id: str) -> SimulationResult:
    return SimulationResult.model_validate(
        {
            "project_id": project_id,
            "model_id": "ASM1",
            "engine": "QSDsan接口测试",
            "effluent": {
                "cod_mg_l": 45,
                "nh4_n_mg_l": 2,
                "tn_mg_l": 14,
                "tp_mg_l": 0.4,
                "tss_mg_l": 8,
            },
            "limits": {
                "cod_mg_l": 50,
                "nh4_n_mg_l": 5,
                "tn_mg_l": 15,
                "tp_mg_l": 0.5,
                "tss_mg_l": 10,
                "basis": "日均值",
                "source": "接口测试限值",
            },
            "reliability": {
                "level": "工程复核",
                "score": 100,
                "decision": "接口测试",
                "checks": {"接口": True},
                "blockers": [],
            },
            "removal_rates": {
                "cod": 0.85,
                "nh4_n": 0.94,
                "tn": 0.7,
                "tp": 0.9,
                "tss": 0.96,
            },
            "energy_kwh_d": 360,
            "sludge_kg_d": 800,
            "compliance": {
                "cod": True,
                "nh4_n": True,
                "tn": True,
                "tp": False,
                "tss": True,
            },
            "applicable_indicators": {
                "cod": True,
                "nh4_n": True,
                "tn": True,
                "tp": False,
                "tss": True,
            },
            "model_note": "接口测试",
            "component_mapping": {
                "method": "测试映射",
                "concentrations_mg_l": {"S_I": 30},
                "reconstructed": {"cod_mg_l": 300},
                "relative_residuals": {"cod_mg_l": 0},
            },
            "mass_balance": {
                "passed": True,
                "hydraulic_relative_error": 0,
                "cod_recovery": 0.6,
                "nitrogen_recovery": 0.5,
                "notes": [],
            },
            "convergence_reached": True,
            "simulation_days": 50,
        }
    )


class ApiWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.store = SQLiteProjectStore(
            Path(self.temporary_directory.name) / "test.sqlite3"
        )
        self.store_patch = patch("app.api.routes.project_store", self.store)
        self.job_store_patch = patch(
            "app.services.simulation_job_service.project_store", self.store
        )
        self.store_patch.start()
        self.job_store_patch.start()

    def tearDown(self) -> None:
        self.job_store_patch.stop()
        self.store_patch.stop()
        self.temporary_directory.cleanup()

    def test_report_compliance_label_requires_formal_decision_gate(self) -> None:
        pending = fake_result("pending-report").model_copy(
            update={"compliance_valid": False}
        )
        self.assertEqual(
            _compliance_label(pending, True),
            "待判定（模型证据不足）",
        )
        self.assertEqual(
            _compliance_label(pending, False),
            "待判定（模型证据不足）",
        )

        valid = pending.model_copy(update={"compliance_valid": True})
        self.assertEqual(_compliance_label(valid, True), "达标")
        self.assertEqual(_compliance_label(valid, False), "超标")

    def test_generated_record_timestamps_are_utc_aware(self) -> None:
        project = ProjectRecord(
            name="时区测试",
            plant_name="时区测试厂",
            process_type=ProcessType.ao,
        )
        result = fake_result(project.id)
        job = SimulationJobRecord(project_id=project.id)

        for timestamp in (
            project.created_at,
            result.created_at,
            result.manifest.generated_at,
            job.created_at,
        ):
            with self.subTest(timestamp=timestamp):
                self.assertIsNotNone(timestamp.tzinfo)
                self.assertEqual(timestamp.utcoffset().total_seconds(), 0)

    def test_independent_validation_requires_server_record(self) -> None:
        project = self.store.create_project(
            ProjectCreate(
                name="验证凭证测试",
                plant_name="验证凭证测试厂",
                process_type=ProcessType.ao,
            )
        )
        payload = bsm1_payload().model_copy(update={"project_id": project.id})
        manual = payload.parameters.model_copy(
            update={
                "independent_validation_passed": True,
                "independent_validation_sample_count": 2,
                "independent_validation_nrmse": 0.1,
            }
        )
        with self.assertRaisesRegex(Exception, "服务器生成的验证凭证"):
            _apply_server_validation(
                payload.model_copy(update={"parameters": manual})
            )

        record = self.store.save_validation_record(
            ValidationRecord(
                project_id=project.id,
                process_type=ProcessType.ao,
                model_type=ModelType.asm1,
                validation_sample_count=3,
                validation_objective=0.12,
                validation_indicator_nrmse={"cod_mg_l": 0.1},
                dataset_hash="a" * 64,
                training_period_start=datetime(2026, 1, 1),
                training_period_end=datetime(2026, 1, 3),
                validation_period_start=datetime(2026, 1, 4),
                validation_period_end=datetime(2026, 1, 5),
                validation_sample_hashes=["b" * 64, "c" * 64],
                engineering_qualified=True,
            )
        )
        referenced = manual.model_copy(
            update={
                "independent_validation_passed": False,
                "independent_validation_sample_count": 0,
                "independent_validation_nrmse": None,
                "validation_record_id": record.id,
            }
        )
        verified = _apply_server_validation(
            payload.model_copy(update={"parameters": referenced})
        )
        self.assertTrue(verified.parameters.independent_validation_passed)
        self.assertEqual(verified.parameters.independent_validation_sample_count, 3)
        self.assertEqual(verified.parameters.independent_validation_nrmse, 0.12)

    def test_project_and_simulation_survive_store_recreation(self) -> None:
        project = self.store.create_project(
            ProjectCreate(
                name="持久化测试",
                plant_name="持久化测试污水厂",
                process_type="AAO",
            )
        )
        result = self.store.add_simulation(fake_result(project.id))

        reopened = SQLiteProjectStore(self.store.database_path)

        self.assertEqual(reopened.get_project(project.id), project)
        self.assertEqual(reopened.get_simulation(project.id), result)

    def test_project_update_and_history_endpoints(self) -> None:
        client = TestClient(app)
        created = client.post(
            "/api/projects",
            json={
                "name": "历史项目",
                "plant_name": "历史污水厂",
                "process_type": "AO",
                "project_code": "HISTORY-001",
                "design_flow_m3_d": 5000,
            },
        ).json()
        updated = client.patch(
            f"/api/projects/{created['id']}",
            json={"name": "历史项目修订", "design_flow_m3_d": 6000},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["revision"], 2)
        self.assertEqual(updated.json()["project_code"], "HISTORY-001")

        measurement = client.post(
            "/api/measurements",
            json={
                "project_id": created["id"],
                "water_quality": self._job_request(created["id"]).influent.model_dump(),
            },
        )
        self.assertEqual(measurement.status_code, 200)
        self.store.add_simulation(fake_result(created["id"]))

        measurements = client.get(
            f"/api/projects/{created['id']}/measurements"
        ).json()
        simulations = client.get(
            f"/api/projects/{created['id']}/simulations"
        ).json()
        self.assertEqual(len(measurements), 1)
        self.assertEqual(len(simulations), 1)

    def test_simulation_job_survives_store_recreation(self) -> None:
        job = SimulationJobRecord(project_id="persistent-job")
        self.store.save_simulation_job(job)

        reopened = SQLiteProjectStore(self.store.database_path)

        self.assertEqual(reopened.get_simulation_job(job.id), job)

    @staticmethod
    def _job_request(project_id: str = "recoverable-project") -> SimulationRequest:
        return SimulationRequest.model_validate(
            {
                "project_id": project_id,
                "influent": {
                    "flow_m3_d": 5000,
                    "cod_mg_l": 300,
                    "nh4_n_mg_l": 35,
                    "tn_mg_l": 48,
                    "tp_mg_l": 5,
                    "tss_mg_l": 200,
                    "ph": 7.2,
                    "temperature_c": 20,
                },
            }
        )

    def test_interrupted_job_is_requeued_with_persisted_request(self) -> None:
        job = SimulationJobRecord(
            project_id="recoverable-project",
            status=SimulationJobStatus.running,
            request_payload=self._job_request(),
            attempt_count=1,
        )
        self.store.save_simulation_job(job)
        with patch("app.services.simulation_job_service._submit") as submit:
            self.assertEqual(recover_simulation_jobs(), 1)
        recovered = self.store.get_simulation_job(job.id)
        self.assertEqual(recovered.status, SimulationJobStatus.queued)
        self.assertEqual(recovered.request_payload.project_id, "recoverable-project")
        submit.assert_called_once_with(job.id)

    def test_queued_job_can_be_cancelled_and_failed_job_retried(self) -> None:
        queued = SimulationJobRecord(
            project_id="recoverable-project",
            request_payload=self._job_request(),
        )
        self.store.save_simulation_job(queued)
        cancelled = cancel_simulation_job(queued.id)
        self.assertEqual(cancelled.status, SimulationJobStatus.cancelled)

        failed = cancelled.model_copy(
            update={
                "status": SimulationJobStatus.failed,
                "cancellation_requested": False,
                "attempt_count": 1,
            }
        )
        self.store.save_simulation_job(failed)
        with patch("app.services.simulation_job_service._submit") as submit:
            retried = retry_simulation_job(failed.id)
        self.assertEqual(retried.status, SimulationJobStatus.queued)
        submit.assert_called_once_with(failed.id)

    def test_background_simulation_job(self) -> None:
        client = TestClient(app)
        project = client.post(
            "/api/projects",
            json={
                "name": "后台任务测试",
                "plant_name": "后台任务污水厂",
                "process_type": "AAO",
            },
        ).json()
        with patch(
            "app.services.simulation_job_service.run_simulation_dispatch",
            return_value=fake_result(project["id"]),
        ):
            submitted = client.post(
                "/api/simulate/jobs",
                json={
                    "project_id": project["id"],
                    "influent": {
                        "flow_m3_d": 5000,
                        "cod_mg_l": 300,
                        "nh4_n_mg_l": 35,
                        "tn_mg_l": 48,
                        "tp_mg_l": 5,
                        "tss_mg_l": 200,
                        "ph": 7.2,
                        "temperature_c": 20,
                    },
                },
            )
            self.assertEqual(submitted.status_code, 200)
            job_id = submitted.json()["id"]
            completed = None
            for _ in range(20):
                completed = client.get(f"/api/simulate/jobs/{job_id}")
                if completed.json()["status"] == "completed":
                    break
                time.sleep(0.01)
        self.assertIsNotNone(completed)
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "completed")
        self.assertEqual(completed.json()["result"]["effluent"]["tn_mg_l"], 14)
        self.assertFalse(completed.json()["result"]["applicable_indicators"]["tp"])

    def test_simulation_job_idempotency_key_returns_same_job(self) -> None:
        client = TestClient(app)
        project = client.post(
            "/api/projects",
            json={
                "name": "幂等任务测试",
                "plant_name": "幂等任务污水厂",
                "process_type": "AO",
            },
        ).json()
        request = self._job_request(project["id"]).model_dump(mode="json")
        headers = {"Idempotency-Key": "same-browser-submit"}
        with patch(
            "app.services.simulation_job_service.run_simulation_dispatch",
            return_value=fake_result(project["id"]),
        ):
            first = client.post("/api/simulate/jobs", json=request, headers=headers)
            second = client.post("/api/simulate/jobs", json=request, headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["id"], second.json()["id"])

    def test_remote_simulation_jobs_run_in_parallel(self) -> None:
        client = TestClient(app)
        project = client.post(
            "/api/projects",
            json={
                "name": "并行任务测试",
                "plant_name": "并行任务污水厂",
                "process_type": "AO",
            },
        ).json()
        state = {"active": 0, "maximum": 0}
        state_lock = Lock()

        def slow_result(payload):
            with state_lock:
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            time.sleep(0.08)
            with state_lock:
                state["active"] -= 1
            return fake_result(payload.project_id)

        request = {
            "project_id": project["id"],
            "influent": {
                "flow_m3_d": 5000,
                "cod_mg_l": 300,
                "nh4_n_mg_l": 35,
                "tn_mg_l": 48,
                "tp_mg_l": 5,
                "tss_mg_l": 200,
                "ph": 7.2,
                "temperature_c": 20,
            },
        }
        with patch(
            "app.services.simulation_job_service.run_simulation_dispatch",
            side_effect=slow_result,
        ):
            job_ids = [
                client.post("/api/simulate/jobs", json=request).json()["id"]
                for _ in range(3)
            ]
            for _ in range(100):
                statuses = [
                    client.get(f"/api/simulate/jobs/{job_id}").json()["status"]
                    for job_id in job_ids
                ]
                if statuses == ["completed"] * 3:
                    break
                time.sleep(0.01)
        self.assertGreaterEqual(state["maximum"], 2)

    def test_project_simulation_and_report_download(self) -> None:
        client = TestClient(app)
        project_response = client.post(
            "/api/projects",
            json={
                "name": "接口测试项目",
                "plant_name": "接口测试污水厂",
                "process_type": "AO",
                "owner": "测试人员",
            },
        )
        self.assertEqual(project_response.status_code, 200)
        project_id = project_response.json()["id"]
        with patch(
            "app.api.routes.run_simulation_dispatch",
            return_value=fake_result(project_id),
        ):
            simulation_response = client.post(
                "/api/simulate",
                json={
                    "project_id": project_id,
                    "influent": {
                        "flow_m3_d": 5000,
                        "cod_mg_l": 300,
                        "nh4_n_mg_l": 35,
                        "tn_mg_l": 48,
                        "tp_mg_l": 5,
                        "tss_mg_l": 200,
                        "ph": 7.2,
                        "temperature_c": 20,
                    },
                },
            )
        self.assertEqual(simulation_response.status_code, 200)
        simulation_id = simulation_response.json()["simulation_id"]
        for report_format in ("pdf", "excel"):
            response = client.post(
                "/api/reports",
                json={
                    "project_id": project_id,
                    "simulation_id": simulation_id,
                    "report_format": report_format,
                    "report_name": "接口测试报告",
                },
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["status"], "ready")
            stored = self.store.get_report_file(body["filename"])
            self.assertIsNotNone(stored)
            local_path = (
                Path(__file__).resolve().parents[1]
                / "generated_reports"
                / body["filename"]
            )
            local_path.unlink(missing_ok=True)
            download = client.get(body["download_url"])
            self.assertEqual(download.status_code, 200)
            self.assertGreater(len(download.content), 1000)
            path = Path(__file__).resolve().parents[1] / "generated_reports" / body["filename"]
            path.unlink(missing_ok=True)

    def test_remote_simulation_gateway(self) -> None:
        from app.models.schemas import SimulationRequest
        from app.services.simulation_gateway import run_simulation_dispatch

        payload = SimulationRequest.model_validate(
            {
                "project_id": "remote-test",
                "influent": {
                    "flow_m3_d": 5000,
                    "cod_mg_l": 300,
                    "nh4_n_mg_l": 35,
                    "tn_mg_l": 48,
                    "tp_mg_l": 5,
                    "tss_mg_l": 200,
                    "ph": 7.2,
                    "temperature_c": 20,
                },
            }
        )
        response = Mock()
        response.json.return_value = fake_result(payload.project_id).model_dump(
            mode="json"
        )
        response.raise_for_status.return_value = None
        with (
            patch(
                "app.services.simulation_gateway.settings.modal_simulation_url",
                "https://example.modal.run",
            ),
            patch(
                "app.services.simulation_gateway.settings.modal_auth_token",
                "test-token",
            ),
            patch(
                "app.services.simulation_gateway.httpx.post",
                return_value=response,
            ) as post,
        ):
            result = run_simulation_dispatch(payload)
        self.assertEqual(result.project_id, payload.project_id)
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer test-token",
        )


if __name__ == "__main__":
    unittest.main()
