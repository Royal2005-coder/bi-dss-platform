# -*- coding: utf-8 -*-
"""Build Word deliverable: 10.1 package folder tree, 10.2 deployment runbook."""
from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = "C:/Users/ADMIN/Downloads/BI&DSS/output_10"
FONT, BODY, TBL, CAP = "Times New Roman", 13, 12, 12
L, C, J = WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.JUSTIFY
TEXT_W = 16.0

doc = Document()
sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21), Cm(29.7)
sec.left_margin, sec.right_margin, sec.top_margin, sec.bottom_margin = Cm(3), Cm(2), Cm(2), Cm(2)
normal = doc.styles["Normal"]
normal.font.name, normal.font.size = FONT, Pt(BODY)
normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)


def run(p, text, size=BODY, bold=False, italic=False):
    r = p.add_run(text)
    r.font.name, r.font.size, r.bold, r.italic = FONT, Pt(size), bold, italic
    r.font.color.rgb = RGBColor(0, 0, 0)
    rf = r._element.get_or_add_rPr().get_or_add_rFonts()
    for k in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rf.set(qn(k), FONT)
    return r


def para(text="", align=J, indent=True, size=BODY, bold=False, italic=False, before=0, after=6, keep=False):
    p = doc.add_paragraph()
    p.alignment = align
    f = p.paragraph_format
    f.first_line_indent = Cm(1) if indent else Cm(0)
    f.space_before, f.space_after, f.line_spacing, f.keep_with_next = Pt(before), Pt(after), 1.3, keep
    if text:
        run(p, text, size, bold, italic)
    return p


def heading(text, level, page_break=False):
    p = para(text, L, False, 14 if level == 1 else 13, True, before=12 if level == 1 else 8, after=6, keep=True)
    p.paragraph_format.page_break_before = page_break
    o = OxmlElement("w:outlineLvl")
    o.set(qn("w:val"), str(level - 1))
    p._p.get_or_add_pPr().append(o)


def caption(text, keep):
    return para(text, C, False, CAP, False, True, before=4, after=8, keep=keep)


def figure(path, width_cm):
    p = para(align=C, indent=False, after=2, keep=True)
    p.add_run().add_picture(path, width=Cm(width_cm))


def table(head, rows, widths, aligns):
    t = doc.add_table(rows=1 + len(rows), cols=len(head))
    t.alignment, t.autofit = WD_TABLE_ALIGNMENT.CENTER, False
    pr = t._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement("w:" + edge)
        for k, v in (("val", "single"), ("sz", "6"), ("color", "000000")):
            e.set(qn("w:" + k), v)
        borders.append(e)
    pr.append(borders)
    lay = OxmlElement("w:tblLayout")
    lay.set(qn("w:type"), "fixed")
    pr.append(lay)
    amap = {"L": L, "C": C, "R": WD_ALIGN_PARAGRAPH.RIGHT}
    for ri, row in enumerate([head] + rows):
        tr = t.rows[ri]
        tr._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        if ri == 0:
            tr._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
        for ci, val in enumerate(row):
            cell = tr.cells[ci]
            cell.width, cell.vertical_alignment = Cm(widths[ci]), WD_ALIGN_VERTICAL.CENTER
            for li, line in enumerate(str(val).split("\n")):
                p = cell.paragraphs[0] if li == 0 else cell.add_paragraph()
                p.paragraph_format.space_after, p.paragraph_format.line_spacing = Pt(2), 1.1
                p.alignment = C if ri == 0 else amap[aligns[ci]]
                run(p, line, TBL, bold=ri == 0)
            mar = OxmlElement("w:tcMar")
            for k, w in (("top", 50), ("bottom", 50), ("start", 80), ("end", 80)):
                n = OxmlElement("w:" + k)
                n.set(qn("w:w"), str(w))
                n.set(qn("w:type"), "dxa")
                mar.append(n)
            cell._tc.get_or_add_tcPr().append(mar)
    return t


# ---------------------------------------------------------------- 10.1
heading("10.1. Đóng gói sản phẩm bàn giao", 1)
para("Toàn bộ sản phẩm đồ án được đóng gói trong một thư mục gốc duy nhất là SkyOps. Thư mục 00_Data chứa dữ liệu "
     "nạp vào mô hình, sáu thư mục từ 01 đến 06 tương ứng với sáu thành phần bàn giao bắt buộc. Tiền tố số giữ thứ "
     "tự hiển thị cố định trong File Explorer. Tên tệp dùng tiền tố SkyOps_ và không chứa dấu cách để tránh lỗi đường "
     "dẫn khi chép sang máy khác. Hình 10.1 trình bày sơ đồ cây thư mục kèm định dạng và chức năng của từng tệp.")
figure(ROOT + "/figures/fig_folder_tree.png", TEXT_W)
caption("Hình 10.1. Sơ đồ cây thư mục gói bàn giao SkyOps", keep=False)
para("Bảng 10.1 thuyết minh vai trò của sáu thành phần. Tệp README.md ở thư mục gốc là điểm vào của gói, liệt kê nội "
     "dung từng thư mục và tóm tắt Runbook triển khai được trình bày chi tiết ở mục 10.2.")
caption("Bảng 10.1. Thuyết minh chức năng sáu thành phần bàn giao", keep=True)
table(["TT", "Thành phần", "Vị trí trong gói", "Chức năng"], [
    ["1", "Solution File", "01_Solution/\nSkyOps.pbix",
     "Tệp giải pháp chứa mô hình dữ liệu đã nạp (FactFlight 1.151.344 dòng, FactDelay 397.131 dòng), 26 measure "
     "và 5 trang dashboard: P1 Overview, P2 Airport & Route, P3 Delay Cause, P4 Time Pattern, P5 Risk & Priority."],
    ["2", "Data & Model Documentation", "02_Data_Model_\nDocumentation/",
     "Đặc tả Star Schema gồm 6 bảng Dim và 2 bảng Fact, từ điển dữ liệu theo bảng và cột, mã nguồn DAX, danh sách "
     "12 quan hệ kèm chiều lọc. Đây là tài liệu tra cứu khi bảo trì hoặc mở rộng mô hình."],
    ["3", "Quick User Guide", "03_Quick_User_Guide/",
     "Cẩm nang ngắn cho người dùng nghiệp vụ: điều hướng 5 trang, dùng slicer và nút Reset, đọc KPI và bảng ưu tiên DSS."],
    ["4", "Test & UAT Report", "04_Test_UAT_Report/",
     "Kết quả đối soát chỉ số giữa DAX và SQL (mục 9.1), kiểm thử giao diện và hiệu năng (mục 9.2.2), biên bản "
     "nghiệm thu theo Phụ lục D có chữ ký xác nhận."],
    ["5", "Limitations & Known Issues", "05_Limitations_\nKnown_Issues/",
     "Danh mục hạn chế còn tồn đọng, mức ảnh hưởng và hướng xử lý, giúp người tiếp nhận biết trước giới hạn của sản phẩm."],
    ["6", "Presentation Slide Deck", "06_Presentation/",
     "Slide thuyết trình kết quả đồ án và kịch bản demo 12 đến 15 phút, phân bổ thời gian theo từng trang dashboard."],
], [1.0, 3.1, 3.7, 8.2], ["C", "L", "L", "L"])

# ---------------------------------------------------------------- 10.2
heading("10.2. Runbook triển khai hệ thống trên máy mới", 1, page_break=True)
para("Tệp SkyOps.pbix nạp dữ liệu từ các tệp CSV theo đường dẫn tuyệt đối. Bản PBIX ban đầu của nhóm trỏ tới "
     "C:\\Users\\Admin\\Downloads đối với 8 bảng mô hình sao và C:\\Users\\vomin\\Downloads\\SkyOps_8_4_handover_v14"
     "\\outputs đối với 2 bảng DSS. Trên một máy khác, các đường dẫn này không tồn tại nên thao tác Refresh báo lỗi "
     "không tìm thấy tệp. Runbook dưới đây chuẩn hóa quy trình bốn bước để triển khai lại hệ thống trên máy mới.")
para("Runbook đã được thực hiện thực tế ngày 14/11/2026 trên một máy Windows 11 không có các thư mục nêu trên, dùng "
     "Power BI Desktop phiên bản 2.159.1231.0. Bảng 10.2 là checklist thao tác. Người triển khai đánh dấu cột cuối khi "
     "bước tương ứng đạt kết quả kiểm tra.")
caption("Bảng 10.2. Checklist 4 bước triển khai SkyOps trên máy mới", keep=True)
table(["Bước", "Thao tác chi tiết", "Kết quả kiểm tra", "Đạt"], [
    ["1. Cài đặt Power BI Desktop",
     "Cài Power BI Desktop bản 64-bit từ Microsoft Store hoặc trang tải của Microsoft, trên Windows 10 hoặc 11.\n"
     "Mở ứng dụng, chọn Help → About để ghi lại phiên bản.\n"
     "Nên dùng máy có tối thiểu 8 GB RAM vì mô hình nạp khoảng 2,7 triệu dòng dữ liệu.",
     "Power BI Desktop khởi động bình thường, xem được số phiên bản.", "□"],
    ["2. Đặt thư mục dữ liệu vào máy cục bộ",
     "Giải nén gói bàn giao vào C:\\SkyOps để có thư mục C:\\SkyOps\\00_Data.\n"
     "Kiểm tra đủ 10 tệp CSV theo Bảng 10.3, tổng dung lượng khoảng 290 MB.\n"
     "Không mở rồi lưu lại CSV bằng Excel vì có thể làm đổi mã hóa UTF-8 và định dạng ngày.\n"
     "Nếu lấy dữ liệu từ GitHub, chạy git lfs pull để FactFlight.csv là tệp dữ liệu thật, không phải tệp con trỏ LFS.",
     "Thư mục 00_Data có đủ 10 tệp, FactFlight.csv khoảng 132 MB.", "□"],
    ["3. Trỏ Data Source Settings đến CSV cục bộ",
     "Mở 01_Solution\\SkyOps.pbix.\n"
     "Chọn Home → Transform data ▾ → Data source settings, giữ tùy chọn Data sources in current file.\n"
     "Chọn từng nguồn → Change Source… → nhập C:\\SkyOps\\00_Data\\<Tên tệp>.csv vào ô File path, giữ File origin "
     "65001: Unicode (UTF-8) và Delimiter Comma → OK.\n"
     "Lặp lại cho đủ 10 nguồn rồi chọn Close. Nguồn FactFlight.txt cũ được đổi thành FactFlight.csv.",
     "Danh sách nguồn chỉ còn đường dẫn c:\\skyops\\00_data\\…, không còn c:\\users\\….", "□"],
    ["4. Refresh nạp lại dữ liệu",
     "Khi thanh vàng There are pending changes xuất hiện, chọn Apply changes.\n"
     "Chọn Home → Refresh ▾ → Data để nạp lại toàn bộ bảng.\n"
     "Mở Table view, chọn FactFlight và FactDelay để đọc số dòng ở thanh trạng thái.\n"
     "Lưu tệp bằng Ctrl+S.",
     "FactFlight 1.151.344 dòng, FactDelay 397.131 dòng. Ô Last Refresh hiển thị thời điểm vừa nạp.", "□"],
], [3.0, 7.6, 4.3, 1.1], ["L", "L", "L", "C"])

para("Đường dẫn nên được dán vào ô File path thay vì gõ tay. Trong lần thực hiện, bộ gõ tiếng Việt đã tự chuyển chuỗi "
     "00_Data thành 00_Dât khi gõ trực tiếp. Bảng 10.3 liệt kê ánh xạ của 10 nguồn và số dòng cần đạt sau khi Refresh. "
     "Thư mục cũ A là C:\\Users\\Admin\\Downloads, thư mục cũ B là C:\\Users\\vomin\\Downloads\\SkyOps_8_4_handover_v14"
     "\\outputs. Mọi tệp mới đều nằm trong C:\\SkyOps\\00_Data.", align=L, before=8)
caption("Bảng 10.3. Ánh xạ nguồn dữ liệu trước và sau khi Change Source", keep=True)
DATA = [("DimAirport", "A", "370"), ("DimCarrier", "A", "17"), ("DimDate", "A", "730"),
        ("DimDelayReason", "A", "5"), ("DimFlightStatus", "A", "4"), ("DimRoute", "A", "6.820"),
        ("FactDelay", "A", "397.131"), ("FactFlight", "A", "1.151.344"),
        ("DSS_Flight_Prediction", "B", "1.151.344"), ("DSS_Priority_Tier", "B", "4")]
rows = [[i, old + "\\" + ("FactFlight.txt" if n == "FactFlight" else n + ".csv"), n + ".csv", c]
        for i, (n, old, c) in enumerate(DATA, 1)]
table(["TT", "Tệp cũ (thư mục \\ tệp)", "Tệp mới trong C:\\SkyOps\\00_Data", "Số dòng"], rows,
      [1.2, 5.9, 5.9, 3.0], ["C", "L", "L", "R"])

heading("Kết quả thực hiện", 2)
para("Hình 10.2 so sánh hộp thoại Data source settings trước và sau Bước 3. Trước khi thay đổi, cả 10 nguồn trỏ tới hai "
     "thư mục người dùng không tồn tại trên máy thử. Sau khi thay đổi, toàn bộ nguồn trỏ về C:\\SkyOps\\00_Data.")
figure(ROOT + "/figures/fig_change_source.png", TEXT_W)
caption("Hình 10.2. Hộp thoại Data source settings trước và sau khi Change Source", keep=False)
para("Ở Bước 4, thao tác Apply changes nạp lại 10 bảng trong khoảng 54 giây, từ 18:26:19 đến 18:27:13. Thao tác "
     "Refresh → Data sau đó hoàn tất trong khoảng 51 giây, từ 18:29:04 đến 18:29:55. Thời gian được ghi theo đồng hồ hệ "
     "thống với chu kỳ theo dõi 3 giây. Thanh trạng thái Table view xác nhận FactFlight có 1.151.344 dòng và FactDelay "
     "có 397.131 dòng, khớp với số dòng của tệp nguồn. Các KPI trên trang P1 Overview giữ nguyên giá trị như trước khi "
     "chuyển nguồn: OTP Rate 76,18%, độ trễ khởi hành trung bình 13,26 phút, độ trễ đến trung bình 7,91 phút và tỷ lệ "
     "hủy chuyến 2,28%.")
figure(ROOT + "/figures/fig_refresh_result.png", 14.5)
caption("Hình 10.3. Dashboard và số dòng hai bảng Fact sau khi Refresh trên máy mới", keep=False)
para("Sau khi kiểm chứng, tệp SkyOps.pbix trong gói đã được lưu với đường dẫn C:\\SkyOps\\00_Data. Nếu người tiếp nhận "
     "giải nén gói đúng vào C:\\SkyOps, Bước 3 chỉ còn là bước kiểm tra. Nếu đặt gói ở vị trí khác, người tiếp nhận thực "
     "hiện đầy đủ Bước 3 với thư mục tương ứng.")

OUT = ROOT + "/BI và DSS - hoàn thiện 10.1-10.2.docx"
doc.save(OUT)
print("saved", OUT.encode("ascii", "replace").decode())
