"""Compose original-colour evidence figures for the deployment runbook."""
from PIL import Image, ImageDraw, ImageFont

FIG = "C:/Users/ADMIN/Downloads/BI&DSS/output_10/figures/"
FONT = ImageFont.truetype("C:/Windows/Fonts/timesbd.ttf", 22)


def labeled(img, label):
    out = Image.new("RGB", (img.width, img.height + 36), "white")
    out.paste(img, (0, 36))
    ImageDraw.Draw(out).text((6, 4), label, font=FONT, fill="black")
    return out


def border(img):
    ImageDraw.Draw(img).rectangle((0, 0, img.width - 1, img.height - 1), outline="black", width=2)
    return img


before = border(Image.open(FIG + "s3a_dss_before.png").convert("RGB").crop((0, 0, 1002, 680)))
after = border(Image.open(FIG + "s3d_dss_after.png").convert("RGB").crop((0, 0, 1002, 680)))
a, b = labeled(before, "(a) Trước khi Change Source"), labeled(after, "(b) Sau khi Change Source")
fig = Image.new("RGB", (a.width + b.width + 30, a.height), "white")
fig.paste(a, (0, 0)); fig.paste(b, (a.width + 30, 0))
fig.save(FIG + "fig_change_source.png", dpi=(200, 200))

p1 = Image.open(FIG + "s4e_after_refresh_p1.png").convert("RGB").crop((86, 214, 1412, 950))
p1 = labeled(border(p1), "(a) Trang P1 Overview sau Refresh, ô Last Refresh = 14/11/2026 6:29:08 CH")
rows = [Image.open(FIG + f).convert("RGB").crop((10, 1004, 520, 1028)) for f in
        ("s4c_factflight_rows.png", "s4d_factdelay_rows.png")]
strip = Image.new("RGB", (520, 60), "white")
strip.paste(rows[0], (5, 4)); strip.paste(rows[1], (5, 32))
strip = strip.resize((strip.width * 2, strip.height * 2))
strip = labeled(border(strip), "(b) Thanh trạng thái Table view")
fig2 = Image.new("RGB", (p1.width, p1.height + strip.height + 20), "white")
fig2.paste(p1, (0, 0)); fig2.paste(strip, (0, p1.height + 20))
fig2.save(FIG + "fig_refresh_result.png", dpi=(200, 200))
print(fig.size, fig2.size)
