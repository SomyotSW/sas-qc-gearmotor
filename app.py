from flask import Flask, render_template, request, redirect, send_file, url_for
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
import uuid

app = Flask(__name__)
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

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        employee_id = request.form.get('employee_id')
        allowed_ids = ['QC001', 'QC002', 'QC003']
        if employee_id not in allowed_ids:
            return "รหัสพนักงานไม่ถูกต้อง", 403
        return render_template('form.html', employee_id=employee_id)
    return render_template('login.html')

@app.route('/submit', methods=['POST'])
def submit():
    try:
        if not request.content_type.startswith('multipart/form-data'):
            return "Invalid Content-Type", 400

        motor_nameplate = request.form.get('motor_nameplate')
        motor_current = request.form.get('motor_current')
        gear_ratio = request.form.get('gear_ratio')
        gear_sound = request.form.get('gear_sound')
        check_complete = request.form.get('check_complete')
        incomplete_reason = request.form.get('incomplete_reason')
        warranty = request.form.get('warranty')
        inspector = request.form.get('inspector')
        oil_liters = request.form.get('oil_liters')
        oil_filled = 'เติมแล้ว' if request.form.get('oil_filled') else 'ยังไม่เติม'

        motor_current_img = request.files.get('motor_current_img')
        gear_sound_img = request.files.get('gear_sound_img')
        assembly_img = request.files.get('assembly_img')
        check_complete_img = request.files.get('check_complete_img')

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

        images = {
            "motor_current_img": upload_image(motor_current_img, "motor_current"),
            "gear_sound_img": upload_image(gear_sound_img, "gear_sound"),
            "assembly_img": upload_image(assembly_img, "assembly"),
            "check_complete_img": upload_image(check_complete_img, "check_complete")
        }

        # ✅ สร้าง QR และ PDF Report
        qr_stream = generate_qr_code(serial)
        pdf_stream = create_qc_pdf({
            "serial": serial,
            "motor_nameplate": motor_nameplate,
            "motor_current": motor_current,
            "gear_ratio": gear_ratio,
            "gear_sound": gear_sound,
            "check_complete": check_complete,
            "incomplete_reason": incomplete_reason,
            "warranty": warranty,
            "inspector": inspector,
            "oil_liters": oil_liters,
            "oil_filled": oil_filled,
            "images": images,
            "date": datetime.datetime.now().strftime("%Y-%m-%d")
        }, image_urls=list(images.values()))

        # ✅ อัปโหลด QR และ PDF ขึ้น Firebase
        qr_blob = bucket.blob(f"qr_codes/{serial}.pdf")
        qr_blob.upload_from_file(qr_stream, content_type="application/pdf")
        qr_blob.make_public()

        report_blob = bucket.blob(f"qc_reports/{serial}.pdf")
        report_blob.upload_from_file(pdf_stream, content_type="application/pdf")
        report_blob.make_public()

        # ✅ เขียนข้อมูลลง Firebase
        ref.child(serial).set({
            "serial": serial,
            "motor_nameplate": motor_nameplate,
            "motor_current": motor_current,
            "gear_ratio": gear_ratio,
            "gear_sound": gear_sound,
            "check_complete": check_complete,
            "incomplete_reason": incomplete_reason,
            "warranty": warranty,
            "inspector": inspector,
            "oil_liters": oil_liters,
            "oil_filled": oil_filled,
            "images": images,
            "qc_pdf_url": report_blob.public_url,
            "qr_pdf_url": qr_blob.public_url,
            "date": datetime.datetime.now().strftime("%Y-%m-%d")
        })

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
                           qr_url=data.get("qr_pdf_url", "#"))

@app.route('/download/<serial_number>')
def download_pdf(serial_number):
    report_data = ref.child(serial_number).get()
    if not report_data:
        return "Report not found", 404
    pdf_stream = create_qc_pdf(report_data, image_urls=list(report_data.get("images", {}).values()))
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
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(serial_number)
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')
    qr_stream = io.BytesIO()
    img.save(qr_stream, 'PNG')
    qr_stream.seek(0)
    return send_file(qr_stream, mimetype='image/png', download_name=f'{serial_number}.png')

if __name__ == '__main__':
    app.run(debug=True)