from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from io import BytesIO
from PIL import Image
import datetime
import requests

# Register Thai font
pdfmetrics.registerFont(TTFont('THSarabun', 'static/fonts/THSarabunNew.ttf'))

# Path to logo in project
LOGO_PATH = "static/logo_sas.png"

# === Map image to header ===
IMAGE_LABELS = {
    "motor_current_img": "ภาพการวัดค่ากระแส (Motor Current)",
    "gear_sound_img": "ภาพการวัดเสียงเกียร์ (Gear Sound)",
    "assembly_img": "ภาพประกอบหน้างาน (Assembly)"
}

def calculate_warranty_expiry(start_date_str, months):
    start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d")
    year = start_date.year + ((start_date.month + months - 1) // 12)
    month = (start_date.month + months - 1) % 12 + 1
    day = min(start_date.day, 28)
    return datetime.date(year, month, day)

def create_qc_pdf(data, image_urls):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # === Logo ===
    try:
        logo_img = Image.open(LOGO_PATH)
        logo_io = BytesIO()
        logo_img.save(logo_io, format='PNG')
        logo_io.seek(0)
        c.drawImage(ImageReader(logo_io), 1.5 * cm, height - 3 * cm, width=3 * cm, preserveAspectRatio=True)
    except Exception as e:
        print(f"Logo Error: {e}")

    # === Header ===
    c.setFont("THSarabun", 24)
    c.drawString(5 * cm, height - 2.5 * cm, "รายงานการตรวจสอบ QC มอเตอร์เกียร์ (SAS)")

    c.setFont("THSarabun", 20)
    c.drawString(2 * cm, height - 4.5 * cm, f"หมายเลขซีเรียล: {data.get('serial', '-')}")
    c.drawString(2 * cm, height - 5.2 * cm, f"ผู้ตรวจสอบ: {data.get('inspector', '-')}")
    c.drawString(2 * cm, height - 5.9 * cm, f"ประเภทสินค้า: {data.get('product_type', '-')}")
    c.drawString(2 * cm, height - 6.6 * cm, f"ชื่อมอเตอร์: {data.get('motor_nameplate', '-')}")
    c.drawString(2 * cm, height - 7.3 * cm, f"อัตราทดเกียร์: {data.get('gear_ratio', '-')}")
    c.drawString(2 * cm, height - 8.0 * cm, f"เสียงเกียร์: {data.get('gear_sound', '-')}")
    c.drawString(2 * cm, height - 8.7 * cm, f"ค่ากระแส: {data.get('motor_current', '-')}")
    c.drawString(2 * cm, height - 9.4 * cm, f"น้ำมันเกียร์: {data.get('oil_liters', '-')} ลิตร - {data.get('oil_filled', '-')}")
    c.drawString(2 * cm, height - 10.1 * cm, f"รับประกัน: {data.get('warranty', '-')} เดือน")

    today = datetime.date.today().strftime("%Y-%m-%d")
    c.drawString(2 * cm, height - 10.8 * cm, f"วันที่จัดทำ: {today}")

    # === Calculate Warranty Expiry ===
    warranty_months = int(data.get('warranty', '0'))
    start_date = data.get('date', today)
    expiry_date = calculate_warranty_expiry(start_date, warranty_months)
    c.drawString(2 * cm, height - 11.5 * cm, f"หมดระยะรับประกัน: {expiry_date.strftime('%Y-%m-%d')}")

    c.showPage()

    # === Images with labels ===
    for key, url in data.get("images", {}).items():
        if url:
            try:
                response = requests.get(url)
                img = Image.open(BytesIO(response.content))
                img.thumbnail((500, 500))
                img_io = BytesIO()
                img.save(img_io, format='PNG')
                img_io.seek(0)

                label = IMAGE_LABELS.get(key, "รูปภาพประกอบ")
                c.setFont("THSarabun", 22)
                c.drawString(2 * cm, height - 2.5 * cm, label)
                c.drawImage(ImageReader(img_io), 2 * cm, 4 * cm, width=15 * cm, preserveAspectRatio=True)
                c.showPage()
            except Exception as e:
                print(f"Error loading image: {e}")
                continue

    # === Footer ===
    c.setFont("THSarabun", 18)
    c.drawString(2 * cm, 1 * cm, "📞 SAS Service: 081-9216225")
    c.drawRightString(width - 2 * cm, 1 * cm, "📞 SAS Sales: 081-9216225 คุณสมยศ")

    c.save()
    buffer.seek(0)
    return buffer
