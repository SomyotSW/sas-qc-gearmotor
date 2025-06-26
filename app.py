from flask import Flask, render_template, request, send_file
from werkzeug.utils import secure_filename
import os
import io
import datetime
import firebase_admin
from firebase_admin import credentials, db, storage
from utils.generate_pdf import create_qc_pdf
from utils.qr_generator import generate_qr_pdf

# ===== CONFIG =====
UPLOAD_FOLDER = 'uploads'
QR_FOLDER = 'qr'

# ===== Flask Setup =====
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# ===== Firebase Setup =====
cred = credentials.Certificate("sas-qc-gearmotor-app.json")
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://sas-transmission.firebaseio.com/',
    'storageBucket': 'sas-transmission.appspot.com'
})

bucket = storage.bucket()
ref = db.reference("/qc_reports")

# ===== Ensure folders exist =====
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(QR_FOLDER, exist_ok=True)

# ===== Routes =====
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/submit', methods=['POST'])
def submit():
    data = request.form.to_dict()
    files = request.files.getlist('images')

    serial = f"SAS{datetime.datetime.now().strftime('%y%m%d%H%M%S')}"
    data['serial'] = serial
    data['date'] = datetime.datetime.now().strftime('%Y-%m-%d')

    image_urls = []
    for file in files:
        if file:
            filename = secure_filename(file.filename)
            blob = bucket.blob(f"qc_images/{serial}/{filename}")
            blob.upload_from_file(file.stream, content_type=file.content_type)
            blob.make_public()
            image_urls.append(blob.public_url)

    data['images'] = image_urls
    ref.child(serial).set(data)

    # ===== Generate PDF QC Report =====
    pdf_stream = create_qc_pdf(data, image_urls)
    pdf_blob = bucket.blob(f"qc_reports/{serial}.pdf")
    pdf_blob.upload_from_file(pdf_stream, content_type='application/pdf')
    pdf_blob.make_public()

    # ===== Generate QR Code PDF =====
    qr_stream = generate_qr_pdf(serial)
    qr_blob = bucket.blob(f"qr/{serial}.pdf")
    qr_blob.upload_from_file(qr_stream, content_type='application/pdf')
    qr_blob.make_public()

    return render_template('success.html', serial=serial, qc_url=pdf_blob.public_url, qr_url=qr_blob.public_url)

@app.route('/check')
def check_serial():
    serial = request.args.get('serial', '')
    record = ref.child(serial).get()
    if not record:
        return render_template('check_serial.html', not_found=True, serial=serial)
    pdf_url = f"https://storage.googleapis.com/sas-transmission.appspot.com/qc_reports/{serial}.pdf"
    return render_template('check_serial.html', not_found=False, serial=serial, data=record, pdf_url=pdf_url)

if __name__ == '__main__':
    app.run(debug=True)