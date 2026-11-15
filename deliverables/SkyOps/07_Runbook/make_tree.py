"""Render the SkyOps package folder tree as a black-and-white Tree View diagram."""
from PIL import Image, ImageDraw, ImageFont

OUT = r"C:/Users/ADMIN/Downloads/BI&DSS/output_10/figures/fig_folder_tree.png"
FONT_DIR = "C:/Windows/Fonts/"
SCALE = 2
ROW_H, INDENT, X0, Y0 = 52 * SCALE, 30 * SCALE, 12 * SCALE, 74 * SCALE
COL_FMT, COL_FUNC, WIDTH = 610 * SCALE, 745 * SCALE, 1340 * SCALE

# (depth, name, is_folder, format, function)
ROWS = [
    (0, "SkyOps/", True, "Thư mục", "Gói bàn giao, giải nén vào C:\SkyOps"),
    (1, "README.md", False, "Markdown", "Mô tả gói và Runbook triển khai 4 bước"),
    (1, "00_Data/", True, "CSV UTF-8", "10 bảng CSV: 6 Dim, 2 Fact, 2 DSS"),
    (2, "_manifest.json", False, "JSON", "Số dòng, cột, phạm vi ngày"),
    (1, "01_Solution/", True, "", "(1) Solution File"),
    (2, "SkyOps.pbix", False, "Power BI", "Mô hình dữ liệu, 5 trang dashboard"),
    (1, "02_Data_Model_Documentation/", True, "", "(2) Data & Model Documentation"),
    (2, "SkyOps_Data_Model_Specification.docx", False, "Word", "Đặc tả Star Schema"),
    (2, "SkyOps_Data_Dictionary.csv", False, "CSV", "Từ điển dữ liệu"),
    (2, "SkyOps_DAX_Measures.dax", False, "DAX", "Mã nguồn 26 measure"),
    (2, "SkyOps_Model_Relationships.csv", False, "CSV", "12 quan hệ, chiều lọc"),
    (1, "03_Quick_User_Guide/", True, "", "(3) Quick User Guide"),
    (2, "SkyOps_Quick_User_Guide.pdf", False, "PDF", "Cẩm nang sử dụng nhanh"),
    (1, "04_Test_UAT_Report/", True, "", "(4) Test & UAT Report"),
    (2, "SkyOps_Test_DoiSoatDuLieu_9.1.docx", False, "Word", "Đối soát chỉ số DAX và SQL"),
    (2, "SkyOps_Test_GiaoDien_HieuNang_9.2.2.docx", False, "Word", "Kiểm thử giao diện, hiệu năng"),
    (2, "SkyOps_UAT_PhuLucD.pdf", False, "PDF ký duyệt", "Biên bản nghiệm thu Phụ lục D"),
    (1, "05_Limitations_Known_Issues/", True, "", "(5) Limitations & Known Issues"),
    (2, "SkyOps_Limitations_Known_Issues.docx", False, "Word", "Hạn chế tồn đọng, hướng xử lý"),
    (1, "06_Presentation/", True, "", "(6) Presentation Slide Deck"),
    (2, "SkyOps_Slide_Deck.pptx", False, "PowerPoint", "Slide thuyết trình"),
    (2, "SkyOps_Demo_Script.docx", False, "Word", "Kịch bản demo 12–15 phút"),
]


def font(name, size):
    return ImageFont.truetype(FONT_DIR + name, size * SCALE)


def main():
    reg, bold, ital = font("times.ttf", 22), font("timesbd.ttf", 22), font("timesi.ttf", 21)
    head = font("timesbd.ttf", 23)
    h = Y0 + ROW_H * len(ROWS) + 20 * SCALE
    img = Image.new("RGB", (WIDTH, h), "white")
    d = ImageDraw.Draw(img)
    line = 2 * SCALE
    d.text((X0, 22 * SCALE), "Cấu trúc thư mục", font=head, fill="black")
    d.text((COL_FMT, 22 * SCALE), "Định dạng", font=head, fill="black")
    d.text((COL_FUNC, 22 * SCALE), "Chức năng", font=head, fill="black")
    d.line((X0, 54 * SCALE, WIDTH - X0, 54 * SCALE), fill="black", width=line)

    last_y = {}  # depth -> y of last node seen at that depth (for vertical connectors)
    for i, (depth, name, is_folder, fmt, func) in enumerate(ROWS):
        y = Y0 + i * ROW_H + ROW_H // 2
        x = X0 + depth * INDENT
        if depth > 0:
            px = X0 + (depth - 1) * INDENT + 9 * SCALE
            d.line((px, last_y[depth - 1] + 12 * SCALE, px, y), fill="black", width=line)
            d.line((px, y, x - 4 * SCALE, y), fill="black", width=line)
        # icon: folder = filled-outline tab box, file = page outline
        if is_folder:
            d.rectangle((x, y - 8 * SCALE, x + 20 * SCALE, y + 8 * SCALE), outline="black", width=line, fill="#d9d9d9")
            d.rectangle((x, y - 12 * SCALE, x + 9 * SCALE, y - 8 * SCALE), outline="black", width=line, fill="#d9d9d9")
        else:
            d.rectangle((x + 3 * SCALE, y - 13 * SCALE, x + 17 * SCALE, y + 9 * SCALE), outline="black", width=line)
        d.text((x + 28 * SCALE, y - 13 * SCALE), name, font=bold if is_folder else reg, fill="black")
        d.text((COL_FMT, y - 13 * SCALE), fmt, font=reg, fill="black")
        d.text((COL_FUNC, y - 13 * SCALE), func, font=bold if func.startswith("(") else reg, fill="black")
        last_y[depth] = y
        for deeper in [k for k in last_y if k > depth]:
            del last_y[deeper]
    d.line((X0, h - 12 * SCALE, WIDTH - X0, h - 12 * SCALE), fill="black", width=line)
    img.save(OUT, dpi=(300, 300))
    print(OUT, img.size)


if __name__ == "__main__":
    main()
