from reportlab.lib.utils import ImageReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.colors import red, black, gray
import io
import requests
from PIL import Image, ExifTags

# Register Thai font
pdfmetrics.registerFont(TTFont('THSarabunNew', 'static/fonts/THSarabunNew.ttf'))

# Paths to static assets
sas_logo_path  = 'static/logos_sas.png'
qc_passed_path = 'static/qc_passed.png'


def draw_header(c, width, height):
    """Draw the SAS logo in the top‑right corner."""
    try:
        logo = Image.open(sas_logo_path)
        buf = io.BytesIO()
        logo.save(buf, format='PNG')
        buf.seek(0)
        logo_w = 3 * cm
        x = width - logo_w - 1.5 * cm
        y = height - 3 * cm
        c.drawImage(
            ImageReader(buf),
            x, y,
            width=logo_w,
            preserveAspectRatio=True,
            mask='auto'
        )
    except Exception as e:
        print("Logo load error:", e, flush=True)


def draw_image(c, image_url, center_x, y_top, max_width):
    """
    Draw a photo (correctly oriented + scaled + centered),
    then overlay a small “QC Passed” sticker on its bottom‑right.
    Returns the new y_top after drawing.
    """
    BOTTOM_MARGIN = 3 * cm

    try:
        # 1) Load and correct orientation
        img_data = requests.get(image_url, timeout=5).content
        img = Image.open(io.BytesIO(img_data))
        try:
            exif = img._getexif() or {}
            tag = next(k for k, v in ExifTags.TAGS.items() if v == "Orientation")
            ori = exif.get(tag)
            if ori == 3:
                img = img.rotate(180, expand=True)
            elif ori == 6:
                img = img.rotate(270, expand=True)
            elif ori == 8:
                img = img.rotate(90, expand=True)
        except Exception:
            pass

        # 2) Scale to fit within max_width and available height
        ow, oh = img.size
        aspect = oh / ow
        img_w = max_width
        img_h = img_w * aspect
        avail_h = y_top - BOTTOM_MARGIN
        if img_h > avail_h:
            img_h = avail_h
            img_w = img_h / aspect

        x = center_x - img_w / 2
        y = y_top - img_h

        # 3) Draw main image
        mbuf = io.BytesIO()
        img.save(mbuf, format='PNG')
        mbuf.seek(0)
        c.drawImage(
            ImageReader(mbuf),
            x, y,
            width=img_w,
            height=img_h,
            mask='auto'
        )

        # 4) Overlay QC Passed sticker
        sticker = Image.open(qc_passed_path)
        sw, sh = sticker.size
        sticker_w = 2 * cm
        sticker_h = sticker_w * (sh / sw)
        pad = 0.2 * cm
        sx = x + img_w - sticker_w - pad
        sy = y + pad

        sbuf = io.BytesIO()
        sticker.save(sbuf, format='PNG')
        sbuf.seek(0)
        c.drawImage(
            ImageReader(sbuf),
            sx, sy,
            width=sticker_w,
            height=sticker_h,
            mask='auto'
        )

        return y - 10

    except Exception as e:
        print("Error loading image:", e, flush=True)
        return y_top - 10


def create_qc_pdf(data, image_urls=None, image_labels=None):
    """
    Builds a two‑page QC report PDF:
     - Page 1: header + textual QC data
     - Page 2: header + photos overlaid with QC Passed sticker
    Returns an io.BytesIO buffer.
    """
    image_urls = image_urls or []
    image_labels = image_labels or []

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 2 * cm
    line = height - margin

    def draw_text(txt, bold=False, color=None):
        nonlocal line
        c.setFont('THSarabunNew', 16)
        c.setFillColor(color or black)
        c.drawString(margin, line, txt)
        line -= 22

    # --- Page 1: Textual QC data ---
    draw_header(c, width, height)
    c.setFont('THSarabunNew', 22)
    c.drawCentredString(width / 2, line, 'รายงานการตรวจสอบ QC มอเตอร์เกียร์')
    line -= 40

    draw_text(f"Serial Number: {data.get('serial','-')}")
    draw_text(f"วันที่ตรวจสอบ: {data.get('date','-')}")
    draw_text(f"ประเภทสินค้า: {data.get('product_type','-')}")
    draw_text(f"Nameplate: {data.get('motor_nameplate','-')}")

    ptype = data.get('product_type','').lower()
    is_servo = 'servo' in ptype
    is_acdc  = 'ac/dc' in ptype or 'bldc' in ptype

    if is_servo:
        draw_text('**ไม่ประกอบสินค้า', color=red)
        draw_text('**ไม่เติมน้ำมันเกียร์', color=red)
    draw_text('')  # spacer

    if data.get('motor_current'):
        draw_text(f"ค่ากระแสมอเตอร์: {data['motor_current']} A")
    if data.get('gear_ratio'):
        draw_text(f"อัตราทดเกียร์: {data['gear_ratio']}")
    if data.get('gear_sound'):
        draw_text(f"เสียงเกียร์: {data['gear_sound']} dB")

    if not (is_servo or is_acdc):
        draw_text(f"น้ำมันเกียร์ (ล): {data.get('oil_liters','-') or '-'}")
        draw_text(f"สถานะเติมน้ำมัน: {data.get('oil_filled','-')}")
    elif is_acdc:
        draw_text('*ไม่ต้องเติมน้ำมันเกียร์', color=red)

    draw_text(f"ระยะเวลารับประกัน: {data.get('warranty','-')} เดือน", color=red)
    draw_text(f"ผู้ตรวจสอบ: {data.get('inspector','-')}")

    if is_servo:
        draw_text('')
        draw_text('**การรับประกันสินค้า 18 เดือน', color=red)

    # Footer
    c.setFillColor(gray)
    c.line(1.5*cm, 3.5*cm, width-1.5*cm, 3.5*cm)
    c.setFont('THSarabunNew', 18)
    c.setFillColor(black)
    c.drawString(2*cm, 1*cm, '📞 SAS Service: 081-9216225')
    c.drawRightString(width-2*cm, 1*cm, '📞 SAS Sales: 081-9216225 คุณสมยศ')

    c.showPage()

    # --- Page 2: Images + QC Passed sticker ---
    draw_header(c, width, height)
    c.setFont('THSarabunNew', 18)
    c.drawString(margin, height - margin - 20, 'ภาพประกอบ:')
    y_top    = height - margin - 60
    center_x = width / 2
    max_w    = 8 * cm

    for i, url in enumerate(image_urls):
        label = image_labels[i] if i < len(image_labels) else f'ภาพที่ {i+1}'
        if y_top - max_w * 1.2 < 3 * cm:
            c.showPage()
            draw_header(c, width, height)
            c.setFont('THSarabunNew', 18)
            c.drawString(margin, height - margin - 20, 'ภาพประกอบ (ต่อ):')
            y_top = height - margin - 60

        c.setFont('THSarabunNew', 16)
        c.drawCentredString(center_x, y_top, label)
        y_top -= 20

        y_top = draw_image(c, url, center_x, y_top, max_w)

    # Final footer on last page
    c.setFillColor(gray)
    c.line(1.5*cm, 3.5*cm, width-1.5*cm, 3.5*cm)
    c.setFillColor(black)
    c.drawString(2*cm, 1*cm, '📞 SAS Service: 081-9216225')
    c.drawRightString(width-2*cm, 1*cm, '📞 SAS Sales: 081-9216225 คุณสมยศ')

    c.save()
    buffer.seek(0)
    return buffer