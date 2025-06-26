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
    'databaseURL': 'https://sas-transmission.firebaseio.com/',
    'storageBucket': 'sas-transmission.appspot.com'
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

        # ✅ ส่งต่อไปหน้า form พร้อมพารามิเตอร์
        return render_template('form.html', employee_id=employee_id)

    return render_template('login.html')

@app.route('/form', methods=['POST'])
def form():
    employee_id = request.form.get('employee_id')
    return render_template('form.html', employee_id=employee_id)

@app.route('/')
def index():
    return render_template('index.html')

# ✅ เพิ่มหน้าล็อกอินสำหรับพนักงาน QC

# ✅ ฟอร์มกรอก QC
@app.route('/submit', methods=['POST'])
def submit():
    data = {
        'serial_number': request.form['serial_number'],
        'model': request.form['model'],
        'inspection_date': request.form['inspection_date'],
        'inspector': request.form['inspector'],
        'remarks': request.form['remarks']
    }

    images = request.files.getlist('images')
    image_urls = []

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    for image in images:
        if image and image.filename != '':
            filename = secure_filename(image.filename)
            blob = bucket.blob(f"qc_images/{timestamp}_{filename}")
            blob.upload_from_file(image.stream, content_type=image.content_type)
            blob.make_public()
            image_urls.append(blob.public_url)

    data['image_urls'] = image_urls

    # บันทึกลง Firebase
    ref.child(data['serial_number']).set(data)

    return redirect('/success')

# ✅ แสดงหน้าสำเร็จ
@app.route('/success')
def success():
    return render_template('success.html')

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