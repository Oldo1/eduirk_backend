from __future__ import annotations

import os
import tempfile
import unittest
from io import BytesIO

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/mky_certificate_variables_test.db"
os.environ["ENABLE_RAG"] = "false"

import pandas as pd
from fastapi.testclient import TestClient

from database import Base, engine
from main import app
from utils.certificate_text import apply_variables, extract_placeholders
from utils.excel_batch import read_fio_list_from_excel, read_rows_from_excel
from utils.name_declension import (
    detect_certificate_context,
    prepare_certificate_variables,
    resolve_name_case_and_gender,
)


def _xlsx_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = BytesIO()
    pd.DataFrame(rows).to_excel(buffer, index=False, engine="openpyxl")
    return buffer.getvalue()


class CertificateVariablesTest(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

    def test_extract_placeholders_returns_unique_names_in_order(self):
        text = "{ФИО} учится в {Класс}. {ФИО} {broken"

        self.assertEqual(extract_placeholders(text), ["ФИО", "Класс"])

    def test_extract_placeholders_ignores_gender_variants(self):
        text = "Вручается {род:ученику|ученице} {ФИО} из {Класс}"

        self.assertEqual(extract_placeholders(text), ["ФИО", "Класс"])

    def test_read_rows_from_excel_preserves_dynamic_headers(self):
        content = _xlsx_bytes(
            [
                {
                    "ФИО": "Иванов Иван Иванович",
                    "Класс": "10А",
                    "Школа": "Лицей 1",
                    "Предмет": "Математика",
                },
                {
                    "ФИО": "Петрова Мария Сергеевна",
                    "Класс": "9Б",
                    "Школа": "Гимназия",
                    "Предмет": "Физика",
                },
            ]
        )

        result = read_rows_from_excel(content)

        self.assertEqual(result.headers, ["ФИО", "Класс", "Школа", "Предмет"])
        self.assertEqual(result.fio_column, "ФИО")
        self.assertEqual(result.row_count, 2)
        self.assertEqual(result.rows[0]["Класс"], "10А")
        self.assertEqual(result.rows[0]["Предмет"], "Математика")

    def test_legacy_fio_reader_still_returns_names_and_column(self):
        content = _xlsx_bytes(
            [
                {"ФИО": "Иванов Иван Иванович", "Класс": "10А"},
                {"ФИО": "Петрова Мария Сергеевна", "Класс": "9Б"},
            ]
        )

        names, column = read_fio_list_from_excel(content)

        self.assertEqual(column, "ФИО")
        self.assertEqual(names, ["Иванов Иван Иванович", "Петрова Мария Сергеевна"])

    def test_name_declension_context_defaults_nagrazhdaetsya_to_nominative(self):
        elements = [
            type("Element", (), {"text": "Награждается", "y_mm": 20})(),
            type("Element", (), {"text": "{ФИО}", "y_mm": 40})(),
        ]

        context = detect_certificate_context(elements)
        resolved = resolve_name_case_and_gender(context, "Иванов Иван Иванович")
        variables = prepare_certificate_variables(elements, {"ФИО": "Иванов Иван Иванович"})

        self.assertEqual(resolved.case, "nominative")
        self.assertEqual(variables["ФИО"], "Иванов Иван Иванович")

    def test_name_declension_context_uses_dative_for_vruchaetsya(self):
        elements = [
            type("Element", (), {"text": "Вручается", "y_mm": 20})(),
            type("Element", (), {"text": "{ФИО}", "y_mm": 40})(),
        ]

        resolved = resolve_name_case_and_gender(
            detect_certificate_context(elements),
            "Иванов Иван Иванович",
        )
        variables = prepare_certificate_variables(elements, {"ФИО": "Иванов Иван Иванович"})

        self.assertEqual(resolved.case, "dative")
        self.assertEqual(variables["ФИО"], "Иванову Ивану Ивановичу")

    def test_name_declension_context_uses_dative_for_vruchaetsya_inside_sentence(self):
        elements = [
            type("Element", (), {"text": "Сертификат", "y_mm": 10})(),
            type("Element", (), {"text": "вручается ученику {Класс} школы за участие в", "y_mm": 20})(),
            type("Element", (), {"text": "{ФИО}", "y_mm": 40})(),
        ]

        context = detect_certificate_context(elements)
        resolved = resolve_name_case_and_gender(context, "Сидоров Михаил Евгеньевич")
        variables = prepare_certificate_variables(elements, {"ФИО": "Сидоров Михаил Евгеньевич"})

        self.assertEqual(resolved.case, "dative")
        self.assertEqual(variables["ФИО"], "Сидорову Михаилу Евгеньевичу")

    def test_name_declension_dative_declines_female_surname_from_masculine_form(self):
        elements = [
            type("Element", (), {"text": "Вручается", "y_mm": 20})(),
            type("Element", (), {"text": "{ФИО}", "y_mm": 40})(),
        ]

        variables = prepare_certificate_variables(elements, {"ФИО": "Сидоров Елена Михайловна"})

        self.assertEqual(variables["ФИО"], "Сидоровой Елене Михайловне")

    def test_gender_variants_use_detected_fio_gender(self):
        elements = [
            type("Element", (), {"text": "Вручается {род:ученику|ученице} {ФИО}", "y_mm": 20})(),
        ]

        variables = prepare_certificate_variables(elements, {"ФИО": "Сидоров Елена Михайловна"})
        rendered = apply_variables(elements[0].text, variables)

        self.assertEqual(rendered, "Вручается ученице Сидоровой Елене Михайловне")

    def test_gender_variants_use_male_form_for_male_fio(self):
        elements = [
            type("Element", (), {"text": "Вручается {род:ученику|ученице} {ФИО}", "y_mm": 20})(),
        ]

        variables = prepare_certificate_variables(elements, {"ФИО": "Сидоров Михаил Евгеньевич"})
        rendered = apply_variables(elements[0].text, variables)

        self.assertEqual(rendered, "Вручается ученику Сидорову Михаилу Евгеньевичу")

    def test_name_declension_dative_declines_male_full_name(self):
        elements = [
            type("Element", (), {"text": "Вручается", "y_mm": 20})(),
            type("Element", (), {"text": "{ФИО}", "y_mm": 40})(),
        ]

        variables = prepare_certificate_variables(elements, {"ФИО": "Сидоров Михаил Евгеньевич"})

        self.assertEqual(variables["ФИО"], "Сидорову Михаилу Евгеньевичу")

    def test_template_variables_and_excel_inspect_endpoints(self):
        client = TestClient(app)
        template_payload = {
            "name": "Variables template",
            "background_url": "/static/certificates/backgrounds/bg.jpg",
            "signers_y_mm": 248,
            "signers_block_x_mm": 105,
            "signers_row_height_mm": 32,
            "signers_band_width_mm": 168,
            "signers_font_size": 10,
            "signers_text_color": "#1e293b",
            "signers_font_weight": "400",
            "margin_left_mm": 12,
            "margin_right_mm": 12,
            "margin_top_mm": 12,
            "margin_bottom_mm": 12,
            "elements": [
                {
                    "text": "Награждается {ФИО} из {Класс} за {Предмет}",
                    "is_variable": True,
                    "x_mm": 105,
                    "y_mm": 100,
                    "font_size": 24,
                    "align": "center",
                    "color": "#0F172A",
                    "font_weight": "400",
                    "max_width_mm": None,
                    "max_height_mm": None,
                }
            ],
            "signers": [],
        }
        template_response = client.post("/certificates/templates/full", json=template_payload)
        self.assertEqual(template_response.status_code, 200, template_response.text)
        template_id = template_response.json()["template"]["id"]

        variables_response = client.get(f"/certificates/templates/{template_id}/variables")
        self.assertEqual(variables_response.status_code, 200, variables_response.text)
        self.assertEqual(
            variables_response.json(),
            {"template_id": template_id, "variables": ["ФИО", "Класс", "Предмет"]},
        )

        content = _xlsx_bytes(
            [{"ФИО": "Иванов Иван Иванович", "Класс": "10А", "Школа": "Лицей"}]
        )
        files = {
            "file": (
                "students.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        }
        inspect_response = client.post(
            "/certificates/excel/inspect",
            data={"template_id": str(template_id)},
            files=files,
        )

        self.assertEqual(inspect_response.status_code, 200, inspect_response.text)
        body = inspect_response.json()
        self.assertEqual(body["headers"], ["ФИО", "Класс", "Школа"])
        self.assertEqual(body["row_count"], 1)
        self.assertEqual(body["fio_column"], "ФИО")
        self.assertEqual(body["template_variables"], ["ФИО", "Класс", "Предмет"])
        self.assertEqual(body["matched_columns"], ["ФИО", "Класс"])
        self.assertEqual(body["missing_columns"], ["Предмет"])
        self.assertEqual(body["preview_rows"][0]["Школа"], "Лицей")


if __name__ == "__main__":
    unittest.main()
