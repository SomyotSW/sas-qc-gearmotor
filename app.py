from flask import Flask, render_template, request, redirect, send_file, url_for, session, jsonify
from werkzeug.utils import secure_filename
import os
import firebase_admin
from firebase_admin import credentials, db, storage
import datetime
import io
from utils.generate_pdf import create_qc_pdf
from utils.qr_generator import generate_qr_code
import json
import qrcode
import threading
#import pandas as pd
from io import BytesIO
from openpyxl import load_workbook

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'
app.config['UPLOAD_FOLDER'] = 'uploads'

# ==== Load Firebase Credential from Environment ====
firebase_json = json.loads(os.environ.get("FIREBASE_CREDENTIAL_JSON"))
cred = credentials.Certificate(firebase_json)

firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://sas-qc-gearmotor-app-default-rtdb.asia-southeast1.firebasedatabase.app/',
    'storageBucket': 'sas-qc-gearmotor-app.firebasestorage.app'
})

ref = db.reference("/qc_data")
bucket = storage.bucket()

# =========================
# Stock on shelf (FAST + SIMPLE)
# =========================
STOCK_BLOB_NAME = "stock/Stock motor.xlsx"
STOCK_XLS_PATH = os.path.join(os.path.dirname(__file__), "Stock motor.xlsx")
STOCK_UPLOAD_PASS = "Adminsas2026"

CHECK_BLOB_NAME = "stock/Check status.xlsx"   # <-- คุณเปลี่ยนชื่อไฟล์ได้ตามจริง
CHECK_UPLOAD_PASS = "Adminsas2026"            # ใช้รหัสเดียวกับ stock ได้
_check_cache = {"mtime": None, "rows": []}
_check_lock = threading.Lock()

_stock_cache = {
    "mtime": None,
    "rows": []
}
_stock_lock = threading.Lock()


def _load_stock_rows_cached():
    """
    Read latest stock xlsx from Firebase Storage and cache by blob updated time.
    Range:
      Code: A10-A460
      Description: E10-E460
      Total: J10-J460
    """
    blob = bucket.blob(STOCK_BLOB_NAME)
    blob.reload()  # ดึง metadata ล่าสุด
    

    # ใช้เวลา updated เป็น key กันโหลดซ้ำ
    # (ถ้า updated เป็น None ให้ fallback)
    mtime = str(blob.updated) if blob.updated else str(blob.generation)

    with _stock_lock:
        if _stock_cache["mtime"] == mtime and _stock_cache["rows"] is not None:
            return _stock_cache["rows"]

        # โหลดไฟล์เป็น bytes แล้วอ่านด้วย openpyxl
        data = blob.download_as_bytes()
        wb = load_workbook(BytesIO(data), data_only=True, read_only=True)
        ws = wb.worksheets[0]

        rows = []
        # อ่านช่วง A..J เพื่อให้ดึง A/E/AF ได้ในรอบเดียว (A=1 ... J=10)
        for row in ws.iter_rows(min_row=9, max_row=460, min_col=1, max_col=10, values_only=True):
            code  = row[0]   # A
            desc  = row[4]   # E
            total = row[31]   # AF

            code_s = "" if code is None else str(code).strip()
            if code_s:
                rows.append({
                    "code": code_s,
                    "description": "" if desc is None else str(desc).strip(),
                    "total": "" if total is None else str(total).strip()
                })

        _stock_cache["mtime"] = mtime
        _stock_cache["rows"] = rows
        return rows
    
def _load_check_rows_cached():
    blob = bucket.blob(CHECK_BLOB_NAME)
    blob.reload()

    mtime = str(blob.updated) if blob.updated else str(blob.generation)

    with _check_lock:
        if _check_cache["mtime"] == mtime and _check_cache["rows"] is not None:
            return _check_cache["rows"]

        data = blob.download_as_bytes()
        wb = load_workbook(BytesIO(data), data_only=True, read_only=True)
        year_sheets = [s for s in wb.sheetnames if str(s).isdigit()]
        ws = wb[str(max(map(int, year_sheets)))] if year_sheets else wb.worksheets[0]

        rows = []

        # อ่าน A ถึง R เพื่อให้เข้าถึง N/Q/R ได้ในรอบเดียว (A=1 ... R=18)
        for row in ws.iter_rows(min_row=25, max_row=600, min_col=1, max_col=18, values_only=True):
        # index ใน tuple: A=0, D=3, E=4, F=5, G=6, H=7, N=13, Q=16, R=17
            no_item      = row[0]   # A
            po_no        = row[3]   # D
            po_open_date = row[4]   # E
            customer     = row[5]   # F
            stock_order  = row[6]   # G
            amount       = row[7]   # H
            transport    = row[13]  # N
            factory_eta  = row[16]  # Q
            delivery_eta = row[17]  # R

            if (no_item is None and po_no is None and po_open_date is None and customer is None
                and stock_order is None and amount is None and transport is None
                and factory_eta is None and delivery_eta is None):
                continue

            rows.append({
                "no_item": "" if no_item is None else str(no_item).strip(),
                "po_open_date": "" if po_open_date is None else str(po_open_date).strip(),
                "po": "" if po_no is None else str(po_no).strip(),
                "customer": "" if customer is None else str(customer).strip(),
                "stock_or_order": "" if stock_order is None else str(stock_order).strip(),
                "amount": "" if amount is None else str(amount).strip(),
                "transport": "" if transport is None else str(transport).strip(),
                "factory_eta": "" if factory_eta is None else str(factory_eta).strip(),
                "delivery_eta": "" if delivery_eta is None else str(delivery_eta).strip(),
            })

        _check_cache["mtime"] = mtime
        _check_cache["rows"] = rows
        return rows
    
@app.route("/stock-upload", methods=["GET", "POST"])
def stock_upload_public():
    if request.method == "GET":
        key = (request.args.get("key") or "").strip()
        if key != STOCK_UPLOAD_PASS:
            return "Unauthorized", 403

        # ✅ ผ่านรหัสแล้ว เก็บสิทธิ์ไว้ใน session เพื่อใช้ตอน POST
        session["stock_upload_ok"] = True
        return render_template("stock_upload_public.html")

    # ===== POST =====
    # ✅ ตรวจซ้ำฝั่ง server ก่อนอัปโหลด
    if not session.get("stock_upload_ok"):
        return "Unauthorized", 403

    f = request.files.get("file")
    if not f or f.filename.strip() == "":
        return "No file uploaded", 400

    if not f.filename.lower().endswith(".xlsx"):
        return "Invalid file type (ต้องเป็น .xlsx เท่านั้น)", 400

    blob = bucket.blob(STOCK_BLOB_NAME)
    blob.upload_from_file(
        f,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # ล้าง cache เพื่อให้หน้า stock ดึงไฟล์ใหม่ทันที
    with _stock_lock:
        _stock_cache["mtime"] = None
        _stock_cache["rows"] = []

    # ✅ ใช้เสร็จแล้ว ปิดสิทธิ์ (กันคนกด refresh แล้วยิง POST ซ้ำ)
    session.pop("stock_upload_ok", None)

    return redirect("/stock")

@app.route("/check-status-upload", methods=["GET", "POST"])
def check_status_upload_public():
    if request.method == "GET":
        key = (request.args.get("key") or "").strip()
        if key != CHECK_UPLOAD_PASS:
            return "Unauthorized", 403

        # ✅ ผ่านรหัสแล้ว เก็บสิทธิ์ไว้ใน session เพื่อใช้ตอน POST
        session["check_upload_ok"] = True
        return render_template("check_status_upload_public.html")

    # ===== POST =====
    # ✅ ตรวจซ้ำฝั่ง server ก่อนอัปโหลด
    if not session.get("check_upload_ok"):
        return "Unauthorized", 403

    f = request.files.get("file")
    if not f or f.filename.strip() == "":
        return "No file uploaded", 400

    if not f.filename.lower().endswith(".xlsx"):
        return "Invalid file type (ต้องเป็น .xlsx เท่านั้น)", 400

    blob = bucket.blob(CHECK_BLOB_NAME)
    blob.upload_from_file(
        f,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # ล้าง cache เพื่อให้หน้า check-status ดึงไฟล์ใหม่ทันที
    with _check_lock:
        _check_cache["mtime"] = None
        _check_cache["rows"] = []

    # ✅ ใช้เสร็จแล้ว ปิดสิทธิ์ (กันคนกด refresh แล้วยิง POST ซ้ำ)
    session.pop("check_upload_ok", None)

    return redirect("/check-status")
                
# ✅ NEW: Inspector mapping (ID -> ชื่อ)
INSPECTOR_MAP = {
    "QC001": "คุณสมประสงค์",
    "QC002": "คุณเกียรติศักดิ์",
    "QC003": "คุณมัด",
    "QC999": "คุณโชติธนินท์",
}


@app.route('/')
def home():
    return render_template('index.html')

@app.route('/stock')
def stock_page():
    # หน้า UI Stock on shelf
    return render_template('stock.html')

@app.route('/api/stock')
def stock_api():
    # API ส่งข้อมูล stock เป็น JSON (โหลดจาก cache เพื่อให้เร็ว)
    try:
        rows = _load_stock_rows_cached()
        return jsonify({"ok": True, "count": len(rows), "rows": rows})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/check-status')
def check_status_page():
    return render_template('check_status.html')


@app.route('/api/check-status')
def check_status_api():
    try:
        rows = _load_check_rows_cached()
        return jsonify({"ok": True, "count": len(rows), "rows": rows})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        employee_id = (request.form.get('employee_id') or '').strip().upper()  # ✅ NEW: กันพิมพ์หลุด
        allowed_ids = list(INSPECTOR_MAP.keys())  # ✅ NEW: ใช้จาก mapping

        if employee_id not in allowed_ids:
            return render_template('login.html', error=True)

        session['employee_id'] = employee_id
        session['inspector_name'] = INSPECTOR_MAP.get(employee_id, employee_id)  # ✅ NEW
        session['just_logged_in'] = True
        return redirect(url_for('form'))

    return render_template('login.html')


@app.route('/form')
def form():
    if 'employee_id' not in session:
        return redirect(url_for('login'))
    just_logged_in = session.pop('just_logged_in', False)
    return render_template('form.html', employee_id=session['employee_id'], welcome=just_logged_in)


@app.route('/submit', methods=['POST'])
def submit():
    try:
        if not request.content_type.startswith('multipart/form-data'):
            return "Invalid Content-Type", 400
        
        or_no = request.form.get('or_no')
        company_name = request.form.get('company_name')
        product_type = request.form.get('product_type')
        motor_nameplate = request.form.get('motor_nameplate')
        motor_current = request.form.get('motor_current')
        gear_ratio = request.form.get('gear_ratio')
        gear_sound = request.form.get('gear_sound')
        warranty = request.form.get('warranty')
        inspector = session.get('inspector_name') or request.form.get('inspector')
        oil_type = request.form.get('oil_type')  # ✅ FIXED INDENT
        oil_liters = request.form.get('oil_liters')
        oil_filled = 'เติมแล้ว' if request.form.get('oil_filled') else 'ยังไม่เติม'
        acdc_parts = request.form.getlist('acdc_parts')
        servo_motor_model = request.form.get('servo_motor_model')
        servo_drive_model = request.form.get('servo_drive_model')

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        serial = f"SAS{timestamp}"

        def upload_image(file, field_name):
            if file and file.filename:
                filename = secure_filename(file.filename)
                blob = bucket.blob(f"qc_images/{serial}_{field_name}_{filename}")
                blob.upload_from_file(file.stream, content_type=file.content_type)
                blob.make_public()
                return blob.public_url
            return None

        motor_current_img = request.files.get('motor_current_img')
        gear_sound_img = request.files.get('gear_sound_img')
        assembly_img = request.files.get('assembly_img')
        controller_img = request.files.get('controller_img')
        servo_motor_img = request.files.get('servo_motor_img')
        servo_drive_img = request.files.get('servo_drive_img')
        cable_wire_img = request.files.get('cable_wire_img')

        # ✅ NEW (RFKS): รับไฟล์แนบ Name plate เพิ่ม 2 ช่อง
        rfks_nameplate_motor_img = request.files.get('rfks_nameplate_motor_img')
        rfks_nameplate_gear_img = request.files.get('rfks_nameplate_gear_img')

        images = {
            "motor_current_img": upload_image(motor_current_img, "motor_current"),
            "gear_sound_img": upload_image(gear_sound_img, "gear_sound"),
            "assembly_img": upload_image(assembly_img, "assembly"),
            "controller_img": upload_image(controller_img, "controller"),
            "servo_motor_img": upload_image(servo_motor_img, "servo_motor"),
            "servo_drive_img": upload_image(servo_drive_img, "servo_drive"),
            "cable_wire_img": upload_image(cable_wire_img, "cable_wire"),

            # ✅ NEW (RFKS): อัปโหลดรูป Name plate : Motor / Gear (จะเป็น None ถ้าไม่ได้เลือก หรือไม่ใช่ RFKS)
            "rfks_nameplate_motor_img": upload_image(rfks_nameplate_motor_img, "rfks_nameplate_motor"),
            "rfks_nameplate_gear_img": upload_image(rfks_nameplate_gear_img, "rfks_nameplate_gear"),
        }

        ref.child(serial).set({
            "serial": serial,
            "or_no": or_no,                    # ✅ NEW
            "company_name": company_name,  
            "product_type": product_type,
            "motor_nameplate": motor_nameplate,
            "motor_current": motor_current,
            "gear_ratio": gear_ratio,
            "gear_sound": gear_sound,
            "warranty": warranty,
            "inspector": inspector,
            "oil_type": oil_type,  # ✅ FIXED INDENT
            "oil_liters": oil_liters,
            "oil_filled": oil_filled,
            "acdc_parts": acdc_parts,
            "servo_motor_model": servo_motor_model,
            "servo_drive_model": servo_drive_model,
            "images": images,
            "date": datetime.datetime.now().strftime("%Y-%m-%d")
        })

        def background_finalize():
            try:
                data = ref.child(serial).get()

                # ✅ NEW (RFKS): จัดลำดับรูป + ชื่อรูปให้ตรงหัวข้อ (ไม่กระทบกรณีอื่น)
                images_dict = data.get("images", {}) or {}

                image_urls = []
                image_labels = []

                # 1) RFKS nameplate (เฉพาะเมื่อมีรูปจริง)
                if images_dict.get("rfks_nameplate_motor_img"):
                    image_urls.append(images_dict.get("rfks_nameplate_motor_img"))
                    image_labels.append("Name plate : Motor")

                if images_dict.get("rfks_nameplate_gear_img"):
                    image_urls.append(images_dict.get("rfks_nameplate_gear_img"))
                    image_labels.append("Name plate : Gear")

                # 2) ภาพเดิมทั้งหมด (คงไว้เหมือนเดิม แต่กรอง None ออก)
                ordered_keys = [
                    ("motor_current_img", "ภาพค่ากระแส"),
                    ("gear_sound_img", "ภาพเสียงเกียร์"),
                    ("assembly_img", "ภาพประกอบหน้างาน"),
                    ("controller_img", "ภาพ Controller"),
                    ("servo_motor_img", "ภาพ Servo Motor"),
                    ("servo_drive_img", "ภาพ Servo Drive"),
                    ("cable_wire_img", "ภาพ Cable Wire"),
                ]

                for k, label in ordered_keys:
                    url = images_dict.get(k)
                    if url:
                        image_urls.append(url)
                        image_labels.append(label)

                # ✅ สร้าง PDF และอัปโหลดก่อน
                pdf_stream = create_qc_pdf(
                    data,
                    image_urls=image_urls,
                    image_labels=image_labels
                )
                report_blob = bucket.blob(f"qc_reports/{serial}.pdf")
                pdf_stream.seek(0)
                report_blob.upload_from_file(pdf_stream, content_type="application/pdf")
                report_blob.make_public()

                # ✅ สร้าง QR จาก public_url จริง
                qr_stream = generate_qr_code(serial, report_blob.public_url)
                qr_blob = bucket.blob(f"qr_codes/{serial}.png")
                qr_blob.upload_from_file(qr_stream, content_type="image/png")
                qr_blob.make_public()

                # ✅ บันทึกลิงก์ลง Firebase
                ref.child(serial).update({
                    "qc_pdf_url": report_blob.public_url,
                    "qr_png_url": qr_blob.public_url
                })

                print(f"✅ PDF + QR สำหรับ {serial} สร้างเสร็จ", flush=True)

            except Exception as e:
                print(f"❌ Error in background finalize: {e}", flush=True)

        threading.Thread(target=background_finalize).start()
        return redirect(url_for('success', serial=serial))

    except Exception as e:
        return f"เกิดข้อผิดพลาด: {e}", 400


@app.route('/success')
def success():
    serial = request.args.get('serial', '')
    data = ref.child(serial).get()
    return render_template('success.html',
                           serial_number=serial,
                           qc_url=data.get("qc_pdf_url", "#"),
                           qr_url=data.get("qr_png_url", "#"))


@app.route('/download/<serial_number>')
def download_pdf(serial_number):
    report_data = ref.child(serial_number).get()
    if not report_data:
        return "ไม่พบรายงาน", 404

    # ✅ NEW (RFKS): ให้ download สร้าง PDF ด้วย label/order เดียวกับ background
    images_dict = report_data.get("images", {}) or {}
    image_urls = []
    image_labels = []

    if images_dict.get("rfks_nameplate_motor_img"):
        image_urls.append(images_dict.get("rfks_nameplate_motor_img"))
        image_labels.append("Name plate : Motor")
    if images_dict.get("rfks_nameplate_gear_img"):
        image_urls.append(images_dict.get("rfks_nameplate_gear_img"))
        image_labels.append("Name plate : Gear")

    ordered_keys = [
        ("motor_current_img", "ภาพค่ากระแส"),
        ("gear_sound_img", "ภาพเสียงเกียร์"),
        ("assembly_img", "ภาพประกอบหน้างาน"),
        ("controller_img", "ภาพ Controller"),
        ("servo_motor_img", "ภาพ Servo Motor"),
        ("servo_drive_img", "ภาพ Servo Drive"),
        ("cable_wire_img", "ภาพ Cable Wire"),
    ]
    for k, label in ordered_keys:
        url = images_dict.get(k)
        if url:
            image_urls.append(url)
            image_labels.append(label)

    pdf_stream = create_qc_pdf(report_data, image_urls=image_urls, image_labels=image_labels)
    pdf_stream.seek(0)
    return send_file(pdf_stream,
                     as_attachment=True,
                     download_name=f"{serial_number}_QC_Report.pdf",
                     mimetype='application/pdf')


@app.route('/qr/<serial_number>')
def generate_qr(serial_number):
    # ✅ NEW: ถ้ามีลิงก์ PDF แล้ว ให้ QR ชี้ไปที่ PDF จริง
    report_data = ref.child(serial_number).get() or {}
    pdf_url = report_data.get("qc_pdf_url")

    if pdf_url:
        link = pdf_url
    else:
        # fallback ถ้ายังไม่เสร็จ
        link = f"https://sas-qc-gearmotor.onrender.com/autodownload/{serial_number}"

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(link)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    qr_stream = io.BytesIO()
    img.save(qr_stream, format='PNG')
    qr_stream.seek(0)
    return send_file(qr_stream, mimetype='image/png', download_name=f"{serial_number}_QR.png")


@app.route('/autodownload/<serial_number>')
def autodownload(serial_number):
    report_data = ref.child(serial_number).get()
    if not report_data:
        return "ไม่พบรายงาน", 404

    # ✅ NEW (RFKS): ให้ autodownload สร้าง PDF ด้วย label/order เดียวกับ background
    images_dict = report_data.get("images", {}) or {}
    image_urls = []
    image_labels = []

    if images_dict.get("rfks_nameplate_motor_img"):
        image_urls.append(images_dict.get("rfks_nameplate_motor_img"))
        image_labels.append("Name plate : Motor")
    if images_dict.get("rfks_nameplate_gear_img"):
        image_urls.append(images_dict.get("rfks_nameplate_gear_img"))
        image_labels.append("Name plate : Gear")

    ordered_keys = [
        ("motor_current_img", "ภาพค่ากระแส"),
        ("gear_sound_img", "ภาพเสียงเกียร์"),
        ("assembly_img", "ภาพประกอบหน้างาน"),
        ("controller_img", "ภาพ Controller"),
        ("servo_motor_img", "ภาพ Servo Motor"),
        ("servo_drive_img", "ภาพ Servo Drive"),
        ("cable_wire_img", "ภาพ Cable Wire"),
    ]
    for k, label in ordered_keys:
        url = images_dict.get(k)
        if url:
            image_urls.append(url)
            image_labels.append(label)

    pdf_stream = create_qc_pdf(report_data, image_urls=image_urls, image_labels=image_labels)
    pdf_stream.seek(0)
    return send_file(pdf_stream,
                     as_attachment=True,
                     download_name=f"{serial_number}_QC_Report.pdf",
                     mimetype='application/pdf')


if __name__ == '__main__':
    app.run(debug=True)
