from reportlab.lib.utils import ImageReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.colors import Color, HexColor, white, black
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import os
import requests
from PIL import Image, ExifTags
from datetime import datetime, date, timedelta

# ──────────────────────────────────────────────
# DESIGN TOKENS  (Navy Blue theme)
# ──────────────────────────────────────────────
NAVY        = HexColor("#1A3A6B")   # แถบหัว / section bar
NAVY_LIGHT  = HexColor("#2A5298")   # gradient ที่สอง / accent
ACCENT      = HexColor("#E8F0FB")   # พื้นหลัง row สลับ
GOLD        = HexColor("#C8A951")   # เส้น divider / highlight
RED_WARN    = HexColor("#C0392B")   # ข้อความเตือน
GRAY_LINE   = HexColor("#CBD5E1")   # เส้นบาง
WHITE       = white
BLACK       = HexColor("#1E293B")

# ──────────────────────────────────────────────
# FONT REGISTRATION
# ──────────────────────────────────────────────
BASE_DIR   = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
font_path  = os.path.join(BASE_DIR, "static", "fonts", "THSarabunNew.ttf")
pdfmetrics.registerFont(TTFont("THSarabunNew", font_path))

sas_logo_path  = os.path.join(BASE_DIR, "static", "logo_sas.png")
qc_passed_path = os.path.join(BASE_DIR, "static", "qc_passed.png")

SESSION = requests.Session()

_QC_STICKER_READER = None
_QC_STICKER_SIZE   = None

INSPECTOR_MAP = {
    "QC001": "คุณสมนา",
    "QC002": "คุณเกียรติศักดิ์",
    "QC003": "คุณมัด",
    "QC999": "คุณโชติธนินท์",
}

# ── Signature image mapping (inspector ID -> filename in static/Sign/)
SIGNATURE_MAP = {
    "QC001": "001.png",
    "QC002": "002.png",
    # รองรับชื่อภาษาไทยด้วย (กรณี app.py เก็บ inspector_name แทน id)
    "คุณสมนา": "001.png",
    "คุณเกียรติศักดิ์": "002.png",
}

# ──────────────────────────────────────────────
# HELPER: QC sticker cache
# ──────────────────────────────────────────────
def _get_qc_sticker_cached():
    global _QC_STICKER_READER, _QC_STICKER_SIZE
    if _QC_STICKER_READER is None:
        sticker = Image.open(qc_passed_path).convert("RGBA")
        sw, sh  = sticker.size
        buf     = io.BytesIO()
        sticker.save(buf, format="PNG")
        buf.seek(0)
        _QC_STICKER_READER = ImageReader(buf)
        _QC_STICKER_SIZE   = (sw, sh)
    return _QC_STICKER_READER, _QC_STICKER_SIZE


def _fetch_image_bytes(url: str) -> bytes:
    r = SESSION.get(url, timeout=8)
    r.raise_for_status()
    return r.content


def _resolve_logo():
    for candidate in [
        os.path.join(BASE_DIR, "static", "logo_sas.png"),
        os.path.join(BASE_DIR, "static", "logo_sas.PNG"),
        os.path.join(BASE_DIR, "static", "logos_sas.png"),
        sas_logo_path,
    ]:
        if os.path.exists(candidate):
            return candidate
    return None


# ──────────────────────────────────────────────
# DATE UTILITIES
# ──────────────────────────────────────────────
def _parse_th_date(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def _add_months(d, months):
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    next_month = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    last_day   = next_month - timedelta(days=1)
    return date(y, m, min(d.day, last_day.day))


def _format_th_date(d):
    return d.strftime("%d/%m/%Y")


def _compute_warranty_end_date(data):
    w_raw = data.get("warranty")
    try:
        digits = "".join(ch for ch in str(w_raw) if ch.isdigit())
        months = int(digits) if digits else None
    except Exception:
        months = None
    if months not in (18, 24, 36):
        return None
    doc_date   = _parse_th_date(data.get("date")) or date.today()
    start_date = doc_date + timedelta(days=7)
    return _add_months(start_date, months)


# ──────────────────────────────────────────────
# DESIGN COMPONENTS
# ──────────────────────────────────────────────
def draw_header_bar(c, width, height):
    """แถบหัวกระดาษสีน้ำเงินครามพร้อมโลโก้"""
    bar_h = 2.8 * cm

    # แถบพื้นหลังน้ำเงินคราม
    c.setFillColor(NAVY)
    c.rect(0, height - bar_h, width, bar_h, fill=1, stroke=0)

    # เส้น gold บาง ๆ ด้านล่างแถบ
    c.setStrokeColor(GOLD)
    c.setLineWidth(2)
    c.line(0, height - bar_h, width, height - bar_h)

    # ชื่อบริษัท (ขาว)
    c.setFillColor(WHITE)
    c.setFont("THSarabunNew", 20)
    c.drawString(1.8 * cm, height - 1.35 * cm, "SYNERGY ASIA SOLUTION CO., LTD.")
    c.setFont("THSarabunNew", 13)
    c.drawString(1.8 * cm, height - 2.0 * cm, "www.synergy-as.com  |  www.motorsas.com")

    # โลโก้ขวา
    try:
        logo_path = _resolve_logo()
        if logo_path:
            logo   = Image.open(logo_path).convert("RGBA")
            lw, lh = logo.size
            logo_w = 2.8 * cm
            logo_h = logo_w * (lh / lw)
            buf    = io.BytesIO()
            logo.save(buf, format="PNG")
            buf.seek(0)
            c.drawImage(
                ImageReader(buf),
                width - logo_w - 1.2 * cm,
                height - bar_h + (bar_h - logo_h) / 2,
                width=logo_w, height=logo_h, mask="auto",
            )
    except Exception as e:
        print("Logo error:", e, flush=True)

    c.setLineWidth(0.5)


def draw_section_bar(c, y, width, label, margin):
    """แถบสีน้ำเงินเข้มสำหรับหัวข้อ section"""
    bar_h = 0.65 * cm
    c.setFillColor(NAVY_LIGHT)
    c.roundRect(margin, y - bar_h + 4, width - 2 * margin, bar_h, 3, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("THSarabunNew", 15)
    c.drawString(margin + 0.4 * cm, y - bar_h + 10, label)
    return y - bar_h - 4


def draw_field_row(c, y, width, margin, label, value, shade=False, warn=False):
    """แถวข้อมูล label: value พร้อม shading สลับ"""
    row_h = 0.68 * cm
    if shade:
        c.setFillColor(ACCENT)
        c.rect(margin, y - row_h + 4, width - 2 * margin, row_h, fill=1, stroke=0)

    c.setFillColor(HexColor("#475569"))
    c.setFont("THSarabunNew", 14)
    c.drawString(margin + 0.3 * cm, y - row_h + 14, label)

    c.setFillColor(RED_WARN if warn else BLACK)
    c.setFont("THSarabunNew", 15)
    c.drawString(margin + 6.5 * cm, y - row_h + 14, str(value) if value else "-")

    # เส้นแบ่งบาง
    c.setStrokeColor(GRAY_LINE)
    c.setLineWidth(0.3)
    c.line(margin, y - row_h + 4, width - margin, y - row_h + 4)
    c.setLineWidth(0.5)
    return y - row_h


def draw_footer(c, width, page_num=None):
    """Footer สีน้ำเงินเรียบ"""
    footer_h = 1.4 * cm

    # เส้น gold
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.5)
    c.line(0, footer_h, width, footer_h)

    c.setFillColor(NAVY)
    c.rect(0, 0, width, footer_h, fill=1, stroke=0)

    c.setFillColor(WHITE)
    c.setFont("THSarabunNew", 13)
    c.drawString(1.5 * cm, 0.45 * cm, "SAS Service: 099-8527166")
    c.drawCentredString(width / 2, 0.45 * cm, "SAS QC Gear Motor Report")
    if page_num:
        c.drawRightString(width - 1.5 * cm, 0.45 * cm, f"Page {page_num}")
    else:
        c.drawRightString(width - 1.5 * cm, 0.45 * cm, "SAS Sales: 081-9216225")

    c.setLineWidth(0.5)


def draw_image_label_bar(c, y, width, margin, label):
    """แถบชื่อรูปสีน้ำเงินอ่อน"""
    bar_h = 0.6 * cm
    c.setFillColor(ACCENT)
    c.roundRect(margin, y - bar_h + 2, width - 2 * margin, bar_h, 3, fill=1, stroke=0)
    c.setStrokeColor(NAVY_LIGHT)
    c.setLineWidth(0.8)
    c.roundRect(margin, y - bar_h + 2, width - 2 * margin, bar_h, 3, fill=0, stroke=1)
    c.setFillColor(NAVY)
    c.setFont("THSarabunNew", 15)
    c.drawCentredString(width / 2, y - bar_h + 9, label)
    c.setLineWidth(0.5)
    return y - bar_h - 6


# ──────────────────────────────────────────────
# IMAGE DRAWING
# ──────────────────────────────────────────────
def draw_image_bytes(c, img_bytes, center_x, y_top, max_width):
    BOTTOM_MARGIN = 3.5 * cm
    try:
        img = Image.open(io.BytesIO(img_bytes))
        try:
            exif = img._getexif() or {}
            tag  = next(k for k, v in ExifTags.TAGS.items() if v == "Orientation")
            ori  = exif.get(tag)
            if ori == 3:   img = img.rotate(180, expand=True)
            elif ori == 6: img = img.rotate(270, expand=True)
            elif ori == 8: img = img.rotate(90, expand=True)
        except Exception:
            pass

        img.thumbnail((1100, 1100))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        ow, oh    = img.size
        aspect    = oh / ow if ow else 1
        img_w     = max_width
        img_h     = img_w * aspect
        avail_h   = y_top - BOTTOM_MARGIN
        if img_h > avail_h:
            img_h = avail_h
            img_w = img_h / aspect if aspect else img_w

        x = center_x - img_w / 2
        y = y_top - img_h

        # เส้นกรอบรูป
        c.setStrokeColor(GRAY_LINE)
        c.setLineWidth(0.8)
        c.rect(x - 2, y - 2, img_w + 4, img_h + 4, fill=0, stroke=1)

        mbuf = io.BytesIO()
        img.save(mbuf, format="JPEG", quality=78, optimize=True)
        mbuf.seek(0)
        c.drawImage(ImageReader(mbuf), x, y, width=img_w, height=img_h)

        # QC Passed sticker
        try:
            sticker_reader, (sw, sh) = _get_qc_sticker_cached()
            sticker_w = 2.2 * cm
            sticker_h = sticker_w * (sh / sw) if sw else sticker_w
            pad = 0.25 * cm
            c.drawImage(
                sticker_reader,
                x + img_w - sticker_w - pad, y + pad,
                width=sticker_w, height=sticker_h, mask="auto",
            )
        except Exception:
            pass

        c.setLineWidth(0.5)
        return y - 14

    except Exception as e:
        print("draw_image_bytes error:", e, flush=True)
        return y_top - 14


def _infer_image_label(url, fallback="ภาพประกอบ"):
    if not url:
        return fallback
    u = str(url).lower()
    mapping = [
        ("rfks_nameplate_motor", "Name plate : Motor"),
        ("rfks_nameplate_gear",  "Name plate : Gear"),
        ("nameplate_motor",      "Name plate : Motor"),
        ("nameplate_gear",       "Name plate : Gear"),
        ("current",              "ภาพค่ากระแส"),
        ("amp",                  "ภาพค่ากระแส"),
        ("nameplate",            "ภาพ Nameplate"),
        ("motor",                "ภาพมอเตอร์"),
        ("gear",                 "ภาพเกียร์"),
        ("sound",                "ภาพเสียงเกียร์"),
        ("noise",                "ภาพเสียงเกียร์"),
        ("install",              "ภาพประกอบหน้างาน"),
        ("assembly",             "ภาพประกอบหน้างาน"),
        ("site",                 "ภาพประกอบหน้างาน"),
        ("controller",           "ภาพ Controller"),
        ("servo_motor",          "ภาพ Servo Motor"),
        ("servo_drive",          "ภาพ Servo Drive"),
        ("cable",                "ภาพ Cable Wire"),
        ("wire",                 "ภาพ Cable Wire"),
    ]
    for key, label in mapping:
        if key in u:
            return label
    return fallback


# ──────────────────────────────────────────────
# MAIN PDF BUILDER
# ──────────────────────────────────────────────
def create_qc_pdf(data, image_urls=None, image_labels=None):
    image_urls   = image_urls   or []
    image_labels = image_labels or []

    buffer = io.BytesIO()
    c      = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 1.8 * cm
    HEADER_H = 2.8 * cm
    FOOTER_H = 1.4 * cm
    content_top = height - HEADER_H - 0.5 * cm

    ptype    = data.get("product_type", "").lower()
    is_servo = "servo" in ptype
    is_acdc  = "ac/dc" in ptype or "bldc" in ptype

    # ════════════════════════════════════════
    # PAGE 1 — QC Data
    # ════════════════════════════════════════
    draw_header_bar(c, width, height)

    # ── ชื่อเอกสาร
    c.setFillColor(NAVY)
    c.setFont("THSarabunNew", 22)
    c.drawCentredString(width / 2, content_top, "รายงานการตรวจเช็คสินค้า  QC Report")

    # เส้นใต้ชื่อ
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.5)
    c.line(margin, content_top - 6, width - margin, content_top - 6)
    c.setLineWidth(0.5)

    y = content_top - 18

    # ── SECTION: ข้อมูลเอกสาร ──────────────
    y = draw_section_bar(c, y, width, "ข้อมูลเอกสาร", margin)
    y -= 4

    raw_date = data.get("date", "-") or "-"
    d        = _parse_th_date(raw_date)
    date_str = _format_th_date(d) if d else str(raw_date)

    rows_info = [
        ("Serial Number",  data.get("serial", "-")),
        ("วันที่ตรวจสอบ", date_str),
        ("OR No.",         data.get("or_no", "-") or "-"),
        ("ชื่อบริษัท",     data.get("company_name", "-") or "-"),
        ("ประเภทสินค้า",   data.get("product_type", "-")),
        ("Model",          data.get("motor_nameplate", "-")),
    ]
    for i, (lbl, val) in enumerate(rows_info):
        y = draw_field_row(c, y, width, margin, lbl, val, shade=(i % 2 == 0))

    y -= 10

    # ── SECTION: ผลการตรวจสอบ ──────────────
    y = draw_section_bar(c, y, width, "ผลการตรวจสอบ", margin)
    y -= 4

    if is_servo:
        for warn_txt in ["** ไม่ประกอบสินค้า", "** ไม่เติมน้ำมันเกียร์"]:
            y = draw_field_row(c, y, width, margin, "", warn_txt, shade=False, warn=True)

    check_rows = []
    if data.get("motor_current"):
        check_rows.append(("ค่ากระแสมอเตอร์", f"{data['motor_current']} A"))
    if data.get("gear_ratio"):
        check_rows.append(("อัตราทดเกียร์", data["gear_ratio"]))
    if data.get("gear_sound"):
        check_rows.append(("เสียงเกียร์", f"{data['gear_sound']} dB"))

    if not (is_servo or is_acdc):
        check_rows.append(("ชนิดของน้ำมันเกียร์", data.get("oil_type") or "-"))
        check_rows.append(("จำนวนน้ำมันเกียร์ (ลิตร)", data.get("oil_liters") or "-"))
        check_rows.append(("สถานะการเติมน้ำมัน", data.get("oil_filled") or "-"))
    elif is_acdc:
        check_rows.append(("หมายเหตุ", "* ไม่ต้องเติมน้ำมันเกียร์"))

    for i, (lbl, val) in enumerate(check_rows):
        y = draw_field_row(c, y, width, margin, lbl, val, shade=(i % 2 == 0))

    y -= 10

    # ── SECTION: การรับประกัน ───────────────
    y = draw_section_bar(c, y, width, "การรับประกัน", margin)
    y -= 4

    warranty_val = f"{data.get('warranty', '-')} เดือน"
    y = draw_field_row(c, y, width, margin, "ระยะเวลารับประกัน", warranty_val, shade=False, warn=True)

    end_date = _compute_warranty_end_date(data)
    if end_date:
        y = draw_field_row(c, y, width, margin, "สิ้นสุดการรับประกัน",
                           _format_th_date(end_date), shade=True, warn=True)

    if is_servo:
        y = draw_field_row(c, y, width, margin, "หมายเหตุ",
                           "** การรับประกันสินค้า 18 เดือน", shade=False, warn=True)

    y -= 10

    # ── SECTION: ผู้ตรวจสอบ ────────────────
    y = draw_section_bar(c, y, width, "ผู้ตรวจสอบ", margin)
    y -= 4

    inspector_value = data.get("inspector", "-") or "-"
    inspector_name  = INSPECTOR_MAP.get(inspector_value, inspector_value)
    y = draw_field_row(c, y, width, margin, "ผู้ตรวจสอบ", inspector_name, shade=False)

    # กล่องลายเซ็น
    y -= 20
    sig_w, sig_h = 6 * cm, 2.2 * cm
    sig_x = margin
    c.setStrokeColor(GRAY_LINE)
    c.setLineWidth(0.5)
    c.rect(sig_x, y - sig_h, sig_w, sig_h, fill=0, stroke=1)

    # วาดลายเซ็นจริง (ถ้ามี) — ลองหลาย path เพื่อรองรับทั้ง local และ Render
    sig_filename = SIGNATURE_MAP.get(inspector_value)
    sig_drawn = False
    if sig_filename:
        # รายการ path ที่เป็นไปได้ทั้งหมด
        candidate_paths = [
            os.path.join(BASE_DIR, "static", "Sign", sig_filename),
            os.path.join(os.path.dirname(__file__), "..", "static", "Sign", sig_filename),
            os.path.join("/opt/render/project/src", "static", "Sign", sig_filename),
            os.path.join(os.getcwd(), "static", "Sign", sig_filename),
        ]
        sig_path = None
        for p in candidate_paths:
            p = os.path.normpath(p)
            print(f"[SIG] checking: {p} -> exists={os.path.exists(p)}", flush=True)
            if os.path.exists(p):
                sig_path = p
                break

        if sig_path:
            try:
                sig_img = Image.open(sig_path).convert("RGBA")
                sig_buf = io.BytesIO()
                sig_img.save(sig_buf, format="PNG")
                sig_buf.seek(0)
                pad = 0.15 * cm
                draw_w = sig_w - pad * 2
                draw_h = sig_h - pad * 2
                iw, ih = sig_img.size
                ratio = min(draw_w / iw, draw_h / ih)
                rw, rh = iw * ratio, ih * ratio
                rx = sig_x + (sig_w - rw) / 2
                ry = y - sig_h + (sig_h - rh) / 2
                c.drawImage(ImageReader(sig_buf), rx, ry, width=rw, height=rh, mask="auto")
                sig_drawn = True
                print(f"[SIG] ✅ drawn from {sig_path}", flush=True)
            except Exception as e:
                print(f"[SIG] ❌ draw error: {e}", flush=True)
        else:
            print(f"[SIG] ❌ ไม่พบไฟล์ลายเซ็น {sig_filename} ในทุก path", flush=True)

    if not sig_drawn:
        c.setFillColor(HexColor("#94A3B8"))
        c.setFont("THSarabunNew", 12)
        c.drawCentredString(sig_x + sig_w / 2, y - sig_h + 8, "ลายเซ็นผู้ตรวจสอบ")

    draw_footer(c, width, page_num=1)
    c.showPage()

    # ════════════════════════════════════════
    # PAGE 2+ — Images
    # ════════════════════════════════════════
    if image_urls:
        # โหลดรูปทั้งหมดแบบ parallel
        fetched = [None] * len(image_urls)
        with ThreadPoolExecutor(max_workers=5) as ex:
            future_map = {ex.submit(_fetch_image_bytes, u): idx
                          for idx, u in enumerate(image_urls)}
        for fut in as_completed(future_map):
            idx = future_map[fut]
            try:
                fetched[idx] = fut.result()
            except Exception as e:
                print("Fetch image failed:", image_urls[idx], e, flush=True)

        draw_header_bar(c, width, height)
        c.setFillColor(NAVY)
        c.setFont("THSarabunNew", 20)
        c.drawCentredString(width / 2, content_top, "ภาพประกอบการตรวจสอบ")
        c.setStrokeColor(GOLD)
        c.setLineWidth(1.5)
        c.line(margin, content_top - 6, width - margin, content_top - 6)
        c.setLineWidth(0.5)

        y_top    = content_top - 22
        center_x = width / 2
        max_w    = min(15.5 * cm, width - 2 * margin)
        page_num = 2

        for i, url in enumerate(image_urls):
            label = (image_labels[i] if i < len(image_labels) and image_labels[i]
                     else _infer_image_label(url, fallback=f"ภาพที่ {i+1}"))

            needed_h = max_w * 0.85 + 1.2 * cm
            if y_top - needed_h < FOOTER_H + 0.5 * cm:
                draw_footer(c, width, page_num=page_num)
                c.showPage()
                page_num += 1
                draw_header_bar(c, width, height)
                c.setFillColor(NAVY)
                c.setFont("THSarabunNew", 20)
                c.drawCentredString(width / 2, content_top, "ภาพประกอบการตรวจสอบ (ต่อ)")
                c.setStrokeColor(GOLD)
                c.setLineWidth(1.5)
                c.line(margin, content_top - 6, width - margin, content_top - 6)
                c.setLineWidth(0.5)
                y_top = content_top - 22

            y_top = draw_image_label_bar(c, y_top, width, margin, label)
            if fetched[i]:
                y_top = draw_image_bytes(c, fetched[i], center_x, y_top, max_w)
            else:
                c.setFillColor(HexColor("#94A3B8"))
                c.setFont("THSarabunNew", 14)
                c.drawCentredString(center_x, y_top - 0.8 * cm, "ไม่พบรูปภาพ")
                y_top -= 1.5 * cm

            y_top -= 12

        draw_footer(c, width, page_num=page_num)

    c.save()
    buffer.seek(0)
    return buffer
