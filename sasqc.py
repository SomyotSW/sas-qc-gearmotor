# sasqc.py
from flask import Flask, render_template, request, redirect, send_file, url_for
import os
from datetime import datetime, timedelta
import qrcode
import random
import string
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import smtplib
from email.message import EmailMessage

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['QR_FOLDER'] = 'static/qr_codes'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['QR_FOLDER'], exist_ok=True)

EMAIL_ADDRESS = "your_email@example.com"
EMAIL_PASSWORD = "your_email_password"

def generate_serial():
    return ''.join(random.choices(string.digits, k=10))

def create_qr(serial):
    img = qrcode.make(url_for('show_customer_report', serial=serial, _external=True))
    qr_path = os.path.join(app.config['QR_FOLDER'], f'{serial}.png')
    img.save(qr_path)
    return qr_path

@app.route('/', methods=['GET'])
def index():
    return render_template('form.html')

@app.route('/submit', methods=['POST'])
def submit_form():
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
                           motor_current=form['motor_current'],
                           gear_sound=form['gear_sound'],
                           check_complete=form['check_complete'],
                           incomplete_reason=form.get('incomplete_reason', ''),
                           warranty=form['warranty'],
                           inspector=form['inspector'],
                           image_paths=image_paths)

@app.route('/generate_serial', methods=['POST'])
def generate_serial_and_qr():
    form = request.form

    serial = generate_serial()
    qr_path = create_qr(serial)
    now = datetime.now()

    warranty_days = 18 * 30 if form['warranty'] == '18' else 24 * 30
    warranty_start = now + timedelta(days=5)
    warranty_end = warranty_start + timedelta(days=warranty_days)

    image_keys = ['motor_current_img', 'gear_sound_img', 'assembly_img', 'check_complete_img']
    with open(f'static/{serial}_info.txt', 'w') as f:
        f.write(warranty_start.strftime('%Y-%m-%d') + '\n')
        f.write(str(warranty_days) + '\n')
        f.write(form['inspector'] + '\n')
        f.write(now.strftime('%Y-%m-%d') + '\n')
        for key in image_keys:
            filename = f"temp_{key}.jpg"
            if os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], filename)):
                new_filename = f"{serial}_{key}.jpg"
                os.rename(os.path.join(app.config['UPLOAD_FOLDER'], filename), os.path.join(app.config['UPLOAD_FOLDER'], new_filename))
                f.write(new_filename + '\n')
            else:
                f.write('\n')

    pdf_path = f'static/{serial}_report.pdf'
    c = canvas.Canvas(pdf_path, pagesize=A4)
    c.drawImage("logo_sas.png", 430, 770, width=120, height=50)

    c.setFont("Helvetica-Bold", 14)
    c.drawString(30, 800, "SAS QC Gear Motor")
    c.setFont("Helvetica", 12)
    c.drawString(30, 780, f"Serial No.: {serial}")
    c.drawString(30, 765, f"ตรวจสอบวันที่: {now.strftime('%d-%m-%Y')}")

    y = 730
    def draw_line(text):
        nonlocal y
        c.drawString(30, y, text)
        y -= 20

    draw_line(f"1. ค่ากระแสมอเตอร์: {form['motor_current']} A")
    draw_line(f"2. ตรวจเสียงหัวเกียร์: {form['gear_sound']}")
    draw_line(f"3. ประกอบ Gear + Motor: เสร็จสิ้น")
    draw_line(f"4. ตรวจสอบครบถ้วน: {form['check_complete']}")
    if form['check_complete'] == 'ไม่ถูกต้อง':
        draw_line(f"เหตุผล: {form['incomplete_reason']}")
    draw_line(f"5. การรับประกัน: {form['warranty']} เดือน")
    draw_line(f"6. ผู้ตรวจสอบ: {form['inspector']}")
    draw_line(f"7. รับประกันถึง: {warranty_end.strftime('%d-%m-%Y')}")

    c.drawImage(qr_path, 430, y - 100, width=100, height=100)
    c.save()

    return redirect(url_for('show_pdf_options', serial=serial))

@app.route('/report/<serial>')
def report(serial):
    pdf_path = f'static/{serial}_report.pdf'
    if not os.path.exists(pdf_path):
        return f"ไม่พบรายงานสำหรับ Serial: {serial}", 404
    return send_file(pdf_path, as_attachment=False)

@app.route('/pdf/<serial>', methods=['GET', 'POST'])
def show_pdf_options(serial):
    if request.method == 'POST':
        recipient = request.form['email']
        pdf_path = f'static/{serial}_report.pdf'

        msg = EmailMessage()
        msg['Subject'] = f'SAS QC Report - Serial {serial}'
        msg['From'] = EMAIL_ADDRESS
        msg['To'] = recipient
        msg.set_content(f'แนบรายงาน QC สำหรับ Serial No. {serial}')

        with open(pdf_path, 'rb') as f:
            file_data = f.read()
            file_name = os.path.basename(pdf_path)

        msg.add_attachment(file_data, maintype='application', subtype='pdf', filename=file_name)

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp.send_message(msg)

        return f"ส่งอีเมลเรียบร้อยแล้วไปยัง {recipient}"

    return f'''
        <h2>Serial No.: {serial}</h2>
        <p>ดาวน์โหลด PDF รายงานการตรวจสอบ:</p>
        <a href="{url_for('report', serial=serial)}" target="_blank">
            <button>⬇️ ดาวน์โหลด PDF</button>
        </a>
        <hr>
        <form method="POST">
            <label>กรอก Email ผู้รับ:</label><br>
            <input type="email" name="email" required><br><br>
            <button type="submit">📧 ส่ง Email พร้อมแนบ PDF</button>
        </form>
    '''

@app.route('/qc/<serial>')
def show_customer_report(serial):
    pdf_path = f'static/{serial}_report.pdf'
    info_path = f'static/{serial}_info.txt'

    if not os.path.exists(pdf_path) or not os.path.exists(info_path):
        return "ไม่พบข้อมูลการรับประกันหรือตรวจสอบ"

    with open(info_path, 'r') as f:
        lines = f.readlines()
        warranty_start = datetime.strptime(lines[0].strip(), '%Y-%m-%d')
        warranty_days = int(lines[1].strip())
        inspector = lines[2].strip()
        inspection_date = lines[3].strip()
        image_files = [line.strip() for line in lines[4:]]

    warranty_end = warranty_start + timedelta(days=warranty_days)
    today = datetime.now()
    days_left = (warranty_end - today).days

    if days_left > 0:
        status = f"ยังอยู่ในระยะรับประกัน (เหลืออีก {days_left} วัน)"
    else:
        status = "สิ้นสุดการรับประกันแล้ว"

    image_html = ""
    image_titles = ["ค่ากระแสมอเตอร์", "เสียงหัวเกียร์", "การประกอบ", "การตรวจสอบครบถ้วน"]
    for title, img in zip(image_titles, image_files):
        if img:
            image_html += f"<h4>{title}</h4><img src='/static/uploads/{img}' width='300'><br><br>"

    return f'''
        <h2>Serial No.: {serial}</h2>
        <p>สถานะ: {status}</p>
        <p>ผู้ตรวจสอบ: {inspector}</p>
        <p>วันที่ตรวจสอบ: {inspection_date}</p>
        <a href="{url_for('report', serial=serial)}" target="_blank">
            <button>📄 เปิดรายงาน QC</button>
        </a>
        <hr>
        {image_html}
    '''

if __name__ == '__main__':
    app.run(debug=True)