from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.lib.colors import red
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
import io
import requests
from PIL import Image

pdfmetrics.registerFont(TTFont('THSarabun', 'static/fonts/THSarabun.ttf'))

def draw_image(c, image_url, x, y, width):
    try:
        img_data = requests.get(image_url).content
        img = Image.open(io.BytesIO(img_data))
        aspect = img.height / img.width
        height = width * aspect
        img_io = io.BytesIO()
        img.save(img_io, format='PNG')
        img_io.seek(0)
        c.drawImage(ImageReader(img_io), x, y - height, width, height)
    except Exception as e:
        print(f"Error loading image {image_url}: {e}")


def create_qc_pdf(data, image_urls=[]):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 2 * cm
    line = height - margin

    def draw_text(text, bold=False, color=None):
        nonlocal line
        if bold:
            c.setFont("Helvetica-Bold", 12)
        else:
            c.setFont("Helvetica", 12)
        if color:
            c.setFillColor(color)
        else:
            c.setFillColorRGB(0, 0, 0)
        c.drawString(margin, line, text)
        line -= 18

    # Title
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width / 2, line, "รายงานการตรวจสอบ QC มอเตอร์เกียร์")
    line -= 30

    # ข้อมูลทั่วไป
    draw_text(f"Serial Number: {data.get('serial', '-')}", bold=True)
    draw_text(f"วันที่ตรวจสอบ: {data.get('date', '-')}")
    draw_text(f"ประเภทสินค้า: {data.get('product_type', '-')}")
    draw_text(f"ชื่อมอเตอร์: {data.get('motor_nameplate', '-')}")

    # เงื่อนไขการแสดงผล
    product_type = data.get("product_type", "").lower()
    is_acdc_or_bldc = "ac/dc" in product_type or "bldc" in product_type
    is_servo = "servo" in product_type
    is_other = not is_acdc_or_bldc and not is_servo

    # เฉพาะ Servo
    if is_servo:
        draw_text("**ไม่ประกอบสินค้า", bold=True, color=red)
        draw_text("**ไม่เติมน้ำมันเกียร์", bold=True, color=red)

    draw_text("")

    # รายละเอียดการตรวจสอบ
    if data.get("motor_current"):
        draw_text(f"ค่ากระแสมอเตอร์: {data['motor_current']} A")
    if data.get("gear_ratio"):
        draw_text(f"อัตราทดเกียร์: {data['gear_ratio']}")
    if data.get("gear_sound"):
        draw_text(f"เสียงเกียร์: {data['gear_sound']} dB")

    # เงื่อนไขเติมน้ำมัน
    if not is_acdc_or_bldc and not is_servo:
        draw_text(f"น้ำมันเกียร์ (ลิตร): {data.get('oil_liters', '-') or '-'} ลิตร")
        draw_text(f"สถานะการเติมน้ำมัน: {data.get('oil_filled', '-')}")
    elif is_acdc_or_bldc:
        draw_text("*ไม่ต้องเติมน้ำมันเกียร์", bold=True, color=red)

    # Warranty
    draw_text(f"ระยะเวลารับประกัน: {data.get('warranty', '-')} เดือน", bold=True, color=red)

    # Inspector
    draw_text(f"ผู้ตรวจสอบ: {data.get('inspector', '-')}")

    # หมายเหตุอื่น ๆ
    if is_servo:
        draw_text("")
        draw_text("**การรับประกันสินค้า 18 เดือน", bold=True, color=red)

    # รูปภาพ
    line -= 20
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin, line, "ภาพประกอบ:")
    line -= 20

    img_x = margin
    img_y = line
    img_width = 6 * cm

    for url in image_urls:
        if line < 5 * cm:
            c.showPage()
            line = height - margin
            img_y = line
        draw_image(c, url, img_x, img_y, img_width)
        img_x += img_width + 1 * cm
        if img_x + img_width > width - margin:
            img_x = margin
            img_y -= 6 * cm


    # === Footer ===
        c.showPage()
    c.setFont("THSarabun", 18)
    c.drawString(2 * cm, 1 * cm, "📞 SAS Service: 081-9216225")
    c.drawRightString(width - 2 * cm, 1 * cm, "📞 SAS Sales: 081-9216225 คุณสมยศ")
    c.save()
    buffer.seek(0)
    return buffer

# รองรับรูปภาพด้วย ImageReader
from reportlab.lib.utils import ImageReader