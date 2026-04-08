from reportlab.lib.utils import ImageReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.colors import red, black, gray
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import io
import os
import requests
from PIL import Image, ExifTags

# ✅ NEW: for warranty end-date calculation
from datetime import datetime, date, timedelta

# Register Thai font
# ✅ แก้เป็น (absolute path)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
font_path = os.path.join(BASE_DIR, 'static', 'fonts', 'THSarabunNew.ttf')
pdfmetrics.registerFont(TTFont('THSarabunNew', font_path))

# Paths to static assets (เดิม)
sas_logo_path  = 'static/logo_sas.png'
qc_passed_path = 'static/qc_passed.png'
SESSION = requests.Session()

_QC_STICKER_READER = None
_QC_STICKER_SIZE = None

def _get_qc_sticker_cached():
    """
    Load qc_passed.png once -> return (ImageReader, (w,h))
    """
    global _QC_STICKER_READER, _QC_STICKER_SIZE
    if _QC_STICKER_READER is None:
        sticker = Image.open(qc_passed_path).convert("RGBA")
        sw, sh = sticker.size
        buf = io.BytesIO()
        sticker.save(buf, format="PNG")
        buf.seek(0)
        _QC_STICKER_READER = ImageReader(buf)
        _QC_STICKER_SIZE = (sw, sh)
    return _QC_STICKER_READER, _QC_STICKER_SIZE

def _fetch_image_bytes(url: str) -> bytes:
    r = SESSION.get(url, timeout=8)
    r.raise_for_status()
    return r.content

# ✅ NEW: Inspector mapping (ID -> ชื่อ) เผื่อข้อมูลเก่าเป็น ID
INSPECTOR_MAP = {
    "QC001": "คุณสมประสงค์",
    "QC002": "คุณเกียรติศักดิ์",
    "QC999": "คุณโชติธนินท์",
}


def _resolve_sas_logo_path():
    """
    ✅ NEW: หาไฟล์โลโก้ให้เจอแบบชัวร์ โดยอิงตำแหน่งไฟล์ generate_pdf.py
    """
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    candidates = [
        os.path.join(base_dir, "static", "logo_sas.png"),
        os.path.join(base_dir, "static", "logo_sas.PNG"),
        os.path.join(base_dir, "static", "logos_sas.png"),
        os.path.join(base_dir, "static", "logos_sas.PNG"),
        os.path.join(base_dir, "logo_sas.png"),
        os.path.join(base_dir, "logo_sas.PNG"),
    ]

    for p in candidates:
        if os.path.exists(p):
            return p

    if os.path.exists(sas_logo_path):
        return sas_logo_path

    return None


def _parse_th_date(s: str):
    """
    ✅ NEW: Parse date from 'dd/mm/yyyy' or ISO 'yyyy-mm-dd'
    Return datetime.date or None
    """
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def _add_months(d: date, months: int) -> date:
    """
    ✅ NEW: add months without external libs (relativedelta)
    """
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1

    if m == 12:
        next_month = date(y + 1, 1, 1)
    else:
        next_month = date(y, m + 1, 1)
    last_day = next_month - timedelta(days=1)

    day = min(d.day, last_day.day)
    return date(y, m, day)


def _format_th_date(d: date) -> str:
    """
    ✅ NEW: format dd/mm/yyyy
    """
    return d.strftime("%d/%m/%Y")


def _compute_warranty_end_date(data: dict):
    """
    ✅ NEW: เงื่อนไขสิ้นสุดการรับประกัน
    - ใช้วันที่ทำเอกสาร (data['date']) ถ้าไม่มี ใช้วันนี้
    - + 7 วัน = วันเริ่มนับ
    - + N เดือน (18/24/36) = วันสิ้นสุด
    """
    w_raw = data.get('warranty')
    months = None

    try:
        if w_raw is None:
            months = None
        else:
            w_str = str(w_raw)
            digits = ''.join(ch for ch in w_str if ch.isdigit())
            months = int(digits) if digits else None
    except Exception:
        months = None

    if months not in (18, 24, 36):
        return None

    doc_date = _parse_th_date(data.get('date')) or date.today()
    start_date = doc_date + timedelta(days=7)
    end_date = _add_months(start_date, months)
    return end_date


def draw_header(c, width, height):
    """Draw the SAS logo in the top-right corner."""
    try:
        logo_path = _resolve_sas_logo_path()
        if not logo_path:
            raise FileNotFoundError("SAS logo not found (static/logo_sas.png).")

        logo = Image.open(logo_path).convert("RGBA")
        lw, lh = logo.size

        # ✅ ปรับเล็กลง + ขยับขึ้น
        logo_w = 2.5 * cm
        logo_h = logo_w * (lh / lw)

        buf = io.BytesIO()
        logo.save(buf, format="PNG")
        buf.seek(0)

        margin_top = 1.0 * cm
        margin_right = 1.5 * cm
        x = width - logo_w - margin_right
        y = height - margin_top - logo_h

        c.drawImage(
            ImageReader(buf),
            x, y,
            width=logo_w,
            height=logo_h,
            mask='auto'
        )
    except Exception as e:
        print("Logo load error:", e, flush=True)


def draw_image(c, image_url, center_x, y_top, max_width):
    """
    Draw a photo (correctly oriented + scaled + centered),
    then overlay a small “QC Passed” sticker on its bottom-right.
    Returns the new y_top after drawing.
    """
    BOTTOM_MARGIN = 3 * cm

    try:
        # 1) Load and correct orientation (✅ faster: reuse session)
        img_data = SESSION.get(image_url, timeout=5).content
        img = Image.open(io.BytesIO(img_data))

        # EXIF orientation
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

        # ✅ NEW: resize before embedding (faster + smaller PDF)
        img.thumbnail((1600, 1600))

        # 2) Scale to fit within max_width and available height
        ow, oh = img.size
        aspect = oh / ow if ow else 1
        img_w = max_width
        img_h = img_w * aspect

        avail_h = y_top - BOTTOM_MARGIN
        if img_h > avail_h:
            img_h = avail_h
            img_w = img_h / aspect if aspect else img_w

        x = center_x - img_w / 2
        y = y_top - img_h

        # 3) Draw main image
        mbuf = io.BytesIO()
        img.save(mbuf, format="PNG")
        mbuf.seek(0)
        c.drawImage(
            ImageReader(mbuf),
            x, y,
            width=img_w,
            height=img_h,
            mask="auto"
        )

        # 4) ✅ CHANGED: Overlay QC Passed sticker (cached)
        sticker_reader, (sw, sh) = _get_qc_sticker_cached()
        sticker_w = 2 * cm
        sticker_h = sticker_w * (sh / sw) if sw else sticker_w
        pad = 0.2 * cm
        sx = x + img_w - sticker_w - pad
        sy = y + pad

        c.drawImage(
            sticker_reader,
            sx, sy,
            width=sticker_w,
            height=sticker_h,
            mask="auto"
        )

        return y - 10

    except Exception as e:
        print("Error loading image:", e, flush=True)
        return y_top - 10

def draw_image_bytes(c, img_bytes, center_x, y_top, max_width):
    BOTTOM_MARGIN = 3 * cm
    try:
        img = Image.open(io.BytesIO(img_bytes))

        # EXIF orientation
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

        # ลดขนาดก่อนฝัง (ปรับได้ 1200/1600)
        img.thumbnail((1100, 1100))

        ow, oh = img.size
        aspect = oh / ow if ow else 1
        img_w = max_width
        img_h = img_w * aspect

        avail_h = y_top - BOTTOM_MARGIN
        if img_h > avail_h:
            img_h = avail_h
            img_w = img_h / aspect if aspect else img_w

        x = center_x - img_w / 2
        y = y_top - img_h

        # ✅ เร็วขึ้นอีก: ฝังเป็น JPEG (เร็ว+ไฟล์เล็กกว่า PNG)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        mbuf = io.BytesIO()
        img.save(mbuf, format="JPEG", quality=75, optimize=True)
        mbuf.seek(0)

        c.drawImage(ImageReader(mbuf), x, y, width=img_w, height=img_h)

        # sticker cached
        sticker_reader, (sw, sh) = _get_qc_sticker_cached()
        sticker_w = 2 * cm
        sticker_h = sticker_w * (sh / sw) if sw else sticker_w
        pad = 0.2 * cm
        sx = x + img_w - sticker_w - pad
        sy = y + pad
        c.drawImage(sticker_reader, sx, sy, width=sticker_w, height=sticker_h, mask="auto")

        return y - 10
    except Exception as e:
        print("Error draw_image_bytes:", e, flush=True)
        return y_top - 10

def _infer_image_label(url: str, fallback: str = "ภาพประกอบ"):
    """
    เดาจาก keyword ใน URL ให้ชื่อรูปตรงกับหัวข้อการตรวจสอบ
    (ถ้า caller ไม่ส่ง image_labels มา)
    """
    if not url:
        return fallback
    u = str(url).lower()

    mapping = [
        # ✅ RFKS nameplate images
        ("rfks_nameplate_motor", "Name plate : Motor"),
        ("rfks_nameplate_gear", "Name plate : Gear"),
        ("nameplate_motor", "Name plate : Motor"),
        ("nameplate_gear", "Name plate : Gear"),

        ("current", "ภาพค่ากระแส"),
        ("amp", "ภาพค่ากระแส"),
        ("nameplate", "ภาพ Nameplate"),
        ("motor", "ภาพมอเตอร์"),
        ("gear", "ภาพเกียร์"),
        ("sound", "ภาพเสียงเกียร์"),
        ("noise", "ภาพเสียงเกียร์"),
        ("install", "ภาพประกอบหน้างาน"),
        ("site", "ภาพประกอบหน้างาน"),
        ("controller", "ภาพ Controller"),

        ("servo_motor", "ภาพ Servo Motor"),
        ("servo_drive", "ภาพ Servo Drive"),
        ("cable", "ภาพ Cable Wire"),
        ("wire", "ภาพ Cable Wire"),
    ]

    for key, label in mapping:
        if key in u:
            return label
    return fallback


def create_qc_pdf(data, image_urls=None, image_labels=None):
    """
    Builds a two-page QC report PDF:
     - Page 1: header + textual QC data
     - Page 2: header + photos overlaid with QC Passed sticker
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

    # --- Page 1: Textual QC data ---
    draw_header(c, width, height)
    c.setFont('THSarabunNew', 22)
    c.drawCentredString(width / 2, line, 'รายงานการตรวจเช็คสินค้า QC Report')
    line -= 40

    draw_text(f"Serial Number: {data.get('serial','-')}")
    raw_date = data.get('date', '-') or '-'
    d = _parse_th_date(raw_date)  # ใช้ฟังก์ชัน parse ที่มีอยู่แล้ว
    date_str = _format_th_date(d) if d else str(raw_date)
    draw_text(f"วันที่ตรวจสอบ: {date_str}")
    draw_text(f"OR No.: {data.get('or_no','-') or '-'}")
    draw_text(f"ชื่อบริษัท: {data.get('company_name','-') or '-'}")
    draw_text(f"ประเภทสินค้า: {data.get('product_type','-')}")
    draw_text(f"Model : {data.get('motor_nameplate','-')}")

    ptype = data.get('product_type', '').lower()
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

    # ✅ FIXED: Indentation ต้องตรงทุกบรรทัดใน block นี้
    if not (is_servo or is_acdc):
        draw_text(f"ชนิดของน้ำมันเกียร์: {data.get('oil_type','-') or '-'}")
        draw_text(f"จำนวนน้ำมันเกียร์ (ลิตร): {data.get('oil_liters','-') or '-'}")
        draw_text(f"สถานะการเติมน้ำมัน: {data.get('oil_filled','-')}")
    elif is_acdc:
        draw_text('*ไม่ต้องเติมน้ำมันเกียร์', color=red)

    draw_text(f"ระยะเวลารับประกัน: {data.get('warranty','-')} เดือน", color=red)

    end_date = _compute_warranty_end_date(data)
    if end_date:
        draw_text(f"สิ้นสุดการรับประกัน: {_format_th_date(end_date)}", color=red)

    inspector_value = data.get('inspector', '-') or '-'
    inspector_name = INSPECTOR_MAP.get(inspector_value, inspector_value)
    draw_text(f"ผู้ตรวจสอบ: {inspector_name}")

    if is_servo:
        draw_text('')
        draw_text('**การรับประกันสินค้า 18 เดือน', color=red)

    # Footer
    c.setFillColor(gray)
    c.line(1.5*cm, 3.5*cm, width-1.5*cm, 3.5*cm)
    c.setFont('THSarabunNew', 18)
    c.setFillColor(black)
    c.drawString(2*cm, 1*cm, '📞 SAS Service: 096-2815161')
    c.drawRightString(width-2*cm, 1*cm, '📞 SAS Sales: 081-9216225 คุณโชติธนินท์')

    c.showPage()

    # --- Page 2: Images + QC Passed sticker ---
    draw_header(c, width, height)
    c.setFont('THSarabunNew', 18)
    c.drawString(margin, height - margin - 20, 'ภาพประกอบ:')
    y_top    = height - margin - 60
    center_x = width / 2

    # ✅ รูปใหญ่ขึ้น ~2 เท่า แต่ไม่ล้นหน้า
    max_w = min(16 * cm, width - (2 * margin))

    fetched = [None] * len(image_urls)
    with ThreadPoolExecutor(max_workers=5) as ex:
        future_map = {ex.submit(_fetch_image_bytes, u): idx for idx, u in enumerate(image_urls)}
    for fut in as_completed(future_map):
        idx = future_map[fut]
        try:
            fetched[idx] = fut.result()
        except Exception as e:
            print("Fetch image failed:", image_urls[idx], e, flush=True)
            fetched[idx] = None

    for i, url in enumerate(image_urls):
        if i < len(image_labels) and image_labels[i]:
            label = image_labels[i]
        else:
            label = _infer_image_label(url, fallback=f'ภาพที่ {i+1}')

        if y_top - max_w * 0.9 < 3 * cm:
            c.showPage()
            draw_header(c, width, height)
            c.setFont('THSarabunNew', 18)
            c.drawString(margin, height - margin - 20, 'ภาพประกอบ (ต่อ):')
            y_top = height - margin - 60

        c.setFont('THSarabunNew', 16)
        c.drawCentredString(center_x, y_top, label)
        y_top -= 20

        if fetched[i]:
            y_top = draw_image_bytes(c, fetched[i], center_x, y_top, max_w)
        else:
            y_top -= 10

    # Final footer on last page
    c.setFillColor(gray)
    c.line(1.5*cm, 2.5*cm, width-1.5*cm, 2.5*cm)
    c.setFillColor(black)
    c.drawString(2*cm, 1*cm, '📞 SAS Service: 096-2815161')
    c.drawRightString(width-2*cm, 1*cm, '📞 SAS Sales: 081-9216225 คุณโชติธนินท์')

    c.save()
    buffer.seek(0)
    return buffer