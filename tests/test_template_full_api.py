import os
import tempfile
import unittest
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.gettempdir()}/mky_template_full_test.db"
os.environ["ENABLE_RAG"] = "false"

from fastapi.testclient import TestClient

from database import Base, engine
from main import app


class TemplateFullApiTest(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

    def tearDown(self):
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
