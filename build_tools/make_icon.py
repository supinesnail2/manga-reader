"""Draws the red seal app icon. Usage: make_icon.py <output.ico>"""
import sys

from PIL import Image, ImageDraw, ImageFont

SIZE = 512
FONT = r"C:\Windows\Fonts\YuGothB.ttc"

image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((16, 16, SIZE - 16, SIZE - 16), radius=88, fill=(229, 72, 77, 255))
draw.text((SIZE / 2, SIZE / 2), "\u6f2b", font=ImageFont.truetype(FONT, 340), fill="white", anchor="mm")
image.save(sys.argv[1], sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
