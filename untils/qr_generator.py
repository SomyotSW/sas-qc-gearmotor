import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from io import BytesIO

def generate_qr_pdf(serial):
    # URL ที่จะฝังใน QR Code
    check_url = f"https://sas-qc-gearmotor.vercel.app/check?serial={serial}"

    # สร้าง QR Image
    qr_img = qrcode.make(check_url)
    qr_stream = BytesIO()
    qr_img.save(qr_stream, format='PNG')
    qr_stream.seek(0)

    # สร้าง PDF พร้อม QR
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2 * 72, height - 72, "SAS QC Gear Motor - QR Code")

    c.drawImage(qr_stream, 2 * 72, height / 2, width=200, height=200)

    c.setFont("Helvetica", 12)
    c.drawString(2 * 72, height / 2 - 30, f"Serial: {serial}")
    c.drawString(2 * 72, height / 2 - 50, f"Link: {check_url}")

    c.save()
    buffer.seek(0)
    return buffer