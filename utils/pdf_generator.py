from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os

# ====================== РЕГИСТРАЦИЯ ШРИФТА ======================
def register_fonts():
    font_path = os.path.join("static", "fonts", "DejaVuSans.ttf")   # новый путь
    
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont('DejaVu', font_path))
            print(f"✅ Шрифт DejaVu успешно зарегистрирован: {font_path}")
            return True
        except Exception as e:
            print(f"❌ Ошибка регистрации шрифта: {e}")
            return False
    else:
        print(f"⚠️ Шрифт не найден: {font_path}")
        print("Положите DejaVuSans.ttf в папку static/fonts/")
        return False

register_fonts()


def generate_certificate_pdf(template, elements, fio: str, event_name: str) -> BytesIO:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Фон
    if template.background_url:
        bg_path = template.background_url.lstrip('/')
        if os.path.exists(bg_path):
            try:
                from reportlab.lib.utils import ImageReader
                c.drawImage(ImageReader(bg_path), 0, 0, width=width, height=height, preserveAspectRatio=True)
                print(f"Фон успешно добавлен: {bg_path}")
            except Exception as e:
                print(f"Ошибка при добавлении фона: {e}")
        else:
            print(f"Фон не найден: {bg_path}")

    # Текстовые элементы
    for el in sorted(elements, key=lambda x: x.y_mm):
        text = el.text
        if el.is_variable:
            text = text.replace("{fio}", fio).replace("{event}", event_name).replace("{event_name}", event_name)

        try:
            c.setFont("DejaVu", el.font_size or 24)
        except:
            c.setFont("Helvetica", el.font_size or 24)  # fallback

        x = el.x_mm * 2.83465
        y = height - (el.y_mm * 2.83465)

        align = getattr(el, 'align', 'center')
        if align == "center":
            c.drawCentredString(x, y, text)
        elif align == "right":
            c.drawRightString(x, y, text)
        else:
            c.drawString(x, y, text)

    c.save()
    buffer.seek(0)
    return buffer