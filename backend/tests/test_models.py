import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app.models.schemas import (
    EffluentPrediction,
    ModelCalibrationRequest,
    ModelType,
    OperatingDataSource,
    ProcessParameters,
    ProcessType,
    SimulationRequest,
)
from app.services.calibration_service import calibrate_model, _qualification_blockers
from app.services.model_catalog import list_models
from app.services.process_capability_service import list_process_capabilities
from app.services.qsdsan_adapter import (
    _apply_advanced_treatment,
    _bulk_components,
    _configure_reactor,
    _element_balance_diagnostics,
    _influent_profile_rows,
    _kinetic_kwargs,
    _mapping_evidence,
    _ph_activity,
    _oxygen_transfer_diagnostics,
    _regulatory_profile_value,
    _require_dynamic_memory,
    _simulation_horizons,
    _temporary_bsm_configuration,
    get_engine_status,
)
from app.services.simulation_service import run_simulation
from app.services.traceability_service import build_simulation_manifest
from app.services.simulation_service import (
    _resolve_limits,
    _run_activated_sludge_screening,
    _validate_model,
)


def bsm1_payload() -> SimulationRequest:
    return SimulationRequest.model_validate(
        {
            "project_id": "bsm1-regression",
            "influent": {
                "flow_m3_d": 18446,
                "cod_mg_l": 381.19,
                "nh4_n_mg_l": 31.56,
                "tn_mg_l": 54.4744,
                "tp_mg_l": 0,
                "tss_mg_l": 211.2675,
                "ph": 7,
                "temperature_c": 20,
            },
            "parameters": {
                "process_type": "AO",
                "model_type": "ASM1",
                "hrt_h": 7.805,
                "srt_d": 9.041,
                "internal_recycle_ratio": 3,
                "sludge_recycle_ratio": 1,
                "simulation_days": 100,
                "aeration_power_kw": 250,
                "aerobic_kla_d": 240,
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

    def test_process_capabilities_match_runnable_dynamic_topologies(self) -> None:
        capabilities = {item.process_type: item for item in list_process_capabilities()}
        runnable = {key for key, value in capabilities.items() if value.runnable}
        self.assertEqual(
            runnable,
            {ProcessType.cas, ProcessType.ao, ProcessType.aao},
        )
        self.assertEqual(capabilities[ProcessType.cas].model_type, ModelType.asm1)
        self.assertEqual(capabilities[ProcessType.aao].model_type, ModelType.asm2d)
        self.assertFalse(capabilities[ProcessType.mbr].runnable)

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
        self.assertTrue(self.result.mass_balance.element_balance_passed)
        self.assertTrue(self.result.mass_balance.load_summary_kg_d)

    def test_element_balance_uses_two_sided_and_inventory_gates(self) -> None:
        accepted = _element_balance_diagnostics(0.45, 0.48, 1.02, 0.005)
        self.assertTrue(accepted["element_balance_passed"])
        low_phosphorus = _element_balance_diagnostics(0.45, 0.48, 0.90, 0.005)
        self.assertFalse(low_phosphorus["element_balance_passed"])
        excess_carbon = _element_balance_diagnostics(1.04, 0.48, None, 0.005)
        self.assertFalse(excess_carbon["element_balance_passed"])
        drifting = _element_balance_diagnostics(0.45, 0.48, None, 0.02)
        self.assertFalse(drifting["element_balance_passed"])
        self.assertLess(self.result.mass_balance.hydraulic_relative_error, 1e-6)
        self.assertTrue(self.result.convergence_reached)
        self.assertIn("QSDsan/EXPOsan", self.result.engine)

    def test_asm1_excludes_phosphorus_from_results(self) -> None:
        self.assertFalse(self.result.applicable_indicators["tp"])
        self.assertFalse(self.result.compliance["tp"])
        self.assertNotIn("tp_mg_l", self.result.component_mapping.reconstructed)
        self.assertIsNone(self.result.mass_balance.phosphorus_recovery)

    def test_ph_activity_has_neutral_optimum(self) -> None:
        self.assertEqual(_ph_activity(7.2, 5.5, 7, 8, 9.5), 1)
        self.assertLess(_ph_activity(3.8, 5.5, 7, 8, 9.5), 0.1)

    def test_asm1_temperature_correction_uses_twenty_degree_reference(self) -> None:
        payload = bsm1_payload()
        at_twenty = _kinetic_kwargs(payload)["mu_A"]
        payload.influent.temperature_c = 10
        at_ten = _kinetic_kwargs(payload)["mu_A"]
        self.assertAlmostEqual(at_twenty, 0.5)
        self.assertAlmostEqual(at_ten / at_twenty, 1.072 ** -10)

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
            + components["S_NO3"]
        )
        self.assertAlmostEqual(reconstructed_tss, payload.influent.tss_mg_l)
        self.assertLess(
            abs(reconstructed_tn - payload.influent.tn_mg_l)
            / payload.influent.tn_mg_l,
            0.15,
        )
        self.assertIn("约束", method)

    def test_auto_convergence_extends_to_configured_limit(self) -> None:
        payload = bsm1_payload()
        payload.parameters.simulation_days = 30
        payload.parameters.max_simulation_days = 180
        self.assertEqual(_simulation_horizons(payload), [30, 60, 90, 120, 150, 180])

    def test_published_data_allows_missing_engineering_evidence(self) -> None:
        data = bsm1_payload().model_dump()
        data["parameters"]["operating_data_source"] = "published"
        data["parameters"]["mixed_liquor_tss_mg_l"] = 3300
        payload = SimulationRequest.model_validate(data)
        self.assertEqual(payload.parameters.operating_data_source, "published")
        self.assertEqual(payload.parameters.mixed_liquor_tss_mg_l, 3300)

    def test_cas_disables_internal_recycle(self) -> None:
        payload = bsm1_payload()
        payload.parameters.process_type = ProcessType.cas
        with _temporary_bsm_configuration(payload) as (_, config):
            self.assertLess(config["Q_intr"] / config["Q"], 1e-8)

    def test_aeration_power_limits_unachievable_kla(self) -> None:
        payload = bsm1_payload()
        payload.parameters.aeration_power_kw = 15
        payload.parameters.aerobic_kla_d = 240
        diagnostics = _oxygen_transfer_diagnostics(
            payload,
            {"aerobic_volume": 4000},
        )
        self.assertLess(diagnostics["effective_kla_d"], 240)
        self.assertFalse(diagnostics["oxygen_transfer_sufficient"])

    def test_site_and_diffuser_corrections_reduce_field_oxygen_capacity(self) -> None:
        payload = bsm1_payload()
        baseline = _oxygen_transfer_diagnostics(payload, {"aerobic_volume": 4000})
        payload.parameters.site_altitude_m = 2000
        payload.parameters.diffuser_fouling_factor = 0.6
        corrected = _oxygen_transfer_diagnostics(payload, {"aerobic_volume": 4000})
        self.assertLess(
            corrected["oxygen_transfer_capacity_kg_d"],
            baseline["oxygen_transfer_capacity_kg_d"],
        )
        self.assertLess(
            corrected["corrected_oxygen_saturation_mg_l"],
            baseline["corrected_oxygen_saturation_mg_l"],
        )

    def test_reactor_ph_overrides_influent_ph_for_kinetics(self) -> None:
        payload = bsm1_payload()
        payload.influent.ph = 5.2
        inhibited = _kinetic_kwargs(payload)["mu_A"]
        payload.parameters.reactor_ph = 7.2
        corrected = _kinetic_kwargs(payload)["mu_A"]
        self.assertGreater(corrected, inhibited)

    def test_measured_mode_requires_engineering_evidence(self) -> None:
        data = bsm1_payload().model_dump()
        data["parameters"]["operating_data_source"] = "measured"
        with self.assertRaisesRegex(ValueError, "同期现场实测模式缺少"):
            SimulationRequest.model_validate(data)

    def test_independent_validation_requires_samples_and_error(self) -> None:
        data = bsm1_payload().model_dump()
        data["parameters"]["independent_validation_passed"] = True
        with self.assertRaisesRegex(ValueError, "至少需要两条"):
            SimulationRequest.model_validate(data)

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

    def test_aao_uses_separate_anaerobic_and_anoxic_zones(self) -> None:
        payload = SimulationRequest.model_validate(
            {
                **bsm1_payload().model_dump(),
                "parameters": {
                    **bsm1_payload().parameters.model_dump(),
                    "process_type": "AAO",
                    "model_type": "ASM2d",
                    "anaerobic_volume_m3": 1000,
                    "anoxic_volume_m3": 1500,
                    "aerobic_volume_m3": 4500,
                    "reactor_volume_m3": 7000,
                    "hrt_h": 9.108,
                    "aerobic_kla_d": 180,
                },
            }
        )
        reactor = SimpleNamespace()
        system = SimpleNamespace(
            flowsheet=SimpleNamespace(unit=SimpleNamespace(AS=reactor))
        )
        _configure_reactor(
            system,
            payload,
            {
                "anaerobic_volume": 1000,
                "anoxic_volume": 1500,
                "aerobic_volume": 4500,
                "Q_intr": 36000,
            },
        )
        self.assertEqual(list(reactor.V_tanks), [500, 500, 750, 750, 1500, 1500, 1500])
        self.assertEqual(reactor.internal_recycles, [(6, 2, 36000)])
        self.assertEqual(list(reactor.kLa), [0, 0, 0, 0, 180, 180, 180])

    def test_ao_step_feed_routes_influent_to_two_anoxic_stages(self) -> None:
        payload = bsm1_payload()
        payload.parameters.step_feed_fractions = [0.6, 0.4]
        reactor = SimpleNamespace()
        system = SimpleNamespace(
            flowsheet=SimpleNamespace(unit=SimpleNamespace(AS=reactor))
        )
        _configure_reactor(
            system,
            payload,
            {
                "anaerobic_volume": 0,
                "anoxic_volume": 2000,
                "aerobic_volume": 3000,
                "Q_intr": 30000,
                "effective_kla_d": 120,
            },
        )
        self.assertEqual(
            reactor.influent_fractions.tolist(),
            [[0.6, 0.4, 0, 0, 0], [1, 0, 0, 0, 0]],
        )
        self.assertEqual(reactor.internal_recycles, [(4, 0, 30000)])
        self.assertEqual(list(reactor.kLa), [0, 0, 120, 120, 120])

    def test_step_feed_ratios_must_close(self) -> None:
        data = bsm1_payload().model_dump()
        data["parameters"]["step_feed_fractions"] = [0.7, 0.4]
        with self.assertRaisesRegex(ValueError, "比例之和"):
            SimulationRequest.model_validate(data)

    def test_partial_zone_volumes_are_rejected(self) -> None:
        data = bsm1_payload().model_dump()
        data["parameters"]["anaerobic_volume_m3"] = 1000
        with self.assertRaisesRegex(ValueError, "不应填写厌氧池"):
            SimulationRequest.model_validate(data)

    def test_equipment_energy_uses_runtime_and_auxiliary_power(self) -> None:
        payload = bsm1_payload()
        payload.parameters.aeration_power_kw = 100
        payload.parameters.aeration_hours_d = 12
        payload.parameters.mixing_power_kw = 10
        payload.parameters.pumping_power_kw = 5
        result = _run_activated_sludge_screening(payload)
        self.assertEqual(result.energy_kwh_d, 1560)
        self.assertFalse(result.operational_estimate_evidence.energy_calibrated)

        payload.parameters.operating_data_source = OperatingDataSource.measured
        payload.parameters.measured_total_energy_kwh_d = 1500
        payload.parameters.measured_dry_sludge_kg_d = result.sludge_kg_d
        calibrated = _run_activated_sludge_screening(payload)
        self.assertTrue(calibrated.operational_estimate_evidence.energy_calibrated)
        self.assertTrue(calibrated.operational_estimate_evidence.sludge_calibrated)
        self.assertAlmostEqual(
            calibrated.operational_estimate_evidence.energy_relative_error,
            0.04,
        )

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
                self._diagnostics(False),
            ),
        ):
            result = run_simulation(payload)
        self.assertTrue(result.advanced_treatment_applied)
        self.assertFalse(result.compliance_valid)
        self.assertEqual(result.biological_effluent.tn_mg_l, 14)
        self.assertEqual(result.effluent.tn_mg_l, 12)

    def test_conditional_assessment_does_not_authorize_compliance(self) -> None:
        payload = bsm1_payload()
        payload.parameters.operating_data_source = OperatingDataSource.measured
        payload.parameters.independent_validation_passed = True
        payload.component_concentrations = None
        with patch(
            "app.services.simulation_service.run_dynamic_system",
            return_value=(
                EffluentPrediction(
                    cod_mg_l=40, nh4_n_mg_l=2, tn_mg_l=12,
                    tp_mg_l=0.4, tss_mg_l=8,
                ),
                EffluentPrediction(
                    cod_mg_l=40, nh4_n_mg_l=2, tn_mg_l=12,
                    tp_mg_l=0.4, tss_mg_l=8,
                ),
                self._mapping(), self._balance(), 100, 50, True, [], [],
                self._diagnostics(True),
            ),
        ):
            result = run_simulation(payload)
        self.assertEqual(result.reliability.level, "筛选计算")
        self.assertFalse(result.compliance_valid)

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

    @staticmethod
    def _diagnostics(converged: bool):
        return {
            "actual_simulation_days": 100,
            "convergence_attempts": 1,
            "effective_kla_d": 240,
            "oxygen_transfer_capacity_kg_d": 9000,
            "oxygen_transfer_sufficient": True,
            "estimated_srt_d": None,
            "clarifier_surface_overflow_m_d": 12.3,
            "return_sludge_relative_error": None,
            "dynamic_influent_applied": False,
            "influent_profile_period_days": None,
            "hot_start_applied": False,
        }

    def test_unsupported_process_topology_is_rejected(self) -> None:
        payload = bsm1_payload()
        payload.parameters.process_type = ProcessType.mbr
        with self.assertRaisesRegex(ValueError, "尚未建立MBR专用"):
            _validate_model(payload)

    def test_every_process_is_supported_or_explicitly_blocked(self) -> None:
        supported = {ProcessType.cas, ProcessType.ao, ProcessType.aao}
        for process in ProcessType:
            payload = bsm1_payload()
            payload.parameters.process_type = process
            payload.parameters.model_type = (
                ModelType.asm2d if process == ProcessType.aao else ModelType.asm1
            )
            with self.subTest(process=process.value):
                if process in supported:
                    _validate_model(payload)
                else:
                    with self.assertRaisesRegex(ValueError, "专用单元"):
                        _validate_model(payload)

    def test_supported_process_rejects_wrong_model(self) -> None:
        cases = (
            (ProcessType.cas, ModelType.asm2d, ModelType.asm1),
            (ProcessType.ao, ModelType.asm2d, ModelType.asm1),
            (ProcessType.aao, ModelType.asm1, ModelType.asm2d),
        )
        for process, supplied, expected in cases:
            payload = bsm1_payload()
            payload.parameters.process_type = process
            payload.parameters.model_type = supplied
            with self.subTest(process=process.value):
                with self.assertRaisesRegex(ValueError, expected.value):
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

    def test_instantaneous_limits_follow_amendment_table(self) -> None:
        payload = bsm1_payload()
        payload.influent.temperature_c = 10
        payload.parameters.assessment_basis = "instantaneous"
        payload.parameters.commissioned_before_2006 = True
        payload.parameters.assessment_date = datetime(2026, 7, 31).date()

        limits = _resolve_limits(payload)
        self.assertEqual(limits.cod_mg_l, 75)
        self.assertEqual(limits.nh4_n_mg_l, 15)
        self.assertEqual(limits.tn_mg_l, 20)
        self.assertEqual(limits.tp_mg_l, 1.5)
        self.assertIn("瞬时值", limits.basis)

        payload.parameters.effluent_standard = "grade_b"
        grade_b = _resolve_limits(payload)
        self.assertEqual(grade_b.cod_mg_l, 90)
        self.assertEqual(grade_b.nh4_n_mg_l, 20)
        self.assertEqual(grade_b.tn_mg_l, 25)
        self.assertEqual(grade_b.tp_mg_l, 2.5)

    def test_instantaneous_limits_reject_dates_before_effective_date(self) -> None:
        payload = bsm1_payload()
        payload.parameters.assessment_basis = "instantaneous"
        payload.parameters.assessment_date = datetime(2026, 2, 28).date()
        with self.assertRaisesRegex(ValueError, "生效后"):
            _resolve_limits(payload)

    def test_simulation_result_exposes_shared_quality_thresholds(self) -> None:
        payload = bsm1_payload()
        payload.parameters.convergence_tolerance_per_d = 0.007
        result = _run_activated_sludge_screening(payload)
        self.assertEqual(result.quality_thresholds.component_mapping_relative_error, 0.05)
        self.assertEqual(result.quality_thresholds.hydraulic_relative_error, 1e-5)
        self.assertEqual(result.quality_thresholds.element_balance_relative_error, 0.03)
        self.assertEqual(result.quality_thresholds.state_drift_per_d, 0.007)

    def test_dynamic_influent_requires_cycle_starting_at_zero(self) -> None:
        payload = bsm1_payload().model_dump(mode="json")
        first = payload["influent"]
        payload["influent_series"] = [
            {"elapsed_days": 0.25, "water_quality": first},
            {"elapsed_days": 0.5, "water_quality": first},
            {"elapsed_days": 1.0, "water_quality": first},
        ]
        with self.assertRaisesRegex(ValueError, "第0天"):
            SimulationRequest.model_validate(payload)

    def test_dynamic_influent_is_mapped_to_native_components(self) -> None:
        payload = bsm1_payload().model_dump(mode="json")
        first = payload["influent"]
        peak = {**first, "flow_m3_d": first["flow_m3_d"] * 1.2, "cod_mg_l": 420}
        payload["influent_series"] = [
            {"elapsed_days": 0, "water_quality": first},
            {"elapsed_days": 0.5, "water_quality": peak},
            {"elapsed_days": 1.0, "water_quality": first},
        ]
        request = SimulationRequest.model_validate(payload)
        rows = _influent_profile_rows(request)
        self.assertEqual([row["t"] for row in rows], [0, 0.5, 1.0])
        self.assertEqual(rows[1]["Q"], peak["flow_m3_d"])
        self.assertIn("S_S", rows[1])

    def test_hot_start_model_must_match_request(self) -> None:
        payload = bsm1_payload().model_dump(mode="json")
        payload["hot_start"] = {
            "model_type": "ASM2d",
            "reactor_concentrations_mg_l": {"S_F": 5},
        }
        with self.assertRaisesRegex(ValueError, "模型类型"):
            SimulationRequest.model_validate(payload)

    def test_profile_aggregation_distinguishes_daily_and_instantaneous(self) -> None:
        import numpy as np

        times = np.asarray([0.0, 0.5, 1.0])
        values = np.asarray([10.0, 30.0, 10.0])
        self.assertAlmostEqual(
            _regulatory_profile_value(times, values, False), 20.0
        )
        self.assertEqual(_regulatory_profile_value(times, values, True), 30.0)

    def test_supplied_components_reject_unknown_and_incomplete_ids(self) -> None:
        payload = bsm1_payload()
        payload.component_concentrations["S_BAD"] = 1
        with self.assertRaisesRegex(ValueError, "不包含"):
            _bulk_components(payload)

        payload = bsm1_payload()
        del payload.component_concentrations["S_ND"]
        with self.assertRaisesRegex(ValueError, "不完整"):
            _bulk_components(payload)

    def test_measured_component_source_controls_engineering_evidence(self) -> None:
        payload = bsm1_payload()
        assumed = _mapping_evidence(payload, "用户提供的模型组分")
        self.assertFalse(assumed["engineering_complete"])
        self.assertEqual(assumed["uncertainty_relative"], 0.35)
        payload.component_data_source = "measured"
        measured = _mapping_evidence(payload, "用户提供的模型组分")
        self.assertTrue(measured["engineering_complete"])
        self.assertEqual(measured["uncertainty_relative"], 0.05)

    def test_trace_manifest_hashes_are_deterministic_and_input_sensitive(self) -> None:
        payload = bsm1_payload()
        first = build_simulation_manifest(payload, "测试标准")
        second = build_simulation_manifest(payload, "测试标准")
        self.assertEqual(first.request_sha256, second.request_sha256)
        self.assertEqual(len(first.request_sha256), 64)
        changed = payload.model_copy(
            update={"influent": payload.influent.model_copy(update={"cod_mg_l": 400})}
        )
        changed_manifest = build_simulation_manifest(changed, "测试标准")
        self.assertNotEqual(first.request_sha256, changed_manifest.request_sha256)
        self.assertNotEqual(first.influent_sha256, changed_manifest.influent_sha256)

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
        ), patch(
            "app.services.calibration_service._run_calibration_simulation",
            side_effect=self._fake_simulation,
        ):
            result = calibrate_model(request)
        self.assertEqual(result.training_sample_count, 6)
        self.assertEqual(result.validation_sample_count, 4)
        self.assertGreaterEqual(result.iterations, 1)
        self.assertLessEqual(result.iterations, request.max_iterations)
        self.assertEqual(result.method, "完整QSDsan动态迭代校准")
        self.assertIsNotNone(result.validation_objective)
        self.assertGreater(result.improvement_percent, 50)
        self.assertTrue(result.validation_passed)
        self.assertTrue(result.calibration_passed)
        self.assertTrue(all(value <= 0.2 for value in result.validation_indicator_nrmse.values()))
        self.assertIsNotNone(result.dataset_hash)
        self.assertGreater(result.validation_period_start, result.training_period_end)
        self.assertEqual(len(result.validation_sample_hashes), 4)
        self.assertFalse(result.engineering_qualified)
        self.assertTrue(result.qualification_blockers)

        measured_samples = []
        for sample in request.samples:
            measured_samples.append(
                sample.model_copy(
                    update={
                        "influent": sample.influent.model_copy(
                            update={
                                "soluble_cod_mg_l": 100,
                                "nitrate_n_mg_l": 2,
                                "nitrite_n_mg_l": 0.2,
                                "vfa_as_cod_mg_l": 40,
                                "orthophosphate_p_mg_l": 2,
                            }
                        ),
                        "parameters": sample.parameters.model_copy(
                            update={
                                "operating_data_source": "measured",
                                "reactor_volume_m3": 5000,
                                "waste_sludge_flow_m3_d": 50,
                                "mixed_liquor_tss_mg_l": 3000,
                                "waste_sludge_tss_mg_l": 8000,
                                "clarifier_surface_area_m2": 500,
                                "clarifier_depth_m": 4,
                                "settler_v_max_m_d": 474,
                                "settler_v_max_practical_m_d": 250,
                                "settler_tss_threshold_mg_l": 3000,
                                "aerobic_kla_d": 120,
                                "reactor_ph": 7.2,
                            }
                        ),
                    }
                )
            )
        qualified_payload = request.model_copy(update={"samples": measured_samples})
        self.assertEqual(_qualification_blockers(qualified_payload), [])

    def test_calibration_rejects_same_plant_same_day_duplicates(self) -> None:
        sample = {
            "group_id": "同日重复厂",
            "sample_time": datetime(2026, 1, 1, 8),
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
        later_same_day = {
            **sample,
            "sample_time": datetime(2026, 1, 1, 16),
        }
        with self.assertRaisesRegex(ValueError, "同厂同日只能保留一条"):
            calibrate_model(
                ModelCalibrationRequest(
                    project_id="duplicate-day",
                    samples=[sample, later_same_day],
                )
            )

    def test_validation_rejects_one_indicator_over_twenty_percent(self) -> None:
        start = datetime(2026, 1, 1)
        samples = []
        for index in range(6):
            samples.append(
                {
                    "group_id": "一厂",
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
                        "tss_mg_l": 5,
                    },
                }
            )
        request = ModelCalibrationRequest(
            project_id="indicator-gate",
            samples=samples,
            validation_fraction=0.2,
        )
        with patch(
            "app.services.calibration_service._run_activated_sludge_screening",
            side_effect=self._fake_simulation,
        ), patch(
            "app.services.calibration_service._run_calibration_simulation",
            side_effect=self._fake_simulation,
        ):
            result = calibrate_model(request)
        self.assertFalse(result.validation_passed)
        self.assertGreater(result.validation_indicator_nrmse["tss_mg_l"], 0.2)

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
        ), patch(
            "app.services.calibration_service._run_calibration_simulation",
            side_effect=worsening_simulation,
        ):
            result = calibrate_model(request)
        self.assertEqual(result.factors.cod, 1)
        self.assertEqual(result.improvement_percent, 0)
        self.assertTrue(any("自动拒绝" in item for item in result.warnings))

    def test_mixed_processes_are_rejected(self) -> None:
        samples = []
        for index, process in enumerate((ProcessType.cas, ProcessType.ao)):
            samples.append(
                {
                    "group_id": "一厂",
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
                    "parameters": {
                        "process_type": process,
                        "model_type": ModelType.asm1,
                    },
                }
            )
        with self.assertRaisesRegex(ValueError, "同一种工艺"):
            calibrate_model(
                ModelCalibrationRequest(project_id="mixed", samples=samples)
            )


class DedicatedTopologyParameterTests(unittest.TestCase):
    def test_oxidation_ditch_requires_consistent_loop_volume(self) -> None:
        with self.assertRaisesRegex(ValueError, "氧化沟专用拓扑缺少"):
            ProcessParameters(process_type=ProcessType.oxidation_ditch)
        params = ProcessParameters(
            process_type=ProcessType.oxidation_ditch,
            oxidation_ditch_channel_count=2,
            oxidation_ditch_loop_volume_m3=6000,
            anoxic_volume_m3=1500,
            aerobic_volume_m3=4500,
        )
        self.assertEqual(params.oxidation_ditch_channel_count, 2)

    def test_sbr_cycle_requires_all_phases_to_close(self) -> None:
        with self.assertRaisesRegex(ValueError, "序批式各阶段时长"):
            ProcessParameters(
                process_type=ProcessType.sbr,
                sbr_reactor_count=4,
                sbr_cycle_h=6,
                sbr_fill_h=1,
                sbr_anoxic_h=1,
                sbr_aerobic_h=2,
                sbr_settle_h=1,
                sbr_decant_h=0.5,
                sbr_decant_fraction=0.3,
            )

    def test_mbr_requires_membrane_capacity_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "膜生物反应器专用拓扑缺少"):
            ProcessParameters(process_type=ProcessType.mbr)
        params = ProcessParameters(
            process_type=ProcessType.mbr,
            reactor_volume_m3=5000,
            mbr_membrane_area_m2=20000,
            mbr_design_flux_l_m2_h=15,
            mbr_recovery=0.95,
            mbr_air_scour_power_kw=80,
        )
        self.assertEqual(params.mbr_recovery, 0.95)


if __name__ == "__main__":
    unittest.main()
