# sasqc.py
from flask import Flask, render_template, request, redirect, send_file, url_for, session
import os
from datetime import datetime, timedelta
import qrcode
import random
import string
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import smtplib
from email.message import EmailMessage

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['QR_FOLDER'] = 'static/qr_codes'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['QR_FOLDER'], exist_ok=True)

EMAIL_ADDRESS = "your_email@example.com"
EMAIL_PASSWORD = "your_email_password"

AUTHORIZED_IDS = {'QC001', 'QC002', 'ST001'}

pdfmetrics.registerFont(TTFont('THSarabunNew', 'THSarabunNew.ttf'))

def generate_serial():
    return ''.join(random.choices(string.digits, k=10))

def create_qr(serial):
    img = qrcode.make(url_for('show_customer_report', serial=serial, _external=True))
    qr_path = os.path.join(app.config['QR_FOLDER'], f'{serial}.png')
    img.save(qr_path)
    return qr_path

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        staff_id = request.form.get('staff_id')
        if staff_id in AUTHORIZED_IDS:
            session['staff_id'] = staff_id
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='รหัสไม่ถูกต้อง')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('staff_id', None)
    return redirect(url_for('login'))

@app.route('/form', methods=['GET'])
def index():
    if 'staff_id' not in session:
        return redirect(url_for('login'))
    return render_template('form.html')

@app.route('/submit', methods=['POST'])
def submit_form():
    if 'staff_id' not in session:
        return redirect(url_for('login'))

    form = request.form
    files = request.files

    image_paths = {}
    for key in files:
        if files[key].filename != '':
            filename = f"temp_{key}.jpg"
            path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            files[key].save(path)
            image_paths[key] = filename

    return render_template('create_serial.html',
                           motor_current=form.get('motor_current', ''),
                           gear_sound=form.get('gear_sound', ''),
                           check_complete=form.get('check_complete', ''),
                           incomplete_reason=form.get('incomplete_reason', ''),
                           warranty=form.get('warranty', ''),
                           inspector=form.get('inspector', ''),
                           image_paths=image_paths)

@app.route('/generate_serial', methods=['POST'])
def generate_serial_and_qr():
    if 'staff_id' not in session:
        return redirect(url_for('login'))

    form = request.form
    serial = generate_serial()
    qr_path = create_qr(serial)
    now = datetime.now()

    warranty_days = 18 * 30 if form.get('warranty') == '18' else 24 * 30
    warranty_start = now + timedelta(days=5)
    warranty_end = warranty_start + timedelta(days=warranty_days)

    image_keys = ['motor_current_img', 'gear_sound_img', 'assembly_img', 'check_complete_img']
    with open(f'static/{serial}_info.txt', 'w') as f:
        f.write(warranty_start.strftime('%Y-%m-%d') + '\n')
        f.write(str(warranty_days) + '\n')
        f.write(form.get('inspector', '') + '\n')
        f.write(now.strftime('%Y-%m-%d') + '\n')
        for key in image_keys:
            filename = f"temp_{key}.jpg"
            if os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], filename)):
                new_filename = f"{serial}_{key}.jpg"
                os.rename(os.path.join(app.config['UPLOAD_FOLDER'], filename),
                          os.path.join(app.config['UPLOAD_FOLDER'], new_filename))
                f.write(new_filename + '\n')
            else:
                f.write('\n')

    pdf_path = f'static/{serial}_report.pdf'
    c = canvas.Canvas(pdf_path, pagesize=A4)
    c.setFont('THSarabunNew', 16)
    c.drawImage("static/logo_sas.png", 430, 770, width=120, height=50)

    c.setFont("THSarabunNew", 18)
    c.drawString(30, 800, "SAS QC Gear Motor")
    c.setFont("THSarabunNew", 16)
    c.drawString(30, 780, f"Serial No.: {serial}")
    c.drawString(30, 765, f"ตรวจสอบวันที่: {now.strftime('%d-%m-%Y')}")

    y = 730
    def draw_line(text):
        nonlocal y
        c.drawString(30, y, text)
        y -= 22

    draw_line(f"1. ค่ากระแสมอเตอร์: {form.get('motor_current', '-')} A")
    draw_line(f"2. ตรวจเสียงหัวเกียร์: {form.get('gear_sound', '-')}")
    draw_line(f"3. ประกอบ Gear + Motor: เสร็จสิ้น")
    draw_line(f"4. ตรวจสอบครบถ้วน: {form.get('check_complete', '-')}")
    if form.get('check_complete') == 'ไม่ถูกต้อง':
        draw_line(f"เหตุผล: {form.get('incomplete_reason', '-')}")
    draw_line(f"5. การรับประกัน: {form.get('warranty', '-')} เดือน")
    draw_line(f"6. ผู้ตรวจสอบ: {form.get('inspector', '-')}")
    draw_line(f"7. รับประกันถึง: {warranty_end.strftime('%d-%m-%Y')}")

    c.drawImage(qr_path, 430, y - 100, width=100, height=100)
    c.save()

    return redirect(url_for('show_customer_report', serial=serial))

@app.route('/customer_report/<serial>')
def show_customer_report(serial):
    with open(f'static/{serial}_info.txt', 'r') as f:
        lines = f.read().splitlines()
        warranty_start = lines[0]
        warranty_days = int(lines[1])
        inspector = lines[2]
        check_date = lines[3]
    return render_template('customer_report.html',
                           serial=serial,
                           inspector=inspector,
                           check_date=check_date)

@app.route('/options/<serial>', methods=['GET'])
def show_pdf_options(serial):
    if 'staff_id' not in session:
        return redirect(url_for('login'))
    return render_template('download_email.html', serial=serial)

@app.route('/download/<serial>', methods=['GET'])
def download_pdf(serial):
    pdf_path = f'static/{serial}_report.pdf'
    return send_file(pdf_path, as_attachment=True)

@app.route('/download_qr/<serial>', methods=['GET'])
def download_qr(serial):
    qr_path = f'static/qr_codes/{serial}.png'
    return send_file(qr_path, as_attachment=True)

@app.route('/send_email/<serial>', methods=['POST'])
def send_email(serial):
    recipient = request.form.get('email')
    pdf_path = f'static/{serial}_report.pdf'

    msg = EmailMessage()
    msg['Subject'] = f'SAS QC Report: {serial}'
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = recipient
    msg.set_content('แนบรายงานการตรวจสอบ SAS QC Gear Motor ครับ')

    with open(pdf_path, 'rb') as f:
        msg.add_attachment(f.read(), maintype='application', subtype='pdf', filename=f'{serial}_report.pdf')

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        smtp.send_message(msg)

    return f"ส่งอีเมลไปยัง {recipient} สำเร็จแล้วครับ"