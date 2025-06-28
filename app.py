from flask import Flask, render_template, request, redirect, send_file
from werkzeug.utils import secure_filename
import os
import firebase_admin
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
    'databaseURL': 'https://sas-qc-gearmotor-app.firebaseio.com/',
    'storageBucket': 'sas-qc-gearmotor-app.firebasestorage.app' # ✅ เปลี่ยนตรงนี้
})

ref = db.reference("/qc_reports")
bucket = storage.bucket()

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

@app.route('/')
def index():
    return render_template('index.html')

# ✅ ฟอร์มกรอก QC
@app.route('/submit', methods=['POST'])
def submit():
    try:
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

        # อัปโหลดรูปภาพแต่ละรายการ (ตามชื่อ field ใน form.html)
        image_fields = [
            'motor_current_img', 'gear_sound_img',
            'assembly_img', 'check_complete_img'
        ]

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        image_urls = {}

        for field in image_fields:
            file = request.files.get(field)
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                blob = bucket.blob(f"qc_images/{timestamp}_{filename}")
                blob.upload_from_file(file.stream, content_type=file.content_type)
                blob.make_public()
                image_urls[field] = blob.public_url

        data['images'] = image_urls

        # สร้างหมายเลข Serial และบันทึกลง Firebase
        serial_number = f"SAS{timestamp}"
        ref.child(serial_number).set(data)

        return redirect(f"/success?serial={serial_number}")
    except Exception as e:
        return f"เกิดข้อผิดพลาด: {e}", 400

# ✅ แสดงหน้าสำเร็จ
@app.route('/success')
def success():
    serial = request.args.get('serial', '')
    return render_template('success.html', serial_number=serial)

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