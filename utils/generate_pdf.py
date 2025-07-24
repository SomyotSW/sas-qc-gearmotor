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
sas_logo_path   = 'static/sas_logo.png'
qc_passed_path  = 'static/qc_passed.png'  # small “QC Passed” sticker

def draw_image(c, image_url, center_x, y_top, max_width):
    """
    Downloads the image, fixes EXIF orientation, scales it to fit,
    draws it centered at (center_x, y_top), then overlays a small
    qc_passed sticker at its bottom-right corner.
    Returns the new y_top after drawing.
    """
    BOTTOM_MARGIN = 3 * cm  # for footer

    try:
        # --- Load main image ---
        img_data = requests.get(image_url, timeout=5).content
        img = Image.open(io.BytesIO(img_data))

        # Fix EXIF orientation
        try:
            exif = img._getexif() or {}
            orient_tag = next(k for k, v in ExifTags.TAGS.items() if v == "Orientation")
            orientation = exif.get(orient_tag)
            if orientation == 3:
                img = img.rotate(180, expand=True)
            elif orientation == 6:
                img = img.rotate(270, expand=True)
            elif orientation == 8:
                img = img.rotate(90, expand=True)
        except Exception:
            pass

        # Compute scaled size
        orig_w, orig_h = img.size
        aspect = orig_h / orig_w
        img_w = max_width
        img_h = img_w * aspect
        avail_h = y_top - BOTTOM_MARGIN
        if img_h > avail_h:
            img_h = avail_h
            img_w = img_h / aspect

        x = center_x - img_w / 2
        y = y_top - img_h

        # Draw main image
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        c.drawImage(ImageReader(buf), x, y, width=img_w, height=img_h)

        # --- Overlay QC Passed sticker ---
        # Load sticker once
        sticker = Image.open(qc_passed_path)
        ow, oh = sticker.size
        sticker_w = 2 * cm
        sticker_h = sticker_w * (oh / ow)
        # position: bottom-right corner with small padding
        pad = 0.2 * cm
        sx = x + img_w - sticker_w - pad
        sy = y + pad

        # draw sticker
        stick_buf = io.BytesIO()
        sticker.save(stick_buf, format='PNG')
        stick_buf.seek(0)
        c.drawImage(ImageReader(stick_buf), sx, sy, width=sticker_w, height=sticker_h, mask='auto')

        return y - 10

    except Exception as e:
        print(f"Error loading image {image_url}: {e}", flush=True)
        return y_top - 10


def create_qc_pdf(data, image_urls=None, image_labels=None):
    """
    Builds a two‑page QC report PDF with:
      • Page 1: textual QC details
      • Page 2: photos each overlaid with QC Passed sticker
    Returns a BytesIO buffer.
    """
    image_urls   = image_urls or []
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

    def draw_header():
        lw = 3 * cm
        c.drawImage(sas_logo_path,
                    width - lw - 1.5 * cm,
                    height - 3 * cm,
                    width=lw,
                    preserveAspectRatio=True)

    # --- Page 1 ---
    draw_header()
    c.setFont('THSarabunNew', 22)
    c.drawCentredString(width/2, line, 'รายงานการตรวจสอบ QC มอเตอร์เกียร์')
    line -= 40

    draw_text(f"Serial Number: {data.get('serial','-')}")
    draw_text(f"วันที่ตรวจสอบ: {data.get('date','-')}")
    draw_text(f"ประเภทสินค้า: {data.get('product_type','-')}")
    draw_text(f"Nameplate: {data.get('motor_nameplate','-')}")

    p = data.get('product_type','').lower()
    is_servo = 'servo' in p
    is_acdc  = 'ac/dc' in p or 'bldc' in p

    if is_servo:
        draw_text('**ไม่ประกอบสินค้า', bold=True, color=red)
        draw_text('**ไม่เติมน้ำมันเกียร์', bold=True, color=red)
    draw_text('')  # spacer

    if data.get('motor_current'): draw_text(f"ค่ากระแสมอเตอร์: {data['motor_current']} A")
    if data.get('gear_ratio'):    draw_text(f"อัตราทดเกียร์: {data['gear_ratio']}")
    if data.get('gear_sound'):    draw_text(f"เสียงเกียร์: {data['gear_sound']} dB")

    if not (is_servo or is_acdc):
        draw_text(f"น้ำมันเกียร์ (ล): {data.get('oil_liters','-') or '-'}")
        draw_text(f"สถานะเติมน้ำมัน: {data.get('oil_filled','-')}")
    elif is_acdc:
        draw_text('*ไม่ต้องเติมน้ำมันเกียร์', bold=True, color=red)

    draw_text(f"ระยะเวลารับประกัน: {data.get('warranty','-')} เดือน", bold=True, color=red)
    draw_text(f"ผู้ตรวจสอบ: {data.get('inspector','-')}")

    if is_servo:
        draw_text('')
        draw_text('**การรับประกันสินค้า 18 เดือน', bold=True, color=red)

    # footer
    c.setFillColor(gray)
    c.line(1.5*cm, 3.5*cm, width-1.5*cm, 3.5*cm)
    c.setFont('THSarabunNew', 18)
    c.setFillColor(black)
    c.drawString(2*cm, 1*cm, '📞 SAS Service: 081-9216225')
    c.drawRightString(width-2*cm, 1*cm, '📞 SAS Sales: 081-9216225 คุณสมยศ')

    c.showPage()

    # --- Page 2: Images ---
    draw_header()
    c.setFont('THSarabunNew', 18)
    c.drawString(margin, height - margin - 20, 'ภาพประกอบ:')
    y_top   = height - margin - 60
    center_x = width / 2
    max_w    = 8 * cm

    for i, url in enumerate(image_urls):
        label = image_labels[i] if i < len(image_labels) else f'ภาพที่ {i+1}'
        if y_top - max_w*1.2 < 3*cm:
            c.showPage()
            draw_header()
            c.setFont('THSarabunNew', 18)
            c.drawString(margin, height - margin - 20, 'ภาพประกอบ (ต่อ):')
            y_top = height - margin - 60

        c.setFont('THSarabunNew', 16)
        c.drawCentredString(center_x, y_top, label)
        y_top -= 20

        y_top = draw_image(c, url, center_x, y_top, max_w)

    # final footer
    c.setFillColor(gray)
    c.line(1.5*cm, 3.5*cm, width-1.5*cm, 3.5*cm)
    c.setFillColor(black)
    c.drawString(2*cm, 1*cm, '📞 SAS Service: 081-9216225')
    c.drawRightString(width-2*cm, 1*cm, '📞 SAS Sales: 081-9216225 คุณสมยศ')

    c.save()
    buffer.seek(0)
    return buffer