from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from io import BytesIO
from types import SimpleNamespace

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/mky_certificate_variables_test.db"

import pandas as pd
from fastapi.testclient import TestClient

from auth import get_current_user
from database import Base, engine
from main import app
from utils.certificate_text import apply_variables, extract_placeholders
from utils.excel_batch import read_fio_list_from_excel, read_rows_from_excel
from utils.name_declension import (
    detect_certificate_context,
    decline_organization,
    prepare_certificate_variables,
    resolve_name_case_and_gender,
)


def _xlsx_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = BytesIO()
    pd.DataFrame(rows).to_excel(buffer, index=False, engine="openpyxl")
    return buffer.getvalue()


class CertificateVariablesTest(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=10,
            email="methodist@example.test",
            role="methodist",
            is_active=True,
        )
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_extract_placeholders_returns_unique_names_in_order(self):
        text = "{ФИО} учится в {Класс}. {ФИО} {broken"

        self.assertEqual(extract_placeholders(text), ["ФИО", "Класс"])

    def test_extract_placeholders_ignores_gender_variants(self):
        text = "Вручается {род:ученику|ученице} {ФИО} из {Класс}"

        self.assertEqual(extract_placeholders(text), ["ФИО", "Класс"])

    def test_extract_placeholders_returns_base_names_for_explicit_grammar(self):
        text = "{ФИО | дательный} из {Школа | родительный} за {Мероприятие}"

        self.assertEqual(extract_placeholders(text), ["ФИО", "Школа", "Мероприятие"])

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

    def test_explicit_grammar_placeholders_use_prepared_case_variants(self):
        elements = [
            type("Element", (), {"text": "{ФИО | дательный} из {Школа | родительный}", "y_mm": 40})(),
        ]

        variables = prepare_certificate_variables(
            elements,
            {
                "ФИО": "Иванов Иван Иванович",
                "Школа": "Школа №52",
            },
        )
        rendered = apply_variables(elements[0].text, variables)

        self.assertEqual(rendered, "Иванову Ивану Ивановичу из Школы №52")

    def test_override_columns_have_priority_over_automatic_declension(self):
        elements = [
            type("Element", (), {"text": "{ФИО | дательный} из {Школа | родительный}", "y_mm": 40})(),
        ]

        variables = prepare_certificate_variables(
            elements,
            {
                "ФИО": "Иванов Иван Иванович",
                "ФИО_дательный": "уважаемому Иванову Ивану Ивановичу",
                "Школа": "Школа №1",
                "Школа_родительный": "МБОУ Школы №1",
            },
        )

        self.assertEqual(apply_variables(elements[0].text, variables), "уважаемому Иванову Ивану Ивановичу из МБОУ Школы №1")

    def test_organization_declension_mvp(self):
        self.assertEqual(decline_organization("Школа №1", "genitive")[0], "Школы №1")
        self.assertEqual(decline_organization("Лицей №15", "genitive")[0], "Лицея №15")
        self.assertEqual(decline_organization("Гимназия №67", "genitive")[0], "Гимназии №67")

    def test_context_declines_event_variables_prepositional(self):
        cases = [
            (
                "Олимпиада по информатике",
                "за участие в олимпиаде по информатике",
            ),
            (
                "Конкурс исследовательских проектов",
                "за участие в конкурсе исследовательских проектов",
            ),
            (
                "Научно-практическая конференция школьников",
                "за участие в научно-практической конференции школьников",
            ),
        ]

        for event_name, expected in cases:
            with self.subTest(event_name=event_name):
                elements = [
                    type("Element", (), {"text": "за участие в {Мероприятие}", "y_mm": 40})(),
                ]

                variables = prepare_certificate_variables(elements, {"Мероприятие": event_name})

                self.assertEqual(apply_variables(elements[0].text, variables), expected)

    def test_context_declines_school_after_gender_variant(self):
        elements = [
            type("Element", (), {"text": "ученик|ученица {Школа}", "y_mm": 40})(),
        ]

        variables = prepare_certificate_variables(
            elements,
            {
                "ФИО": "Лебедева Юлия Михайловна",
                "Школа": "Школа № 12",
            },
        )

        self.assertEqual(apply_variables(elements[0].text, variables), "ученица школы № 12")

    def test_batch_archive_content_disposition_supports_cyrillic_filename(self):
        client = TestClient(app)
        template_payload = {
            "name": "Batch Cyrillic archive",
            "background_url": None,
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
                    "text": "Награждается {ФИО} за участие в {Мероприятие}",
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

        content = _xlsx_bytes(
            [
                {
                    "ФИО": f"Иванов Иван Иванович {index}",
                    "Мероприятие": "Олимпиада по информатике",
                }
                for index in range(1, 101)
            ]
        )
        files = {
            "file": (
                "students.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        }

        response = client.post(
            "/certificates/batch",
            data={"template_id": str(template_id), "archive_name": "Грамоты"},
            files=files,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/zip")
        disposition = response.headers["content-disposition"]
        self.assertIn('filename="certificates.zip"', disposition)
        self.assertIn("filename*=UTF-8''%D0%93%D1%80%D0%B0%D0%BC%D0%BE%D1%82%D1%8B.zip", disposition)
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            self.assertEqual(len(archive.namelist()), 100)

    def test_batch_can_return_json_download_url_for_frontend(self):
        client = TestClient(app)
        template_payload = {
            "name": "Batch JSON archive",
            "background_url": None,
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
                    "text": "Награждается {ФИО} за участие в {Мероприятие}",
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

        content = _xlsx_bytes(
            [
                {
                    "ФИО": "Иванов Иван Иванович",
                    "Мероприятие": "Олимпиада по информатике",
                }
            ]
        )
        files = {
            "file": (
                "students.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        }

        response = client.post(
            "/certificates/batch",
            data={
                "template_id": str(template_id),
                "archive_name": "Грамоты",
                "response_mode": "json",
            },
            files=files,
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["filename"], "Грамоты.zip")
        self.assertEqual(body["count"], 1)
        self.assertTrue(body["file_url"].startswith("/static/certificates/generated/batch_"))
        self.assertTrue(body["file_url"].endswith(".zip"))
        archive_path = os.path.join("static", "certificates", "generated", os.path.basename(body["file_url"]))
        self.assertTrue(os.path.exists(archive_path))
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(len(archive.namelist()), 1)

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

    def test_excel_inspect_returns_processed_preview_and_blocks_missing_required_columns(self):
        client = TestClient(app)
        template_payload = {
            "name": "Grammar template",
            "background_url": None,
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
                    "text": "{ФИО | дательный} из {Школа | родительный} за {Мероприятие}",
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

        content = _xlsx_bytes(
            [
                {
                    "ФИО": "Иванов Иван Иванович",
                    "ФИО_дательный": "ручному Иванову Ивану Ивановичу",
                    "Школа": "Школа №52",
                }
            ]
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
        self.assertEqual(body["template_variables"], ["ФИО", "Школа", "Мероприятие"])
        self.assertEqual(body["matched_columns"], ["ФИО", "Школа"])
        self.assertEqual(body["missing_columns"], ["Мероприятие"])
        self.assertEqual(body["processed_preview"][0]["ФИО:дательный"], "ручному Иванову Ивану Ивановичу")
        self.assertEqual(body["processed_preview"][0]["Школа:родительный"], "Школы №52")

        blocked = client.post(
            "/certificates/batch",
            data={"template_id": str(template_id)},
            files=files,
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("В Excel не найдены обязательные столбцы: Мероприятие", blocked.json()["detail"])


if __name__ == "__main__":
    unittest.main()
