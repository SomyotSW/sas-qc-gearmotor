# utils/qr_generator.py

import qrcode
from io import BytesIO
from base64 import b64encode

def generate_qr_code(data):
    """
    Generate a base64-encoded PNG QR code from the given data.
    
    Args:
        data (str): The string to encode into a QR code.

    Returns:
        str: Base64 string of the PNG image.
    """
    qr = qrcode.QRCode(
        version=1,
        box_size=10,
        border=5
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    img_base64 = b64encode(buffer.read()).decode('utf-8')
    return img_base64