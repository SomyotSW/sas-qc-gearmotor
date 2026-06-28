# utils/generate_motor_qc_job_pdf.py
# สร้างเอกสาร QC-Motor Precheck สำหรับ Admin Motor
import os
import io
import tempfile
import html
import re
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, KeepTogether
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
    if logo_path and os.path.exists(logo_path):
        try:
            return Image(logo_path, width=30*mm, height=12*mm)
        except Exception:
            return ''
    return ''


def _barcode_drawing(value):
    # Code128 เป็น Flowable อยู่แล้ว วางใน Table ได้โดยตรง
    return code128.Code128(str(value or ''), barHeight=11*mm, barWidth=0.36*mm, humanReadable=True)


def create_motor_qc_job_pdf(job, qr_image_stream, barcode_value='', logo_path=None):
    """
    job structure:
    {
      qr_no, company_name, product_type, item_count, items:[{no, model, assembly}],
      form_url, created_at
    }
    """
    stream = io.BytesIO()
    doc = SimpleDocTemplate(
        stream,
        pagesize=A4,
        rightMargin=10*mm,
        leftMargin=10*mm,
        topMargin=7*mm,
        bottomMargin=7*mm,
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
    title = ParagraphStyle('SASTitle', parent=base, fontSize=18, leading=20, alignment=TA_CENTER, textColor=colors.HexColor('#0b63ce'))
    subtitle = ParagraphStyle('SASSubTitle', parent=base, fontSize=10, leading=12, alignment=TA_CENTER, textColor=colors.HexColor('#334155'))
    section = ParagraphStyle('SASSection', parent=base, fontSize=12, leading=14, textColor=colors.white)
    head = ParagraphStyle('SASHead', parent=base, fontSize=10, leading=12, alignment=TA_CENTER, textColor=colors.white)
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
        colWidths=[34*mm, 110*mm, 31*mm],
    )
    header_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, 0), 'LEFT'),
        ('ALIGN', (2, 0), (2, 0), 'RIGHT'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 3*mm))

    info = [
        [_p('QR No.', cell_c), _p(job.get('qr_no', '-'), cell), _p('วันที่ออกเอกสาร', cell_c), _p(job.get('created_at', '-'), cell)],
        [_p('บริษัท', cell_c), _p(job.get('company_name', '-'), cell), _p('ประเภทสินค้า', cell_c), _p(job.get('product_type', '-'), cell)],
        [_p('จำนวนรายการ', cell_c), _p(str(job.get('item_count') or len(job.get('items', []))), cell), _p('ผู้จัดทำ', cell_c), _p(job.get('created_by', 'Admin Motor SAS04'), cell)],
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
    story.append(Spacer(1, 3*mm))

    barcode_tbl = Table(
        [[_p('BARCODE DATA', cell_c), _barcode_drawing(barcode_value), _p('ใช้สำหรับอ้างอิงเอกสาร / เปิดงาน QC', note)]],
        colWidths=[28*mm, 98*mm, 49*mm],
    )
    barcode_tbl.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0, 0), (0, 0), colors.HexColor('#eaf4ff')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 0), (1, 0), 'CENTER'),
    ]))
    story.append(barcode_tbl)
    story.append(Spacer(1, 3*mm))

    story.append(_section_bar('1) รายการสินค้า / Product Items', section))
    item_rows = [[_p('Item', head), _p('Model สินค้า', head), _p('สถานะงาน', head), _p('QC Result', head), _p('หมายเหตุ', head)]]
    for item in (job.get('items') or [])[:30]:
        item_rows.append([
            _p(str(item.get('no', '')), cell_c),
            _p(item.get('model', ''), cell),
            _p(item.get('assembly', ''), cell_c),
            _p('Pass / NG', cell_c),
            _p('', cell),
        ])
    item_tbl = Table(item_rows, colWidths=[14*mm, 82*mm, 25*mm, 28*mm, 26*mm], repeatRows=1)
    item_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0b63ce')),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#cbd5e1')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(item_tbl)
    story.append(Spacer(1, 3*mm))

    story.append(_section_bar('2) หัวข้อการตรวจ QC-Gearmotor / QC Checkpoints', section))
    checks = [
        ('1', 'ตรวจ Model / Nameplate / Serial / Power / Voltage / Ratio ให้ตรงกับเอกสารขาย'),
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
    check_rows = [[_p('No.', head), _p('หัวข้อที่ต้องตรวจ', head), _p('OK', head), _p('NG', head), _p('หมายเหตุ', head)]]
    for no, text in checks:
        check_rows.append([_p(no, cell_c), _p(text, cell), _p('', cell_c), _p('', cell_c), _p('', cell)])
    check_tbl = Table(check_rows, colWidths=[12*mm, 111*mm, 14*mm, 14*mm, 24*mm], repeatRows=1)
    check_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0b63ce')),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#cbd5e1')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(check_tbl)
    story.append(Spacer(1, 3*mm))

    sign_tbl = Table([
        [_p('Admin Motor SAS04', cell_c), _p('QC Inspector', cell_c), _p('Warehouse / Packing', cell_c)],
        ['', '', ''],
        [_p('วันที่ / Date: ____________', small), _p('วันที่ / Date: ____________', small), _p('วันที่ / Date: ____________', small)],
    ], colWidths=[58*mm, 58*mm, 58*mm], rowHeights=[7*mm, 14*mm, 7*mm])
    sign_tbl.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eaf4ff')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(sign_tbl)
    story.append(Spacer(1, 3*mm))
    story.append(_p('หมายเหตุ: QR Code บนหัวเอกสารจะเปิดหน้า QC Form และเติมข้อมูล QR No., บริษัท, ประเภทสินค้า และ Model ให้อัตโนมัติ จากนั้น QC เลือก Item ที่ต้องตรวจแล้วกรอกผลทดสอบจริง', note))

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
