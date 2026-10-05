"""Render the app's code-native document/wave mark at all Windows icon sizes."""
from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1] / "assets"
root.mkdir(exist_ok=True)
scale = 4
image = Image.new("RGBA", (256 * scale, 256 * scale))
d = ImageDraw.Draw(image)
def box(bounds, radius, fill):
    d.rounded_rectangle(tuple(int(x * scale) for x in bounds), radius=int(radius * scale), fill=fill)
box((5, 5, 251, 251), 60, "#102238")
box((16, 16, 240, 240), 52, "#17334A")
box((64, 41, 194, 209), 23, "#EAFBF8")
d.polygon([(155 * scale, 41 * scale), (194 * scale, 80 * scale), (155 * scale, 80 * scale)], fill="#B8E6DF")
for x, height in ((87, 22), (105, 40), (123, 65), (141, 45), (159, 25)):
    box((x, 110 - height / 2, x + 10, 110 + height / 2), 5, "#1F9E92")
box((88, 159, 166, 167), 4, "#557F90")
box((88, 178, 139, 186), 4, "#A4C6CF")
box((168, 174, 239, 245), 24, "#65E3C1")
d.line([(189 * scale, 210 * scale), (200 * scale, 221 * scale), (221 * scale, 198 * scale)], fill="#103548", width=8 * scale, joint="curve")
image = image.resize((256, 256), Image.Resampling.LANCZOS)
image.save(root / "app.png")
image.save(root / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
