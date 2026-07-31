import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import SimulationResult


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
                "tp": True,
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
