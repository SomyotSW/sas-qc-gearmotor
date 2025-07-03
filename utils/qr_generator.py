import io
import qrcode

def generate_qr_code(serial_number):
    url = f"https://sas-qc-gearmotor-app.web.app/view?serial={serial_number}"  # <== ใส่ URL จริง
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(fill='black', back_color='white')
    qr_stream = io.BytesIO()
    img.save(qr_stream, format='PNG')
    qr_stream.seek(0)
    return qr_stream