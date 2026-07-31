import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app.models.schemas import (
    EffluentPrediction,
    ModelCalibrationRequest,
    ModelType,
    ProcessType,
    SimulationRequest,
)
from app.services.calibration_service import calibrate_model
from app.services.model_catalog import list_models
from app.services.qsdsan_adapter import (
    _apply_advanced_treatment,
    _bulk_components,
    _ph_activity,
    _require_dynamic_memory,
    get_engine_status,
)
from app.services.simulation_service import run_simulation
from app.services.simulation_service import _resolve_limits, _validate_model


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

    def test_asm2d_total_phosphorus_is_not_double_counted(self) -> None:
        payload = SimulationRequest.model_validate(
            {
                "project_id": "asm2d-mapping",
                "influent": {
                    "flow_m3_d": 7465,
                    "cod_mg_l": 427.1,
                    "bod_mg_l": 106.9,
                    "nh4_n_mg_l": 29.8,
                    "tn_mg_l": 43.5,
                    "tp_mg_l": 4.03,
                    "tss_mg_l": 204.8,
                    "ph": 6.95,
                    "temperature_c": 29.5,
                },
                "parameters": {
                    "process_type": "AAO",
                    "model_type": "ASM2d",
                },
            }
        )
        components, _ = _bulk_components(payload)
        reconstructed_p = (
            components["S_PO4"]
            + components["S_F"] * 0.01
            + components["X_I"] * 0.01
            + components["X_S"] * 0.01
            + components["X_H"] * 0.02
        )
        self.assertAlmostEqual(reconstructed_p, payload.influent.tp_mg_l)

    def test_bulk_fractionation_is_constrained_by_tss_and_nitrogen(self) -> None:
        payload = SimulationRequest.model_validate(
            {
                "project_id": "bulk-fit",
                "influent": {
                    "flow_m3_d": 5000,
                    "cod_mg_l": 260,
                    "bod_mg_l": 120,
                    "nh4_n_mg_l": 32,
                    "tn_mg_l": 48,
                    "tp_mg_l": 4.2,
                    "tss_mg_l": 180,
                    "ph": 7.1,
                    "temperature_c": 22,
                },
                "parameters": {"model_type": "ASM2d"},
            }
        )
        components, method = _bulk_components(payload)
        reconstructed_tss = (
            components["X_I"] * 0.75
            + components["X_S"] * 0.75
            + components["X_H"] * 0.9
        )
        reconstructed_tn = (
            components["S_NH4"]
            + components["S_I"] * 0.01
            + components["S_F"] * 0.03
            + components["X_I"] * 0.02
            + components["X_S"] * 0.04
            + components["X_H"] * 0.07
        )
        self.assertAlmostEqual(reconstructed_tss, payload.influent.tss_mg_l)
        self.assertLess(
            abs(reconstructed_tn - payload.influent.tn_mg_l)
            / payload.influent.tn_mg_l,
            0.15,
        )
        self.assertIn("约束", method)

    def test_suspiciously_low_influent_tss_is_rejected(self) -> None:
        payload = SimulationRequest.model_validate(
            {
                "project_id": "bad-tss",
                "influent": {
                    "flow_m3_d": 5000,
                    "cod_mg_l": 260,
                    "bod_mg_l": 120,
                    "nh4_n_mg_l": 32,
                    "tn_mg_l": 48,
                    "tp_mg_l": 4.2,
                    "tss_mg_l": 12,
                    "ph": 7.1,
                    "temperature_c": 22,
                },
                "parameters": {"model_type": "ASM2d"},
            }
        )
        with self.assertRaisesRegex(ValueError, "比值异常低"):
            _bulk_components(payload)

class AdvancedTreatmentTests(unittest.TestCase):
    def test_advanced_treatment_meets_target_case_limits(self) -> None:
        payload = SimulationRequest.model_validate(
            {
                "project_id": "advanced-treatment",
                "influent": {
                    "flow_m3_d": 50000,
                    "cod_mg_l": 260,
                    "bod_mg_l": 130,
                    "nh4_n_mg_l": 32,
                    "tn_mg_l": 48,
                    "tp_mg_l": 4.2,
                    "tss_mg_l": 180,
                    "ph": 7.2,
                    "temperature_c": 20,
                },
                "parameters": {
                    "model_type": "ASM2d",
                    "external_carbon_dose_mg_l": 8,
                    "ferric_chloride_dose_mg_l": 18,
                    "tertiary_filter_solids_capture": 0.85,
                },
            }
        )
        prediction = EffluentPrediction(
            cod_mg_l=20.938,
            nh4_n_mg_l=0.11,
            tn_mg_l=15.988,
            tp_mg_l=2.983,
            tss_mg_l=11.875,
        )
        treated, energy, sludge, assumptions, warnings = _apply_advanced_treatment(
            prediction, payload
        )
        self.assertLessEqual(treated.cod_mg_l, 50)
        self.assertLessEqual(treated.nh4_n_mg_l, 5)
        self.assertLessEqual(treated.tn_mg_l, 15)
        self.assertLessEqual(treated.tp_mg_l, 0.5)
        self.assertLessEqual(treated.tss_mg_l, 10)
        self.assertGreater(energy, 0)
        self.assertGreater(sludge, 0)
        self.assertTrue(assumptions)
        self.assertTrue(warnings)

    def test_result_exposes_biological_and_final_effluent(self) -> None:
        payload = bsm1_payload()
        payload.parameters.external_carbon_dose_mg_l = 8
        with patch(
            "app.services.simulation_service.run_dynamic_system",
            return_value=(
                EffluentPrediction(
                    cod_mg_l=40, nh4_n_mg_l=2, tn_mg_l=12,
                    tp_mg_l=0.4, tss_mg_l=8,
                ),
                EffluentPrediction(
                    cod_mg_l=45, nh4_n_mg_l=2, tn_mg_l=14,
                    tp_mg_l=0.8, tss_mg_l=12,
                ),
                self._mapping(),
                self._balance(),
                100,
                50,
                False,
                [],
                [],
            ),
        ):
            result = run_simulation(payload)
        self.assertTrue(result.advanced_treatment_applied)
        self.assertEqual(result.biological_effluent.tn_mg_l, 14)
        self.assertEqual(result.effluent.tn_mg_l, 12)

    @staticmethod
    def _mapping():
        from app.models.schemas import ComponentMappingResult
        return ComponentMappingResult(
            method="测试", concentrations_mg_l={},
            reconstructed={}, relative_residuals={},
        )

    @staticmethod
    def _balance():
        from app.models.schemas import MassBalanceResult
        return MassBalanceResult(
            passed=True, hydraulic_relative_error=0,
            cod_recovery=0.5, nitrogen_recovery=0.5,
        )

    def test_unsupported_process_topology_is_rejected(self) -> None:
        payload = bsm1_payload()
        payload.parameters.process_type = ProcessType.mbr
        with self.assertRaisesRegex(ValueError, "尚未建立MBR专用"):
            _validate_model(payload)

    def test_standard_limits_follow_temperature_and_transition_date(self) -> None:
        payload = bsm1_payload()
        payload.influent.temperature_c = 10
        payload.parameters.commissioned_before_2006 = True
        payload.parameters.assessment_date = datetime(2026, 7, 31).date()
        limits = _resolve_limits(payload)
        self.assertEqual(limits.nh4_n_mg_l, 8)
        self.assertEqual(limits.tp_mg_l, 1)
        payload.parameters.assessment_date = datetime(2028, 1, 1).date()
        self.assertEqual(_resolve_limits(payload).tp_mg_l, 0.5)

class MemoryRequirementTests(unittest.TestCase):
    def test_low_memory_instance_is_rejected_before_model_import(self) -> None:
        with patch(
            "app.services.qsdsan_adapter._memory_limit_bytes",
            return_value=512 * 1024**2,
        ):
            status = get_engine_status()
            self.assertFalse(status.available)
            self.assertIn("内存不足", status.detail)
            with self.assertRaisesRegex(ValueError, "至少需要1 GB"):
                _require_dynamic_memory()


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
            "app.services.calibration_service._run_activated_sludge_screening",
            side_effect=self._fake_simulation,
        ):
            result = calibrate_model(request)
        self.assertEqual(result.training_sample_count, 8)
        self.assertEqual(result.validation_sample_count, 2)
        self.assertIsNotNone(result.validation_objective)
        self.assertGreater(result.improvement_percent, 50)

    def test_worse_calibration_candidate_is_rejected(self) -> None:
        def worsening_simulation(request):
            factor = request.parameters.cod_kinetic_factor
            source = request.influent
            return SimpleNamespace(
                effluent=EffluentPrediction(
                    cod_mg_l=source.cod_mg_l * factor,
                    nh4_n_mg_l=source.nh4_n_mg_l,
                    tn_mg_l=source.tn_mg_l,
                    tp_mg_l=source.tp_mg_l,
                    tss_mg_l=source.tss_mg_l,
                )
            )

        samples = []
        for index in range(2):
            samples.append(
                {
                    "group_id": "甲厂",
                    "sample_time": datetime(2026, 1, index + 1),
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
                    "measured": {"cod_mg_l": 150},
                }
            )
        request = ModelCalibrationRequest(
            project_id="rejection-test",
            samples=samples,
            validation_fraction=0,
        )
        with patch(
            "app.services.calibration_service._run_activated_sludge_screening",
            side_effect=worsening_simulation,
        ):
            result = calibrate_model(request)
        self.assertEqual(result.factors.cod, 1)
        self.assertEqual(result.improvement_percent, 0)
        self.assertTrue(any("自动拒绝" in item for item in result.warnings))


if __name__ == "__main__":
    unittest.main()
