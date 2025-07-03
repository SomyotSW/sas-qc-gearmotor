from flask import Flask, render_template, request, redirect, send_file
from werkzeug.utils import secure_filename
import os
import firebase_admin
import traceback
from firebase_admin import credentials, db, storage
import datetime
import io
from utils.generate_pdf import create_qc_pdf
from utils.qr_generator import generate_qr_code
import json

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'

# ==== Load Firebase Credential from Environment ====
firebase_json = json.loads(os.environ.get("FIREBASE_CREDENTIAL_JSON"))
cred = credentials.Certificate(firebase_json)

firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://sas-qc-gearmotor-app-default-rtdb.asia-southeast1.firebasedatabase.app/',
    'storageBucket': 'sas-qc-gearmotor-app.firebasestorage.app' # ✅ เปลี่ยนตรงนี้
})

ref = db.reference("/qc_reports")
bucket = storage.bucket()

@app.route('/')
def home():
    return render_template('index.html')

# ✅ หน้าแรกพนักงาน QC กด Login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        employee_id = request.form.get('employee_id')

        # 🔐 ตรวจสอบรหัส (ถ้ามี whitelist เช่น: 'QC001', 'QC002')
        allowed_ids = ['QC001', 'QC002', 'QC003']
        if employee_id not in allowed_ids:
            return "รหัสพนักงานไม่ถูกต้อง", 403

        return render_template('form.html', employee_id=employee_id)

    return render_template('login.html')

@app.route('/submit', methods=['POST'])
def submit():
    try:
        print("📥 เริ่มรับข้อมูลจากฟอร์ม QC")

        # ==== ดึงข้อมูลจากฟอร์ม ====
        data = {
            'motor_nameplate': request.form.get('motor_nameplate'),
            'motor_current': request.form.get('motor_current'),
            'gear_ratio': request.form.get('gear_ratio'),
            'gear_sound': request.form.get('gear_sound'),
            'check_complete': request.form.get('check_complete'),
            'incomplete_reason': request.form.get('incomplete_reason'),
            'warranty': request.form.get('warranty'),
            'inspector': request.form.get('inspector'),
            'oil_liters': request.form.get('oil_liters'),
            'oil_filled': 'เติมแล้ว' if request.form.get('oil_filled') else 'ยังไม่เติม'
        }

        print(f"📄 ข้อมูลฟอร์มที่ได้รับ: {data}")

        # ==== ตรวจสอบค่าจำเป็น ====
        required_fields = ['motor_nameplate', 'inspector']
        for field in required_fields:
            if not data[field]:
                raise ValueError(f"❌ Missing required field: {field}")

        # ==== สร้าง Serial Number ====
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        serial_number = f"SAS{timestamp}"
        data['serial_number'] = serial_number

        print(f"🔢 สร้าง Serial Number: {serial_number}")

        # ==== เตรียมรูปภาพ ====
        image_fields = [
            'motor_current_img', 'gear_sound_img',
            'assembly_img', 'check_complete_img'
        ]

        image_urls = {}
        for field in image_fields:
            file = request.files.get(field)
            if file and file.filename:
                filename = secure_filename(file.filename)
                storage_path = f"qc_images/{serial_number}_{filename}"

                print(f"🖼️ Uploading {field} => {storage_path}")

                blob = bucket.blob(storage_path)
                blob.upload_from_file(file.stream, content_type=file.content_type)
                blob.make_public()
                image_urls[field] = blob.public_url
            else:
                print(f"⚠️ ไม่พบไฟล์แนบสำหรับ: {field}")

        data['images'] = image_urls

        # ==== บันทึกลง Firebase ====
        print("📤 กำลังบันทึกข้อมูลลง Firebase Database...")
        ref.child(serial_number).set(data)
        print("✅ บันทึกสำเร็จ")

        # ==== Redirect ไปหน้า Success ====
        return redirect(f"/success?serial={serial_number}")

    except Exception as e:
        print("❌ ERROR เกิดขึ้นใน /submit")
        traceback.print_exc()
        return f"เกิดข้อผิดพลาด: {e}", 400

@app.route('/success')
def success():
    serial = request.args.get('serial', '')

    # ✅ ชื่อ bucket ที่ถูกต้อง
    bucket_name = "sas-qc-gearmotor-app.firebasestorage.app"

    # ✅ ลิงก์ต้องใช้กับ bucket นี้
    qc_url = f"https://storage.googleapis.com/{bucket_name}/qc_reports/{serial}.pdf"
    qr_url = f"https://storage.googleapis.com/{bucket_name}/qr_codes/{serial}.pdf"

    return render_template("success.html", serial_number=serial, qc_url=qc_url, qr_url=qr_url)

# ✅ ให้ลูกค้าโหลด PDF QC ได้โดยตรง
@app.route('/download/<serial_number>')
def download_pdf(serial_number):
    report_data = ref.child(serial_number).get()
    if not report_data:
        return "Report not found", 404

    pdf_stream = create_qc_pdf(report_data)
    return send_file(
        pdf_stream,
        as_attachment=True,
        download_name=f"{serial_number}_QC_Report.pdf",
        mimetype='application/pdf'
    )

# ✅ สร้าง QR จาก Serial (ใช้ภายใน)
@app.route('/qr/<serial_number>')
def generate_qr(serial_number):
    qr_stream = generate_qr_code(serial_number)
    return send_file(
        qr_stream,
        mimetype='image/png'
    )

if __name__ == '__main__':
    app.run(debug=True)