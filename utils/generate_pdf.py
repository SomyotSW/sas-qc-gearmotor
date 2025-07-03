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
    y = height - 3 * cm
    line_height = 1 * cm

    # Helper function to draw line safely
    def draw_safe_line(label, value):
        nonlocal y
        value = value if value not in [None, ""] else "-"
        c.drawString(2 * cm, y, f"{label}: {value}")
        y -= line_height

    draw_safe_line("Serial Number", data.get("serial"))
    draw_safe_line("Inspector", data.get("inspector"))
    draw_safe_line("Product Type", data.get("product_type"))
    draw_safe_line("Motor Nameplate", data.get("motor_nameplate"))
    draw_safe_line("Motor Current (A)", data.get("motor_current"))
    draw_safe_line("Gear Ratio", f"1:{data.get('gear_ratio', '-')}")
    draw_safe_line("Gear Sound", data.get("gear_sound"))
    draw_safe_line("Oil Filled", data.get("oil_filled"))
    draw_safe_line("Oil Liters", data.get("oil_liters"))
    draw_safe_line("Warranty (months)", data.get("warranty"))
    draw_safe_line("Date", data.get("date"))

    c.showPage()

    # Add images
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