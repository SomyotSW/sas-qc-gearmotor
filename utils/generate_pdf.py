from reportlab.lib.utils import ImageReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.colors import red, black, gray
import io
import requests
from PIL import Image

pdfmetrics.registerFont(TTFont('THSarabunNew', 'static/fonts/THSarabunNew.ttf'))

# Path ของโลโก้ SAS
sas_logo_path = 'static/logos_sas.png'


def draw_image(c, image_url, center_x, y_top, width):
    try:
        img_data = requests.get(image_url).content
        img = Image.open(io.BytesIO(img_data))
        img = img.convert("RGB")
        img.thumbnail((800, 600))

        img_width = width
        img_height = img_width * (4 / 3)
        x = center_x - (img_width / 2)

        img_io = io.BytesIO()
        img.save(img_io, format='PNG')
        img_io.seek(0)
        c.drawImage(ImageReader(img_io), x, y_top - img_height, img_width, img_height)
        return y_top - img_height - 10
    except Exception as e:
        print(f"Error loading image {image_url}: {e}", flush=True)
        return y_top - 10


def create_qc_pdf(data, image_urls=[], image_labels=[]):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 2 * cm
    line = height - margin

    def draw_text(text, bold=False, color=None):
        nonlocal line
        font_name = 'THSarabunNew'
        font_size = 16
        c.setFont(font_name, font_size)
        c.setFillColor(color if color else black)
        c.drawString(margin, line, text)
        line -= 22

    def draw_header():
        logo_width = 3 * cm
        x = width - logo_width - 1.5 * cm
        y = height - 3 * cm
        c.drawImage(sas_logo_path, x, y, width=logo_width, preserveAspectRatio=True)

    draw_header()
    c.setFont("THSarabunNew", 22)
    c.drawCentredString(width / 2, line, "รายงานการตรวจสอบ QC มอเตอร์เกียร์")
    line -= 40

    draw_text(f"Serial Number: {data.get('serial', '-')}", bold=True)
    draw_text(f"วันที่ตรวจสอบ: {data.get('date', '-')}")
    draw_text(f"ประเภทสินค้า: {data.get('product_type', '-')}")
    draw_text(f"Nameplate: {data.get('motor_nameplate', '-')}")

    product_type = data.get("product_type", "").lower()
    is_acdc_or_bldc = "ac/dc" in product_type or "bldc" in product_type
    is_servo = "servo" in product_type
    is_other = not is_acdc_or_bldc and not is_servo

    if is_servo:
        draw_text("**ไม่ประกอบสินค้า", bold=True, color=red)
        draw_text("**ไม่เติมน้ำมันเกียร์", bold=True, color=red)

    draw_text("")

    if data.get("motor_current"):
        draw_text(f"ค่ากระแสมอเตอร์: {data['motor_current']} A")
    if data.get("gear_ratio"):
        draw_text(f"อัตราทดเกียร์: {data['gear_ratio']}")
    if data.get("gear_sound"):
        draw_text(f"เสียงเกียร์: {data['gear_sound']} dB")

    if not is_acdc_or_bldc and not is_servo:
        draw_text(f"น้ำมันเกียร์ (ลิตร): {data.get('oil_liters', '-') or '-'} ลิตร")
        draw_text(f"สถานะการเติมน้ำมัน: {data.get('oil_filled', '-')}")
    elif is_acdc_or_bldc:
        draw_text("*ไม่ต้องเติมน้ำมันเกียร์", bold=True, color=red)

    draw_text(f"ระยะเวลารับประกัน: {data.get('warranty', '-')} เดือน", bold=True, color=red)
    draw_text(f"ผู้ตรวจสอบ: {data.get('inspector', '-')}")

    if is_servo:
        draw_text("")
        draw_text("**การรับประกันสินค้า 18 เดือน", bold=True, color=red)

    c.setFillColor(gray)
    c.line(1.5 * cm, 3.5 * cm, width - 1.5 * cm, 3.5 * cm)

    c.setFont("THSarabunNew", 18)
    c.setFillColor(black)
    c.drawString(2 * cm, 1 * cm, "📞 SAS Service: 081-9216225")
    c.drawRightString(width - 2 * cm, 1 * cm, "📞 SAS Sales: 081-9216225 คุณสมยศ")

    c.showPage()

    draw_header()
    c.setFont("THSarabunNew", 18)
    c.drawString(margin, height - margin - 20, "ภาพประกอบ:")
    y_top = height - margin - 60
    center_x = width / 2
    img_width = 8 * cm

    for idx, url in enumerate(image_urls):
        label = image_labels[idx] if idx < len(image_labels) else f"ภาพที่ {idx + 1}"

        if y_top - (img_width * 4 / 3) < 3 * cm:
            c.showPage()
            draw_header()
            c.setFont("THSarabunNew", 18)
            c.drawString(margin, height - margin - 20, "ภาพประกอบ (ต่อ):")
            y_top = height - margin - 60

        c.setFont("THSarabunNew", 16)
        c.drawCentredString(center_x, y_top, label)
        y_top -= 20

        y_top = draw_image(c, url, center_x, y_top, img_width)

    c.setFont("THSarabunNew", 18)
    c.setFillColor(gray)
    c.line(1.5 * cm, 3.5 * cm, width - 1.5 * cm, 3.5 * cm)
    c.setFillColor(black)
    c.drawString(2 * cm, 1 * cm, "📞 SAS Service: 081-9216225")
    c.drawRightString(width - 2 * cm, 1 * cm, "📞 SAS Sales: 081-9216225 คุณสมยศ")

    c.save()
    buffer.seek(0)
    return buffer