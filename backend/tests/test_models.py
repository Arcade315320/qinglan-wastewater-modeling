import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app.models.schemas import (
    EffluentPrediction,
    ModelCalibrationRequest,
    ModelType,
    SimulationRequest,
)
from app.services.calibration_service import calibrate_model
from app.services.model_catalog import list_models
from app.services.qsdsan_adapter import _ph_activity
from app.services.simulation_service import run_simulation


def bsm1_payload() -> SimulationRequest:
    return SimulationRequest.model_validate(
        {
            "project_id": "bsm1-regression",
            "influent": {
                "flow_m3_d": 18446,
                "cod_mg_l": 381.19,
                "nh4_n_mg_l": 31.56,
                "tn_mg_l": 49.1,
                "tp_mg_l": 0,
                "tss_mg_l": 211.2675,
                "ph": 7,
                "temperature_c": 15,
            },
            "parameters": {
                "process_type": "AO",
                "model_type": "ASM1",
                "hrt_h": 7.805,
                "srt_d": 9.041,
                "internal_recycle_ratio": 3,
                "sludge_recycle_ratio": 1,
                "simulation_days": 100,
            },
            "component_concentrations": {
                "S_S": 69.5,
                "X_BH": 28.17,
                "X_S": 202.32,
                "X_I": 51.2,
                "S_NH": 31.56,
                "S_I": 30,
                "S_ND": 6.95,
                "X_ND": 10.59,
                "S_ALK": 84,
            },
        }
    )


class QSDsanRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_simulation(bsm1_payload())

    def test_catalog_contains_four_reference_models(self) -> None:
        self.assertEqual(
            {item.id for item in list_models()},
            {ModelType.asm1, ModelType.asm2d, ModelType.masm2d, ModelType.adm1},
        )

    def test_bsm1_fixed_effluent_regression(self) -> None:
        expected = {
            "cod_mg_l": 47.552,
            "nh4_n_mg_l": 1.735,
            "tn_mg_l": 14.044,
            "tss_mg_l": 12.497,
        }
        actual = self.result.effluent.model_dump()
        for key, value in expected.items():
            with self.subTest(indicator=key):
                self.assertAlmostEqual(actual[key], value, delta=max(0.03, value * 0.005))

    def test_bsm1_mass_balance_and_engine(self) -> None:
        self.assertTrue(self.result.mass_balance.passed)
        self.assertLess(self.result.mass_balance.hydraulic_relative_error, 1e-6)
        self.assertTrue(self.result.convergence_reached)
        self.assertIn("QSDsan/EXPOsan", self.result.engine)

    def test_ph_activity_has_neutral_optimum(self) -> None:
        self.assertEqual(_ph_activity(7.2, 5.5, 7, 8, 9.5), 1)
        self.assertLess(_ph_activity(3.8, 5.5, 7, 8, 9.5), 0.1)


class GroupedCalibrationTests(unittest.TestCase):
    @staticmethod
    def _fake_simulation(request):
        p = request.parameters
        source = request.influent
        return SimpleNamespace(
            effluent=EffluentPrediction(
                cod_mg_l=source.cod_mg_l / p.cod_kinetic_factor,
                nh4_n_mg_l=source.nh4_n_mg_l / p.nitrification_kinetic_factor,
                tn_mg_l=source.tn_mg_l / p.denitrification_kinetic_factor,
                tp_mg_l=source.tp_mg_l / p.phosphorus_kinetic_factor,
                tss_mg_l=source.tss_mg_l * 0.1,
            )
        )

    def test_grouped_calibration_reserves_latest_dates(self) -> None:
        start = datetime(2026, 1, 1)
        samples = []
        for group in ("一厂", "二厂"):
            for index in range(5):
                samples.append(
                    {
                        "group_id": group,
                        "sample_time": start + timedelta(days=index),
                        "influent": {
                            "flow_m3_d": 10000,
                            "cod_mg_l": 300,
                            "nh4_n_mg_l": 30,
                            "tn_mg_l": 45,
                            "tp_mg_l": 5,
                            "tss_mg_l": 200,
                            "ph": 7.2,
                            "temperature_c": 20,
                        },
                        "measured": {
                            "cod_mg_l": 150,
                            "nh4_n_mg_l": 15,
                            "tn_mg_l": 22.5,
                            "tp_mg_l": 2.5,
                        },
                    }
                )
        request = ModelCalibrationRequest(
            project_id="grouped-test",
            samples=samples,
            max_iterations=8,
            validation_fraction=0.2,
        )
        with patch(
            "app.services.calibration_service.run_simulation",
            side_effect=self._fake_simulation,
        ):
            result = calibrate_model(request)
        self.assertEqual(result.training_sample_count, 8)
        self.assertEqual(result.validation_sample_count, 2)
        self.assertIsNotNone(result.validation_objective)
        self.assertGreater(result.improvement_percent, 50)


if __name__ == "__main__":
    unittest.main()
