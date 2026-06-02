import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/mky_template_full_test.db"

from fastapi.testclient import TestClient

from auth import get_current_user
from database import Base, engine
from main import app


class TemplateFullApiTest(unittest.TestCase):
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
        for path in Path("static/fonts/custom").glob("*_CustomSans.ttf"):
            path.unlink(missing_ok=True)

    def test_create_template_full_persists_template_elements_and_signers(self):
        client = TestClient(app)
        payload = {
            "name": "Full template",
            "background_url": "/static/certificates/backgrounds/bg.jpg",
            "signers_y_mm": 248,
            "signers_block_x_mm": 105,
            "signers_row_height_mm": 32,
            "signers_band_width_mm": 168,
            "signers_font_size": 10,
            "signers_text_color": "#1e293b",
            "signers_font_weight": "400",
            "signers_font_family": "DejaVu",
            "margin_left_mm": 12,
            "margin_right_mm": 12,
            "margin_top_mm": 12,
            "margin_bottom_mm": 12,
            "elements": [
                {
                    "text": "{ФИО}",
                    "is_variable": True,
                    "x_mm": 105,
                    "y_mm": 100,
                    "font_size": 24,
                    "align": "center",
                    "color": "#0F172A",
                    "font_weight": "400",
                    "font_family": "DejaVu",
                    "max_width_mm": None,
                    "max_height_mm": None,
                }
            ],
            "signers": [
                {
                    "order": 1,
                    "position": "Директор",
                    "full_name": "Иванов И.И.",
                    "facsimile_url": None,
                    "offset_y_mm": 0,
                    "facsimile_offset_x_mm": 0,
                    "facsimile_offset_y_mm": 0,
                    "facsimile_scale": 1,
                }
            ],
        }

        response = client.post("/certificates/templates/full", json=payload)

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["template"]["name"], "Full template")
        self.assertEqual(body["template"]["signers_font_family"], "DejaVu")
        self.assertEqual(body["elements"][0]["text"], "{ФИО}")
        self.assertEqual(body["elements"][0]["font_family"], "DejaVu")
        self.assertEqual(body["signers"][0]["full_name"], "Иванов И.И.")

        template_id = body["template"]["id"]
        loaded = client.get(f"/certificates/templates/{template_id}/full")
        self.assertEqual(loaded.status_code, 200, loaded.text)
        self.assertEqual(len(loaded.json()["elements"]), 1)
        self.assertEqual(len(loaded.json()["signers"]), 1)
        self.assertEqual(loaded.json()["elements"][0]["font_family"], "DejaVu")
        self.assertEqual(loaded.json()["template"]["signers_font_family"], "DejaVu")

    def test_full_template_persists_extended_canvas_elements(self):
        client = TestClient(app)
        payload = {
            "name": "Canvas template",
            "background_url": "/static/certificates/backgrounds/bg.jpg",
            "signers_y_mm": 248,
            "signers_block_x_mm": 105,
            "signers_row_height_mm": 32,
            "signers_band_width_mm": 168,
            "signers_font_size": 10,
            "signers_text_color": "#1e293b",
            "signers_font_weight": "400",
            "signers_font_family": "DejaVu",
            "margin_left_mm": 12,
            "margin_right_mm": 12,
            "margin_top_mm": 12,
            "margin_bottom_mm": 12,
            "elements": [
                {
                    "id": "txt_1",
                    "type": "text",
                    "text": "{ФИО | дательный}",
                    "is_variable": True,
                    "x_mm": 105,
                    "y_mm": 120,
                    "width": 160,
                    "height": 24,
                    "font_size": 24,
                    "align": "center",
                    "color": "#0F172A",
                    "font_weight": "700",
                    "font_family": "DejaVu",
                    "italic": True,
                    "underline": True,
                    "line_height": 1.35,
                    "z_index": 20,
                    "hidden": False,
                    "locked": True,
                    "opacity": 0.9,
                    "anchor": "center",
                    "variableName": "ФИО",
                    "grammar_settings": {"value_type": "fio", "case": "dative"},
                    "signerGroupId": "signer_1",
                    "max_width_mm": 160,
                    "max_height_mm": 24,
                },
                {
                    "id": "fac_1",
                    "type": "signature",
                    "text": "",
                    "is_variable": False,
                    "x_mm": 105,
                    "y_mm": 232,
                    "width": 52,
                    "height": 20,
                    "font_size": 12,
                    "align": "center",
                    "color": "#0F172A",
                    "font_weight": "400",
                    "font_family": "DejaVu",
                    "source_url": "/static/certificates/facsimiles/fac.png",
                    "z_index": 32,
                    "hidden": False,
                    "locked": False,
                    "opacity": 0.85,
                    "signerGroupId": "signer_1",
                },
                {
                    "id": "line_1",
                    "type": "line",
                    "text": "",
                    "is_variable": False,
                    "x_mm": 105,
                    "y_mm": 236,
                    "width": 70,
                    "height": 0.6,
                    "font_size": 12,
                    "align": "center",
                    "color": "#1e293b",
                    "font_weight": "400",
                    "font_family": "DejaVu",
                    "z_index": 31,
                    "hidden": True,
                    "locked": False,
                    "opacity": 1,
                    "signerGroupId": "signer_1",
                },
            ],
            "signers": [],
        }

        response = client.post("/certificates/templates/full", json=payload)

        self.assertEqual(response.status_code, 200, response.text)
        template_id = response.json()["template"]["id"]
        loaded = client.get(f"/certificates/templates/{template_id}/full")
        self.assertEqual(loaded.status_code, 200, loaded.text)
        elements = loaded.json()["elements"]
        self.assertEqual([item["id"] for item in elements], ["txt_1", "line_1", "fac_1"])
        self.assertEqual(elements[0]["type"], "text")
        self.assertEqual(elements[0]["font_weight"], "700")
        self.assertTrue(elements[0]["italic"])
        self.assertTrue(elements[0]["underline"])
        self.assertEqual(elements[0]["line_height"], 1.35)
        self.assertEqual(elements[0]["z_index"], 20)
        self.assertTrue(elements[0]["locked"])
        self.assertEqual(elements[0]["signerGroupId"], "signer_1")
        self.assertEqual(elements[2]["type"], "signature")
        self.assertEqual(elements[2]["source_url"], "/static/certificates/facsimiles/fac.png")
        self.assertEqual(elements[1]["type"], "line")
        self.assertTrue(elements[1]["hidden"])

    def test_full_template_update_accepts_numeric_element_ids_from_existing_templates(self):
        client = TestClient(app)
        payload = {
            "name": "Legacy ids",
            "background_url": None,
            "signers_y_mm": 248,
            "signers_block_x_mm": 105,
            "signers_row_height_mm": 32,
            "signers_band_width_mm": 168,
            "signers_font_size": 10,
            "signers_text_color": "#1e293b",
            "signers_font_weight": "400",
            "signers_font_family": "DejaVu",
            "margin_left_mm": 12,
            "margin_right_mm": 12,
            "margin_top_mm": 12,
            "margin_bottom_mm": 12,
            "elements": [
                {
                    "text": "ГРАМОТА",
                    "is_variable": False,
                    "x_mm": 105,
                    "y_mm": 42,
                    "font_size": 30,
                    "align": "center",
                    "color": "#004f75",
                    "font_weight": "700",
                    "font_family": "DejaVu",
                }
            ],
            "signers": [],
        }
        created = client.post("/certificates/templates/full", json=payload)
        self.assertEqual(created.status_code, 200, created.text)
        template_id = created.json()["template"]["id"]
        loaded_element = client.get(f"/certificates/templates/{template_id}/full").json()["elements"][0]

        payload["elements"] = [
            {
                **loaded_element,
                "text": "ГРАМОТА обновлена",
                "width": 160,
                "height": 24,
            }
        ]
        response = client.put(f"/certificates/templates/{template_id}/full", json=payload)

        self.assertEqual(response.status_code, 200, response.text)
        saved = client.get(f"/certificates/templates/{template_id}/full").json()["elements"][0]
        self.assertEqual(saved["text"], "ГРАМОТА обновлена")
        self.assertEqual(str(saved["id"]), str(loaded_element["id"]))

    def test_upload_font_accepts_ttf_and_rejects_non_fonts(self):
        client = TestClient(app)
        font_path = Path("static/fonts/DejaVuSans.ttf")
        font_bytes = font_path.read_bytes()

        response = client.post(
            "/certificates/upload-font",
            files={"file": ("CustomSans.ttf", font_bytes, "font/ttf")},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["font_family"], "CustomSans")
        self.assertTrue(body["font_url"].startswith("/static/fonts/custom/"))

        rejected = client.post(
            "/certificates/upload-font",
            files={"file": ("not-a-font.txt", b"hello", "text/plain")},
        )
        self.assertEqual(rejected.status_code, 400)

if __name__ == "__main__":
    unittest.main()
