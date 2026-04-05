from dotenv import load_dotenv
load_dotenv()
from flask import Flask, render_template, request, redirect, send_file, url_for, session, jsonify
from werkzeug.utils import secure_filename
import os
import firebase_admin
from firebase_admin import credentials, db
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

# ✅ NEW: Cloudflare R2 (แทน Firebase Storage)
import boto3
from botocore.client import Config

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'
app.config['UPLOAD_FOLDER'] = 'uploads'

# ==== Load Firebase Credential from Environment ====
# (ยังคงใช้ Firebase Realtime Database เหมือนเดิมทุกอย่าง)
firebase_json = json.loads(os.environ.get("FIREBASE_CREDENTIAL_JSON"))
cred = credentials.Certificate(firebase_json)

firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://sas-qc-gearmotor-app-default-rtdb.asia-southeast1.firebasedatabase.app/',
})

ref = db.reference("/qc_data")

# ==== Cloudflare R2 Setup ====
# ตั้งค่า Environment Variables บน Render.com:
#   R2_ACCOUNT_ID     = Cloudflare Account ID (ดูได้จาก R2 dashboard)
#   R2_ACCESS_KEY_ID  = R2 API Token Access Key ID
#   R2_SECRET_KEY     = R2 API Token Secret Access Key
#   R2_BUCKET_NAME    = ชื่อ bucket ที่สร้างไว้
#   R2_PUBLIC_URL     = https://<your-bucket>.<your-subdomain>.r2.dev  (หรือ custom domain)
#
# วิธีสร้าง R2 API Token:
#   1. เข้า Cloudflare Dashboard → R2 → Manage R2 API Tokens
#   2. Create API Token → ให้สิทธิ์ Object Read & Write
#   3. คัดลอก Access Key ID และ Secret Access Key ไปใส่ใน Render Environment

R2_ACCOUNT_ID    = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_KEY    = os.environ.get("R2_SECRET_KEY", "")
R2_BUCKET_NAME   = os.environ.get("R2_BUCKET_NAME", "sas-qc-gearmotor")
R2_PUBLIC_URL    = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")

r2 = boto3.client(
    "s3",
    endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_KEY,
    config=Config(signature_version="s3v4"),
    region_name="auto",
)

def r2_upload_fileobj(fileobj, key, content_type):
    """
    อัปโหลด file-like object ขึ้น R2
    คืน public URL ของไฟล์
    """
    r2.upload_fileobj(
        fileobj,
        R2_BUCKET_NAME,
        key,
        ExtraArgs={"ContentType": content_type},
    )
    return f"{R2_PUBLIC_URL}/{key}"

def r2_upload_bytes(data_bytes, key, content_type):
    """
    อัปโหลด bytes ขึ้น R2
    คืน public URL ของไฟล์
    """
    r2.put_object(
        Bucket=R2_BUCKET_NAME,
        Key=key,
        Body=data_bytes,
        ContentType=content_type,
    )
    return f"{R2_PUBLIC_URL}/{key}"

def r2_download_bytes(key):
    """
    ดาวน์โหลดไฟล์จาก R2 คืนเป็น bytes
    """
    resp = r2.get_object(Bucket=R2_BUCKET_NAME, Key=key)
    return resp["Body"].read()

def r2_get_mtime(key):
    """
    ดึง LastModified ของ object ใน R2 (ใช้เป็น cache key เหมือนเดิม)
    """
    resp = r2.head_object(Bucket=R2_BUCKET_NAME, Key=key)
    return str(resp.get("LastModified", ""))

# =========================
# Stock on shelf (FAST + SIMPLE)
# =========================
STOCK_BLOB_NAME = "stock/Stock motor.xlsx"
STOCK_XLS_PATH = os.path.join(os.path.dirname(__file__), "Stock motor.xlsx")
STOCK_UPLOAD_PASS = "Adminsas2026"

# ✅ Supplier (ZD) stock file
SUPPLIER_BLOB_NAME = "stock/Stock ZD.xlsx"
SUPPLIER_UPLOAD_PASS = "Chottaninsas2029"

CHECK_BLOB_NAME = "stock/Check status.xlsx"
CHECK_UPLOAD_PASS = "Adminsas2026"
_check_cache = {"mtime": None, "rows": []}
_check_lock = threading.Lock()

_stock_cache = {
    "mtime": None,
    "rows": []
}
_stock_lock = threading.Lock()

# ✅ cache for Supplier ZD
_supplier_cache = {"mtime": None, "rows": []}
_supplier_lock = threading.Lock()


def _load_stock_rows_cached():
    """
    Read latest stock xlsx from R2 and cache by object mtime.
    Range:
      Code: A (col 1)
      Description: E (col 5)
      Total: AF (col 32)
    """
    mtime = r2_get_mtime(STOCK_BLOB_NAME)

    with _stock_lock:
        if _stock_cache["mtime"] == mtime and _stock_cache["rows"] is not None:
            return _stock_cache["rows"]

        data = r2_download_bytes(STOCK_BLOB_NAME)
        wb = load_workbook(BytesIO(data), data_only=True, read_only=True)
        ws = next((s for s in wb.worksheets if (s.max_column or 0) >= 32 and (s.max_row or 0) >= 3000), wb.worksheets[0])

        rows = []

        for row in ws.iter_rows(min_row=2, max_row=3000, min_col=1, max_col=32, values_only=True):
            code  = row[0] if len(row) > 0 else None   # A
            desc  = row[4] if len(row) > 4 else None   # E
            total = row[31] if len(row) > 31 else None  # AF

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


# ✅ Supplier ZD rows (Sheet1, A2-A400, B2-B400)
def _load_supplier_rows_cached():
    mtime = r2_get_mtime(SUPPLIER_BLOB_NAME)

    with _supplier_lock:
        if _supplier_cache["mtime"] == mtime and _supplier_cache["rows"] is not None:
            return _supplier_cache["rows"]

        data = r2_download_bytes(SUPPLIER_BLOB_NAME)
        wb = load_workbook(BytesIO(data), data_only=True, read_only=True)

        ws = wb["Sheet1"] if "Sheet1" in wb.sheetnames else wb.worksheets[0]

        rows = []
        for row in ws.iter_rows(min_row=2, max_row=400, min_col=1, max_col=2, values_only=True):
            code = row[0] if len(row) > 0 else None  # A
            total = row[1] if len(row) > 1 else None  # B

            code_s = "" if code is None else str(code).strip()
            if code_s:
                rows.append({
                    "code": code_s,
                    "description": "Supplier ZD",
                    "total": "" if total is None else str(total).strip()
                })

        _supplier_cache["mtime"] = mtime
        _supplier_cache["rows"] = rows
        return rows

    
def _load_check_rows_cached():
    mtime = r2_get_mtime(CHECK_BLOB_NAME)

    with _check_lock:
        if _check_cache["mtime"] == mtime and _check_cache["rows"] is not None:
            return _check_cache["rows"]

        data = r2_download_bytes(CHECK_BLOB_NAME)
        wb = load_workbook(BytesIO(data), data_only=True, read_only=True)
        year_sheets = [s for s in wb.sheetnames if str(s).isdigit()]
        ws = wb[str(max(map(int, year_sheets)))] if year_sheets else wb.worksheets[0]

        rows = []

        for row in ws.iter_rows(min_row=25, max_row=600, min_col=1, max_col=18, values_only=True):
            no_item      = row[0]   # A
            po_no        = row[3]   # D
            po_open_date = row[4]   # E
            stock_order  = row[5]   # G
            amount       = row[6]   # H
            transport    = row[12]  # N
            factory_eta  = row[15]  # Q
            delivery_eta = row[16]  # R

            if (no_item is None and po_no is None and po_open_date is None
                and stock_order is None and amount is None and transport is None
                and factory_eta is None and delivery_eta is None):
                continue

            rows.append({
                "no_item": "" if no_item is None else str(no_item).strip(),
                "po_open_date": "" if po_open_date is None else str(po_open_date).strip(),
                "po": "" if po_no is None else str(po_no).strip(),
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

        session["stock_upload_ok"] = True
        return render_template("stock_upload_public.html")

    # ===== POST =====
    if not session.get("stock_upload_ok"):
        return "Unauthorized", 403

    f = request.files.get("file")
    if not f or f.filename.strip() == "":
        return "No file uploaded", 400

    if not f.filename.lower().endswith(".xlsx"):
        return "Invalid file type (ต้องเป็น .xlsx เท่านั้น)", 400

    # ✅ อัปโหลดขึ้น R2 แทน Firebase Storage
    r2_upload_fileobj(
        f,
        STOCK_BLOB_NAME,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # ล้าง cache เพื่อให้หน้า stock ดึงไฟล์ใหม่ทันที
    with _stock_lock:
        _stock_cache["mtime"] = None
        _stock_cache["rows"] = []

    session.pop("stock_upload_ok", None)

    return redirect("/stock")


@app.route("/check-status-upload", methods=["GET", "POST"])
def check_status_upload_public():
    if request.method == "GET":
        key = (request.args.get("key") or "").strip()
        if key != CHECK_UPLOAD_PASS:
            return "Unauthorized", 403

        session["check_upload_ok"] = True
        return render_template("check_status_upload_public.html")

    # ===== POST =====
    if not session.get("check_upload_ok"):
        return "Unauthorized", 403

    f = request.files.get("file")
    if not f or f.filename.strip() == "":
        return "No file uploaded", 400

    if not f.filename.lower().endswith(".xlsx"):
        return "Invalid file type (ต้องเป็น .xlsx เท่านั้น)", 400

    # ✅ อัปโหลดขึ้น R2 แทน Firebase Storage
    r2_upload_fileobj(
        f,
        CHECK_BLOB_NAME,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # ล้าง cache
    with _check_lock:
        _check_cache["mtime"] = None
        _check_cache["rows"] = []

    session.pop("check_upload_ok", None)

    return redirect("/check-status")

                
# ✅ Inspector mapping (ID -> ชื่อ)
INSPECTOR_MAP = {
    "QC001": "คุณสมประสงค์",
    "QC002": "คุณเกียรติศักดิ์",
    "QC003": "คุณมัด",
    "QC999": "คุณโชติธนินท์",
}


@app.route('/')
def home():
    return render_template('index.html')

@app.route("/healthz")
def healthz():
    return "ok", 200

@app.route('/stock')
def stock_page():
    return render_template('stock.html')

@app.route('/api/stock')
def stock_api():
    try:
        rows_main = _load_stock_rows_cached()

        try:
            rows_supplier = _load_supplier_rows_cached()
        except Exception:
            rows_supplier = []

        rows = (rows_main or []) + (rows_supplier or [])
        return jsonify({"ok": True, "count": len(rows), "rows": rows})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ✅ Supplier upload page
@app.route("/supplier-upload", methods=["GET", "POST"])
def supplier_upload_public():
    if request.method == "GET":
        key = (request.args.get("key") or "").strip()
        if key != SUPPLIER_UPLOAD_PASS:
            return "Unauthorized", 403

        session["supplier_upload_ok"] = True
        return render_template("stock_upload_supplier.html")

    # ===== POST =====
    if not session.get("supplier_upload_ok"):
        return "Unauthorized", 403

    f = request.files.get("file")
    if not f or f.filename.strip() == "":
        return "No file uploaded", 400

    if not f.filename.lower().endswith(".xlsx"):
        return "Invalid file type (ต้องเป็น .xlsx เท่านั้น)", 400

    # ✅ อัปโหลดขึ้น R2 แทน Firebase Storage
    r2_upload_fileobj(
        f,
        SUPPLIER_BLOB_NAME,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # ล้าง cache
    with _supplier_lock:
        _supplier_cache["mtime"] = None
        _supplier_cache["rows"] = []

    session.pop("supplier_upload_ok", None)
    return redirect("/stock")


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
        employee_id = (request.form.get('employee_id') or '').strip().upper()
        allowed_ids = list(INSPECTOR_MAP.keys())

        if employee_id not in allowed_ids:
            return render_template('login.html', error=True)

        session['employee_id'] = employee_id
        session['inspector_name'] = INSPECTOR_MAP.get(employee_id, employee_id)
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
        oil_type = request.form.get('oil_type')
        oil_liters = request.form.get('oil_liters')
        oil_filled = 'เติมแล้ว' if request.form.get('oil_filled') else 'ยังไม่เติม'
        acdc_parts = request.form.getlist('acdc_parts')
        servo_motor_model = request.form.get('servo_motor_model')
        servo_drive_model = request.form.get('servo_drive_model')

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        serial = f"SAS{timestamp}"

        def upload_image(file, field_name):
            """
            ✅ อัปโหลดรูปขึ้น R2 แทน Firebase Storage
            คืน public URL เหมือนเดิมทุกอย่าง
            """
            if file and file.filename:
                filename = secure_filename(file.filename)
                key = f"qc_images/{serial}_{field_name}_{filename}"
                # อ่าน stream เป็น bytes เพื่อ upload
                file_bytes = file.stream.read()
                return r2_upload_bytes(file_bytes, key, file.content_type or "image/jpeg")
            return None

        motor_current_img = request.files.get('motor_current_img')
        gear_sound_img = request.files.get('gear_sound_img')
        assembly_img = request.files.get('assembly_img')
        controller_img = request.files.get('controller_img')
        servo_motor_img = request.files.get('servo_motor_img')
        servo_drive_img = request.files.get('servo_drive_img')
        cable_wire_img = request.files.get('cable_wire_img')

        # ✅ RFKS: รับไฟล์แนบ Name plate เพิ่ม 2 ช่อง
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

            # ✅ RFKS: อัปโหลดรูป Name plate : Motor / Gear
            "rfks_nameplate_motor_img": upload_image(rfks_nameplate_motor_img, "rfks_nameplate_motor"),
            "rfks_nameplate_gear_img": upload_image(rfks_nameplate_gear_img, "rfks_nameplate_gear"),
        }

        ref.child(serial).set({
            "serial": serial,
            "or_no": or_no,
            "company_name": company_name,  
            "product_type": product_type,
            "motor_nameplate": motor_nameplate,
            "motor_current": motor_current,
            "gear_ratio": gear_ratio,
            "gear_sound": gear_sound,
            "warranty": warranty,
            "inspector": inspector,
            "oil_type": oil_type,
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

                # ✅ RFKS: จัดลำดับรูป + ชื่อรูปให้ตรงหัวข้อ
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

                # 2) ภาพเดิมทั้งหมด
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

                # ✅ สร้าง PDF
                pdf_stream = create_qc_pdf(
                    data,
                    image_urls=image_urls,
                    image_labels=image_labels
                )
                pdf_stream.seek(0)

                # ✅ อัปโหลด PDF ขึ้น R2
                pdf_key = f"qc_reports/{serial}.pdf"
                pdf_url = r2_upload_bytes(
                    pdf_stream.read(),
                    pdf_key,
                    "application/pdf"
                )

                # ✅ สร้าง QR จาก public URL จริง
                qr_stream = generate_qr_code(serial, pdf_url)
                qr_key = f"qr_codes/{serial}.png"
                qr_url = r2_upload_bytes(
                    qr_stream.read(),
                    qr_key,
                    "image/png"
                )

                # ✅ บันทึกลิงก์ลง Firebase Realtime Database (เหมือนเดิม)
                ref.child(serial).update({
                    "qc_pdf_url": pdf_url,
                    "qr_png_url": qr_url
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

    # ✅ RFKS: ให้ download สร้าง PDF ด้วย label/order เดียวกับ background
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
    # ✅ ถ้ามีลิงก์ PDF แล้ว ให้ QR ชี้ไปที่ PDF จริง
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

    # ✅ RFKS: ให้ autodownload สร้าง PDF ด้วย label/order เดียวกับ background
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
