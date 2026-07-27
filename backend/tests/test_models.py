import unittest

from app.models.schemas import ModelType, SimulationRequest
from app.services.model_catalog import list_models
from app.services.simulation_service import run_simulation


def make_payload(model_type: str = "ASM2d") -> SimulationRequest:
    return SimulationRequest.model_validate(
        {
            "project_id": "test-project",
            "influent": {
                "flow_m3_d": 10_000,
                "cod_mg_l": 300,
                "bod_mg_l": 160,
                "nh4_n_mg_l": 35,
                "tn_mg_l": 45,
                "tp_mg_l": 6,
                "tss_mg_l": 220,
                "ph": 7.2,
                "temperature_c": 20,
            },
            "parameters": {
                "process_type": "AAO",
                "model_type": model_type,
                "hrt_h": 12,
                "srt_d": 15,
                "internal_recycle_ratio": 2,
            },
        }
    )


class ModelCatalogTests(unittest.TestCase):
    def test_catalog_contains_four_reference_models(self) -> None:
        self.assertEqual(
            {item.id for item in list_models()},
            {ModelType.asm1, ModelType.asm2d, ModelType.masm2d, ModelType.adm1},
        )

    def test_asm2d_result_is_parameterized_and_bounded(self) -> None:
        result = run_simulation(make_payload())
        self.assertEqual(result.model_id, ModelType.asm2d)
        self.assertLess(result.effluent.cod_mg_l, 300)
        self.assertLess(result.effluent.nh4_n_mg_l, 35)
        self.assertTrue(all(0 <= value <= 1 for value in result.removal_rates.model_dump().values()))
        self.assertIn("screening", result.engine)

    def test_longer_aerobic_contact_reduces_cod(self) -> None:
        short = make_payload()
        long = make_payload()
        short.parameters.hrt_h = 6
        long.parameters.hrt_h = 18
        self.assertLess(
            run_simulation(long).effluent.cod_mg_l,
            run_simulation(short).effluent.cod_mg_l,
        )

    def test_extended_models_reject_incomplete_bulk_input(self) -> None:
        for model_type in ("mASM2d", "ADM1"):
            with self.subTest(model=model_type), self.assertRaises(ValueError):
                run_simulation(make_payload(model_type))


if __name__ == "__main__":
    unittest.main()
