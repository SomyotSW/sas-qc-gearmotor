# utils/generate_motor_qc_job_pdf.py
# สร้างเอกสาร QC-Motor Precheck สำหรับ Admin Motor
import os
import io
import tempfile
import html
import re
import base64
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, KeepTogether, PageBreak
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.barcode import code128


def _register_thai_font():
    """พยายามหา TH Sarabun จากหลาย path; ถ้าไม่เจอ fallback เป็น Helvetica"""
    candidates = [
        os.path.join(os.getcwd(), 'THSarabunNew.ttf'),
        os.path.join(os.getcwd(), 'static', 'THSarabunNew.ttf'),
        os.path.join(os.getcwd(), 'static', 'fonts', 'THSarabunNew.ttf'),
        os.path.join(os.getcwd(), 'fonts', 'THSarabunNew.ttf'),
        '/usr/share/fonts/truetype/thai/THSarabunNew.ttf',
        '/usr/share/fonts/opentype/tlwg/Garuda.otf',
        '/usr/share/fonts/opentype/tlwg/Loma.otf',
        '/usr/share/fonts/opentype/tlwg/Sawasdee.otf',
        '/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont('SASFont', path))
                return 'SASFont'
            except Exception:
                pass
    return 'Helvetica'


FONT_NAME = _register_thai_font()


def _p(text, style):
    text = '' if text is None else str(text)
    safe = html.escape(text)

    # ฟอนต์ไทยบางตัวไม่ครอบคลุม Latin/ตัวเลขครบ จึงห่อช่วง ASCII ด้วย Helvetica
    # เพื่อให้ Model, QR No., Date, RFKS, QC-GEARMOTOR แสดงผลไม่หาย
    def repl(m):
        chunk = m.group(0)
        if not chunk.strip():
            return chunk
        return f'<font name="Helvetica">{chunk}</font>'

    safe = re.sub(r'[A-Za-z0-9_./:+|()\-]+', repl, safe)
    return Paragraph(safe, style)


def _qr_image(qr_image_stream, width=27*mm, height=27*mm):
    qr_image_stream.seek(0)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
    tmp.write(qr_image_stream.read())
    tmp.close()
    return Image(tmp.name, width=width, height=height)


def _logo_image(logo_path):
    """วาง Logo SAS แบบรักษาสัดส่วน ไม่บีบแบน และให้สูงขึ้นกว่า V2"""
    if logo_path and os.path.exists(logo_path):
        try:
            img = Image(logo_path)
            iw = float(getattr(img, 'imageWidth', 1) or 1)
            ih = float(getattr(img, 'imageHeight', 1) or 1)
            target_w = 40 * mm
            target_h = target_w * (ih / iw)
            max_h = 22 * mm
            if target_h > max_h:
                target_h = max_h
                target_w = target_h * (iw / ih)
            min_h = 15 * mm
            if target_h < min_h:
                target_h = min_h
                target_w = target_h * (iw / ih)
            img.drawWidth = target_w
            img.drawHeight = target_h
            return img
        except Exception:
            return ''
    return ''


def _barcode_drawing(value, max_width=170*mm):
    """สร้าง Code128 ให้เป็นแถบเดียวและย่อให้พอดีกับหน้ากระดาษ"""
    value = str(value or '').strip()
    if not value:
        value = '-'

    # เริ่มด้วย barWidth ปกติ แล้วคำนวณลดขนาดอัตโนมัติถ้า URL ยาว
    bw = 0.28 * mm
    bc = code128.Code128(value, barHeight=14*mm, barWidth=bw, humanReadable=False)
    try:
        if bc.width > max_width:
            bw = max(0.10 * mm, bw * (max_width / float(bc.width)))
            bc = code128.Code128(value, barHeight=14*mm, barWidth=bw, humanReadable=False)
    except Exception:
        pass
    return bc



def _signature_image_from_data_uri(data_uri, width=38*mm, height=16*mm):
    """แปลงลายเซ็น base64 จากหน้า Approve เป็น ReportLab Image"""
    data_uri = str(data_uri or '').strip()
    if not data_uri.startswith('data:image'):
        return None
    try:
        b64 = data_uri.split(',', 1)[1]
        raw = base64.b64decode(b64)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
        tmp.write(raw)
        tmp.close()
        img = Image(tmp.name)
        iw = float(getattr(img, 'imageWidth', 1) or 1)
        ih = float(getattr(img, 'imageHeight', 1) or 1)
        ratio = min(width / iw, height / ih)
        img.drawWidth = iw * ratio
        img.drawHeight = ih * ratio
        return img
    except Exception:
        return None


def _approval_cell(approval, style_small, is_admin=False):
    approval = approval or {}
    name = approval.get('name') or ('Admin SAS04' if is_admin else '')
    sig = _signature_image_from_data_uri(approval.get('signature_data'))
    if sig:
        return [sig, _p(name, style_small)]
    if approval or is_admin:
        return [_p(f'Approved by {name}', style_small)]
    return ''


def _approval_date(approval, style_small):
    approval = approval or {}
    approved_at = str(approval.get('approved_at') or '').strip()
    date_only = approved_at[:10] if approved_at else '____________'
    return _p(f'วันที่ / Date: {date_only}', style_small)


QC_CHECKPOINTS = [
    ('1', 'ตรวจจำนวนสินค้า / Model / Nameplate / Serial / Power / Voltage / Ratio ให้ตรงกับเอกสารขาย'),
    ('2', 'ตรวจสภาพภายนอก สี รอยกระแทก หน้าแปลน ขาแท่น เพลา Keyway และอุปกรณ์ประกอบ'),
    ('3', 'ตรวจการประกอบ Motor + Gear: Bolt, Coupling, Adapter, Alignment และความแน่นของจุดยึด'),
    ('4', 'ตรวจอัตราทดเกียร์ ทิศทางการหมุน และความผิดปกติของ Output Shaft'),
    ('5', 'Run Test แบบ No-load: เสียงผิดปกติ การสั่น อุณหภูมิ และการรั่วซึมของน้ำมัน'),
    ('6', 'วัดกระแสมอเตอร์และเทียบ Nameplate / ตรวจความสมดุล 3 เฟสเมื่อเกี่ยวข้อง'),
    ('7', 'ตรวจ Terminal Box, Wiring, Grounding, Cable, Plug และ Controller/Drive เมื่อเกี่ยวข้อง'),
    ('8', 'ตรวจชนิดน้ำมันเกียร์ ปริมาณน้ำมัน ระดับน้ำมัน และยืนยันว่าเติมแล้วสำหรับรุ่นที่ต้องเติม'),
    ('9', 'ตรวจรูปถ่ายหลักฐาน: Nameplate, กระแส, เสียง/Run Test และภาพประกอบก่อนแพ็กสินค้า'),
    ('10', 'ตรวจ Packing, Label, QR/Barcode, Warranty และเอกสารแนบก่อนส่งมอบ'),
]


def _qc_mark(job, checkpoint_no, target_status='OK'):
    """พิมพ์ X ลงช่อง OK หรือ NG จากข้อมูลที่ QC เลือกตอน Approve"""
    approvals = (job or {}).get('approvals') or {}
    qc = approvals.get('qc') or {}
    checklist = qc.get('qc_checklist') or {}
    key = str(checkpoint_no)
    target_status = str(target_status or '').upper()
    if isinstance(checklist, dict):
        val = checklist.get(key) or checklist.get(int(key))
        if isinstance(val, dict):
            return 'X' if str(val.get('status') or '').upper() == target_status else ''
        # รองรับข้อมูลเก่า V7 ที่เป็น True = OK
        if target_status == 'OK' and val:
            return 'X'
    if isinstance(checklist, list) and target_status == 'OK':
        return 'X' if key in [str(x) for x in checklist] else ''
    return ''


def _qc_note(job, checkpoint_no):
    approvals = (job or {}).get('approvals') or {}
    qc = approvals.get('qc') or {}
    checklist = qc.get('qc_checklist') or {}
    key = str(checkpoint_no)
    if isinstance(checklist, dict):
        val = checklist.get(key) or checklist.get(int(key))
        if isinstance(val, dict):
            return str(val.get('note') or '')
    return ''


def _department_qr_block(qc_qr_image_stream, warehouse_qr_image_stream, cell_c, small, note):
    """ก้อน QR 2 แผนก + หมายเหตุ ใช้ซ้ำได้ทั้งหน้าแรกหรือหน้าใหม่ด้านล่าง"""
    qr_cells = ['', '', '']
    if qc_qr_image_stream:
        qr_cells[1] = _qr_image(qc_qr_image_stream, width=21*mm, height=21*mm)
    if warehouse_qr_image_stream:
        qr_cells[2] = _qr_image(warehouse_qr_image_stream, width=21*mm, height=21*mm)

    dept_qr_tbl = Table([
        ['', _p('QC Inspector Approve QR', cell_c), _p('Warehouse Prepare QR', cell_c)],
        qr_cells,
        ['', _p('QC สแกนหลังตรวจสินค้าเสร็จ', small), _p('Warehouse สแกนหลังเตรียมสินค้าเสร็จ', small)],
    ], colWidths=[58*mm, 58*mm, 58*mm], rowHeights=[5*mm, 23*mm, 6*mm])
    dept_qr_tbl.setStyle(TableStyle([
        ('GRID', (1, 0), (-1, -1), 0.35, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (1, 0), (-1, 0), colors.HexColor('#eaf4ff')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
    ]))

    note_p = _p('หมายเหตุ: QR Code บนหัวเอกสารจะเปิดหน้า QC Form และเติมข้อมูล QR No., บริษัท และรายการสินค้าให้อัตโนมัติ โดย QC สามารถกรอกผลตรวจแยกตาม Item เช่น ประเภทสินค้า, Model, จำนวน, อัตราทด, กระแส, เสียง, น้ำมัน และรูปภาพของแต่ละรายการ', note)
    return [dept_qr_tbl, Spacer(1, 2*mm), note_p]

def create_motor_qc_job_pdf(job, qr_image_stream, barcode_value='', logo_path=None, qc_qr_image_stream=None, warehouse_qr_image_stream=None):
    """
    job structure:
    {
      qr_no, company_name, product_type, item_count, items:[{no, product_type, model, qty, assembly}],
      form_url, created_at
    }
    """
    stream = io.BytesIO()
    doc = SimpleDocTemplate(
        stream,
        pagesize=A4,
        rightMargin=8*mm,
        leftMargin=8*mm,
        topMargin=3.5*mm,
        bottomMargin=5*mm,
        title=f"QC Motor {job.get('qr_no','')}",
    )

    styles = getSampleStyleSheet()
    base = ParagraphStyle(
        'SASBase',
        parent=styles['Normal'],
        fontName=FONT_NAME,
        fontSize=11,
        leading=13,
        alignment=TA_LEFT,
    )
    small = ParagraphStyle('SASSmall', parent=base, fontSize=9, leading=10)
    title = ParagraphStyle('SASTitle', parent=base, fontSize=17, leading=19, alignment=TA_CENTER, textColor=colors.HexColor('#0b63ce'))
    subtitle = ParagraphStyle('SASSubTitle', parent=base, fontSize=9.5, leading=11, alignment=TA_CENTER, textColor=colors.HexColor('#334155'))
    section = ParagraphStyle('SASSection', parent=base, fontSize=12, leading=14, textColor=colors.white)
    head = ParagraphStyle('SASHead', parent=base, fontSize=9.5, leading=11, alignment=TA_CENTER, textColor=colors.white)
    cell = ParagraphStyle('SASCell', parent=base, fontSize=9.5, leading=11)
    cell_c = ParagraphStyle('SASCellC', parent=cell, alignment=TA_CENTER)
    note = ParagraphStyle('SASNote', parent=small, textColor=colors.HexColor('#475569'))

    story = []

    header_left = [
        _p('QC-GEARMOTOR PRE-CHECK DOCUMENT', title),
        _p('เอกสารแนบงานก่อนส่งสินค้า - สแกน QR เพื่อเปิดฟอร์ม QC พร้อมข้อมูลอัตโนมัติ', subtitle),
    ]
    header_tbl = Table(
        [[_logo_image(logo_path), header_left, _qr_image(qr_image_stream)]],
        colWidths=[43*mm, 101*mm, 31*mm],
    )
    header_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, 0), 'LEFT'),
        ('ALIGN', (2, 0), (2, 0), 'RIGHT'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 2*mm))

    info = [
        [_p('OR No.', cell_c), _p(job.get('qr_no', '-'), cell), _p('วันที่ออกเอกสาร', cell_c), _p(job.get('created_at', '-'), cell)],
        [_p('บริษัท', cell_c), _p(job.get('company_name', '-'), cell), _p('จำนวนรายการ', cell_c), _p(str(job.get('item_count') or len(job.get('items', []))), cell)],
        [_p('ประเภทสินค้า', cell_c), _p(job.get('product_type', 'หลายประเภทสินค้า'), cell), _p('ผู้จัดทำ', cell_c), _p(job.get('created_by', 'Admin Motor'), cell)],
    ]
    info_tbl = Table(info, colWidths=[28*mm, 72*mm, 28*mm, 47*mm])
    info_tbl.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#eaf4ff')),
        ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#eaf4ff')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(info_tbl)
    story.append(Spacer(1, 2*mm))

    # Barcode สำหรับยิง Scanner บน PC: ให้เป็นแถบเดียวเหมือน QR Code
    # โดยฝัง form_url ไม่ฝังข้อมูลทุกรายการสินค้า เพื่อไม่ให้ barcode ยาวจนล้นกระดาษ
    barcode_data = barcode_value or job.get('form_url') or job.get('qr_no', '')
    barcode_tbl = Table(
        [[_barcode_drawing(barcode_data)]],
        colWidths=[175*mm],
        rowHeights=[18*mm],
    )
    barcode_tbl.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#cbd5e1')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(barcode_tbl)
    story.append(_p('Barcode Scanner Data: เปิดฟอร์ม QC อัตโนมัติเหมือน QR Code', note))
    story.append(Spacer(1, 2*mm))

    story.append(_section_bar('1) รายการสินค้า / Product Items', section))
    item_rows = [[
        _p('Item', head), _p('ประเภทสินค้า', head), _p('Model สินค้า', head), _p('จำนวน', head),
        _p('สถานะงาน', head), _p('เตรียมจริง', head), _p('QC Result', head), _p('หมายเหตุ', head)
    ]]
    for item in (job.get('items') or [])[:30]:
        wh_qty = item.get('warehouse_prepared_qty')
        wh_note = item.get('warehouse_note') or ''
        item_rows.append([
            _p(str(item.get('no', '')), cell_c),
            _p(item.get('product_type') or job.get('product_type', ''), cell),
            _p(item.get('model', ''), cell),
            _p(str(item.get('qty', 1)), cell_c),
            _p(item.get('assembly', ''), cell_c),
            _p('' if wh_qty is None else str(wh_qty), cell_c),
            _p('Pass / NG', cell_c),
            _p(wh_note, cell),
        ])
    item_tbl = Table(item_rows, colWidths=[10*mm, 30*mm, 54*mm, 13*mm, 18*mm, 17*mm, 14*mm, 19*mm], repeatRows=1)
    item_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0b63ce')),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#cbd5e1')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(item_tbl)
    story.append(Spacer(1, 2*mm))

    story.append(_section_bar('2) หัวข้อการตรวจ QC-Gearmotor / QC Checkpoints', section))
    checks = QC_CHECKPOINTS
    check_rows = [[_p('No.', head), _p('หัวข้อที่ต้องตรวจ', head), _p('OK', head), _p('NG', head), _p('หมายเหตุ', head)]]
    for no, text in checks:
        ok_mark = _qc_mark(job, no, 'OK')
        ng_mark = _qc_mark(job, no, 'NG')
        check_note = _qc_note(job, no)
        check_rows.append([_p(no, cell_c), _p(text, cell), _p(ok_mark, cell_c), _p(ng_mark, cell_c), _p(check_note, cell)])
    check_tbl = Table(check_rows, colWidths=[12*mm, 111*mm, 14*mm, 14*mm, 24*mm], repeatRows=1)
    check_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0b63ce')),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#cbd5e1')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(check_tbl)
    story.append(Spacer(1, 2*mm))

    approvals = job.get('approvals') or {}
    admin_approval = approvals.get('admin') or {'name': job.get('created_by', 'Admin SAS04'), 'approved_at': job.get('created_at', '')}
    qc_approval = approvals.get('qc') or {}
    warehouse_approval = approvals.get('warehouse') or {}

    sign_tbl = Table([
        [_p('Admin Motor SAS04', cell_c), _p('QC Inspector', cell_c), _p('Warehouse / Packing', cell_c)],
        [
            _approval_cell(admin_approval, small, is_admin=True),
            _approval_cell(qc_approval, small),
            _approval_cell(warehouse_approval, small),
        ],
        [
            _approval_date(admin_approval, small),
            _approval_date(qc_approval, small),
            _approval_date(warehouse_approval, small),
        ],
    ], colWidths=[58*mm, 58*mm, 58*mm], rowHeights=[7*mm, 18*mm, 7*mm])
    sign_tbl.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eaf4ff')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 1), (-1, 1), 2),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 2),
    ]))
    story.append(sign_tbl)
    story.append(Spacer(1, 2*mm))

    # QR Code แยกตามแผนก ใช้สำหรับกด Approve หลังได้รับเอกสาร
    # ถ้ามีรายการ 4 รายการขึ้นไป ให้ย้ายก้อนนี้ไปไว้หน้าถัดไปชิดขอบล่าง เพื่อให้หน้าแรกไม่แน่นและดูมืออาชีพ
    dept_block = _department_qr_block(qc_qr_image_stream, warehouse_qr_image_stream, cell_c, small, note)
    item_count = int(job.get('item_count') or len(job.get('items') or []) or 0)
    if item_count >= 4:
        story.append(PageBreak())
        story.append(Spacer(1, 210*mm))
        story.extend(dept_block)
    else:
        story.extend(dept_block)

    doc.build(story)
    stream.seek(0)
    return stream


def _section_bar(text, section_style):
    tbl = Table([[_p(text, section_style)]], colWidths=[175*mm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#0b63ce')),
        ('BOX', (0, 0), (-1, -1), 0.0, colors.HexColor('#0b63ce')),
        ('LEFTPADDING', (0, 0), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    return KeepTogether([tbl, Spacer(1, 2*mm)])
