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
        # ===== 1. รับค่าจากฟอร์ม =====
        serial_number = request.form['serial_number']
        customer_name = request.form['customer_name']
        inspector = request.form['inspector']
        install_date = request.form['install_date']
        motor_type = request.form['motor_type']
        note = request.form['note']
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # ===== 2. สร้างไฟล์ PDF รายงาน QC และ QR Code =====
        pdf_path, qr_path, pdf_success, qr_success = generate_pdf_and_qr(
            serial_number,
            motor_type,
            customer_name,
            inspector,
            install_date,
            note
        )

        # ===== 3. อัปโหลดไฟล์ขึ้น Firebase Storage และรับ URL =====
        qc_pdf_url = upload_file_to_firebase(pdf_path, folder_name="qc_reports")
        qr_pdf_url = upload_file_to_firebase(qr_path, folder_name="qr_codes")

        # ===== 4. เก็บข้อมูลลง Firebase Database =====
        data = {
            "serial_number": serial_number,
            "customer_name": customer_name,
            "motor_type": motor_type,
            "inspector": inspector,
            "install_date": install_date,
            "note": note,
            "timestamp": now,
            "qc_pdf_url": qc_pdf_url,
            "qr_pdf_url": qr_pdf_url,
        }
        ref = db.reference(f"/qc_reports/{serial_number}")
        ref.set(data)

        # ===== 5. ส่งอีเมล (ถ้าต้องการเปิดใช้งาน) =====
        # send_email(serial_number, qc_pdf_url, qr_pdf_url)

        # ===== 6. กลับไปหน้า success พร้อม Serial =====
        return redirect(url_for('success', serial=serial_number))

    except Exception as e:
        return f"❌ เกิดข้อผิดพลาด: {e}", 500


@app.route('/success')
def success():
    serial = request.args.get('serial', '')
    ref = db.reference(f"/qc_reports/{serial}")
    data = ref.get()

    # ✅ ชื่อ bucket ที่ถูกต้อง
    bucket_name = "sas-qc-gearmotor-app.firebasestorage.app"

    # ✅ ลิงก์ต้องใช้กับ bucket นี้
    qc_url = f"https://storage.googleapis.com/{bucket_name}/qc_reports/{serial}.pdf"
    qr_url = f"https://storage.googleapis.com/{bucket_name}/qr_codes/{serial}.pdf"

    return render_template('success.html',
                           serial_number=serial,
                           qc_url=data.get("qc_pdf_url", "#"),
                           qr_url=data.get("qr_pdf_url", "#"))

def upload_file_to_firebase(file_path, folder_name="uploads"):
    from firebase_admin import storage
    import os

    bucket = storage.bucket()
    file_name = os.path.basename(file_path)
    blob = bucket.blob(f"{folder_name}/{file_name}")
    blob.upload_from_filename(file_path)
    blob.make_public()  # ทำให้ลิงก์เปิดดูได้จากภายนอก
    return blob.public_url

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

@app.route('/qr/<serial_number>')
def generate_qr(serial_number):
    import io
    import qrcode

    # สร้าง QR Code
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(serial_number)
    qr.make(fit=True)

    img = qr.make_image(fill='black', back_color='white')

    # บันทึกภาพลงหน่วยความจำ
    qr_stream = io.BytesIO()
    img.save(qr_stream, 'PNG')
    qr_stream.seek(0)

    return send_file(
        qr_stream,
        mimetype='image/png',
        download_name=f'{serial_number}.png'  # ให้ชื่อไฟล์เวลาโหลด
    )

if __name__ == '__main__':
    app.run(debug=True)