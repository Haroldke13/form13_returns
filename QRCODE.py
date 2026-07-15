import qrcode
from PIL import Image

# =========================
# CONFIG
# =========================
url = "https://returnsform14.org/form/pbo-week"

output_file = "pbolandingpage.jpg"

# =========================
# CREATE QR CODE
# =========================
qr = qrcode.QRCode(
    version=None,  # automatic size
    error_correction=qrcode.constants.ERROR_CORRECT_H,
    box_size=12,
    border=2
)

qr.add_data(url)
qr.make(fit=True)

# Create image (black QR on white background first)
img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")

# =========================
# MAKE BACKGROUND TRANSPARENT
# =========================
datas = img.getdata()
new_data = []

for item in datas:
    # Detect white pixels → make transparent
    if item[0] > 240 and item[1] > 240 and item[2] > 240:
        new_data.append((255, 255, 255, 0))  # transparent
    else:
        new_data.append(item)

img.putdata(new_data)

# =========================
# SAVE AS JPG (with white fallback)
# =========================
# JPG doesn't support transparency → convert properly
background = Image.new("RGB", img.size, (255, 255, 255))
background.paste(img, mask=img.split()[3])  # apply transparency mask

background.save(output_file, "JPEG", quality=100)

print(f"✅ QR code saved as {output_file}")
