from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from io import BytesIO
from PIL import Image
import requests
from reportlab.lib.utils import ImageReader

def create_qc_pdf(data, image_urls):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2 * cm, height - 2 * cm, "SAS QC Gear Motor Report")

    c.setFont("Helvetica", 12)
    c.drawString(2 * cm, height - 3 * cm, f"Serial Number: {data.get('serial', '-')}")
    c.drawString(2 * cm, height - 4 * cm, f"Inspector: {data.get('inspector', '-')}")
    c.drawString(2 * cm, height - 5 * cm, f"Product Type: {data.get('product_type', '-')}")
    c.drawString(2 * cm, height - 6 * cm, f"Motor Nameplate: {data.get('motor_nameplate', '-')}")
    c.drawString(2 * cm, height - 7 * cm, f"Gear Ratio: {data.get('gear_ratio', '-')}")
    c.drawString(2 * cm, height - 8 * cm, f"Gear Sound: {data.get('gear_sound', '-')}")
    c.drawString(2 * cm, height - 9 * cm, f"Current: {data.get('motor_current', '-')}")
    c.drawString(2 * cm, height - 10 * cm, f"Oil: {data.get('oil_liters', '-')} L - {data.get('oil_filled', '-')}")
    c.drawString(2 * cm, height - 11 * cm, f"Warranty: {data.get('warranty', '-')} months")

    c.showPage()

    for url in image_urls:
        try:
            response = requests.get(url)
            img = Image.open(BytesIO(response.content))
            img.thumbnail((500, 500))
            img_io = BytesIO()
            img.save(img_io, format='PNG')
            img_io.seek(0)
            c.drawImage(ImageReader(img_io), 2 * cm, height / 2, width=15 * cm, preserveAspectRatio=True)
            c.showPage()
        except Exception as e:
            print(f"Error loading image: {e}")
            continue

    c.save()
    buffer.seek(0)
    return buffer