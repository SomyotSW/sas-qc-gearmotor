import io
import qrcode

def generate_qr_code(serial, pdf_url):
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(pdf_url)  # ใช้ลิงก์ตรงไปยัง PDF URL ที่เปิดเผย
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    qr_stream = io.BytesIO()
    img.save(qr_stream, format='PNG')
    qr_stream.seek(0)
    return qr_stream
