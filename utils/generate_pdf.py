import os
import io
import requests
from reportlab.lib.utils import ImageReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.colors import red, black, gray
from PIL import Image, ExifTags

# ─── ตั้งค่า BASE_DIR และ paths ───────────────────────────────────
BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR      = os.path.join(BASE_DIR, 'static')
sas_logo_path   = os.path.join(STATIC_DIR, 'logos_sas.png')
qc_passed_path  = os.path.join(STATIC_DIR, 'qc_passed.png')
font_path       = os.path.join(STATIC_DIR, 'fonts', 'THSarabunNew.ttf')

# ─── ลงทะเบียนฟอนต์ภาษาไทย ────────────────────────────────────────
pdfmetrics.registerFont(TTFont('THSarabunNew', font_path))


def draw_header(c, width, height):
    """
    วาดโลโก้ SAS ขนาดเล็กลง (2cm wide) ที่มุมบนขวา
    เรียกได้บนทุกหน้า
    """
    try:
        logo = Image.open(sas_logo_path)
        buf = io.BytesIO()
        logo.save(buf, format='PNG')
        buf.seek(0)

        logo_w = 2 * cm
        # วัดตำแหน่ง: ให้ห่างขอบขวา 1cm, ห่างขอบบน 1cm
        x = width - logo_w - 1 * cm
        y = height - logo_w - 1 * cm

        c.drawImage(
            ImageReader(buf),
            x, y,
            width=logo_w,
            preserveAspectRatio=True,
            mask='auto'
        )
    except Exception as e:
        print(f"[ERROR] draw_header: {e}", flush=True)


def draw_image(c, image_url, center_x, y_top, max_width):
    """
    วาดภาพ QC พร้อมหมุนตาม EXIF, scale, centered,
    และ overlay สติ๊กเกอร์ QC Passed มุมล่างขวา
    """
    BOTTOM_MARGIN = 3 * cm

    try:
        # โหลดและแก้ orientation
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

        # scale ให้พอดี
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

        # วาดภาพหลัก
        mbuf = io.BytesIO()
        img.save(mbuf, format='PNG')
        mbuf.seek(0)
        c.drawImage(ImageReader(mbuf), x, y, width=img_w, height=img_h, mask='auto')

        # overlay QC Passed
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
        c.drawImage(ImageReader(sbuf), sx, sy, width=sticker_w, height=sticker_h, mask='auto')

        return y - 10

    except Exception as e:
        print(f"[ERROR] draw_image: {e}", flush=True)
        return y_top - 10


def create_qc_pdf(data, image_urls=None, image_labels=None):
    """
    สร้าง PDF QC report สองหน้า
    หน้า 1: ข้อความ QC + header
    หน้า 2+: รูปภาพ QC + sticker + header ทุกหน้า
    """
    image_urls   = image_urls or []
    image_labels = image_labels or []

    buf = io.BytesIO()
    c   = canvas.Canvas(buf, pagesize=A4)
    w, h   = A4
    margin = 2 * cm
    line   = h - margin

    def draw_text(txt, color=black):
        nonlocal line
        c.setFont('THSarabunNew', 16)
        c.setFillColor(color)
        c.drawString(margin, line, txt)
        line -= 22

    # ─── Page 1: ข้อความ QC ──────────────────────────────────────
    draw_header(c, w, h)
    c.setFont('THSarabunNew', 22)
    c.drawCentredString(w / 2, line, 'รายงานการตรวจสอบ QC มอเตอร์เกียร์')
    line -= 40

    draw_text(f"Serial Number: {data.get('serial','-')}")
    draw_text(f"วันที่ตรวจสอบ: {data.get('date','-')}")
    draw_text(f"ประเภทสินค้า: {data.get('product_type','-')}")
    draw_text(f"Nameplate: {data.get('motor_nameplate','-')}")

    ptype    = data.get('product_type','').lower()
    is_servo = 'servo' in ptype
    is_acdc  = 'ac/dc' in ptype or 'bldc' in ptype

    if is_servo:
        draw_text('**ไม่ประกอบสินค้า', color=red)
        draw_text('**ไม่เติมน้ำมันเกียร์', color=red)
    draw_text('')

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

    # footer หน้า 1
    c.setFillColor(gray)
    c.line(1.5*cm, 3.5*cm, w-1.5*cm, 3.5*cm)
    c.setFont('THSarabunNew', 18)
    c.setFillColor(black)
    c.drawString(2*cm, 1*cm, '📞 SAS Service: 081-9216225')
    c.drawRightString(w-2*cm, 1*cm, '📞 SAS Sales: 081-9216225 คุณสมยศ')

    c.showPage()

    # ─── Page 2+: รูปภาพ QC ───────────────────────────────────────
    # วาด header ใหม่บนทุกหน้า
    draw_header(c, w, h)
    c.setFont('THSarabunNew', 18)
    c.drawString(margin, h - margin - 20, 'ภาพประกอบ:')
    y_top    = h - margin - 60
    cx       = w / 2
    max_w    = 8 * cm

    for idx, url in enumerate(image_urls):
        label = image_labels[idx] if idx < len(image_labels) else f'ภาพที่ {idx+1}'
        if y_top - max_w * 1.2 < 3*cm:
            c.showPage()
            draw_header(c, w, h)
            c.setFont('THSarabunNew', 18)
            c.drawString(margin, h - margin - 20, 'ภาพประกอบ (ต่อ):')
            y_top = h - margin - 60

        c.setFont('THSarabunNew', 16)
        c.drawCentredString(cx, y_top, label)
        y_top -= 20

        y_top = draw_image(c, url, cx, y_top, max_w)

    # footer หน้าสุดท้าย
    c.setFillColor(gray)
    c.line(1.5*cm, 3.5*cm, w-1.5*cm, 3.5*cm)
    c.setFillColor(black)
    c.drawString(2*cm, 1*cm, '📞 SAS Service: 081-9216225')
    c.drawRightString(w-2*cm, 1*cm, '📞 SAS Sales: 081-9216225 คุณสมยศ')

    c.save()
    buf.seek(0)
    return buf