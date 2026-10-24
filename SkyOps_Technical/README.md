# SkyOps — Bàn giao kỹ thuật cho Phúc

Tài liệu mô tả ETL Pandas, tám CSV 2022–2023 và cấu hình để Phúc dựng Power BI. Mô hình dbt/hợp đồng trong `../analytics/models/`, `../contracts/` giữ SQL snake_case; CSV dùng tên dưới đây. Đây là **bàn giao dữ liệu, công thức và hướng dẫn**, không phải xác nhận đã dựng hoặc chạy Power BI. Không có `.pbix`, Model View hay ảnh dashboard giả lập.

## 6.2. Fact table(s)

Nguồn nghiệp vụ là `flights_sample_3m.csv`, chỉ giữ ngày bay 01/01/2022–31/12/2023. Raw có thể nằm ngoài Git; có mã ETL không có nghĩa là raw đã được bàn giao. Mapping/Grain ở chương trước chưa có trong gói này; các quy tắc được đối chiếu với SQL/hợp đồng hiện hữu, không giả lập tài liệu nguồn chưa nhận được.

### 6.2.1. FactFlight

FactFlight có **một dòng cho một bản ghi chuyến bay** trong tập nguồn đã lọc; khóa chính là FlightKey. Không phải một dòng cho mỗi FlightNumber: một số hiệu có thể lặp qua nhiều ngày. ETL Pandas không dedup; nếu trùng bộ ngày/hãng/số hiệu/đi/đến thì dừng và báo lỗi thay vì xóa dòng hoặc đánh số tùy ý. Mapping hãng không rõ ràng cũng phải được giải quyết trước khi dựng.

Các khóa ngoại gồm DateKey, CarrierKey, OriginAirportKey, DestAirportKey, RouteKey, FlightStatusKey. Thuộc tính chuyến gồm FlightNumber, ScheduledDepTime, ScheduledDepHour, ScheduledDepMinute và CancellationCode. Các đại lượng gồm DepDelay, ArrDelay, DepDelayMinutes, ArrDelayMinutes, Excess_Delay_Minutes, TaxiOutMinutes, TaxiInMinutes, AirTimeMinutes, ElapsedTimeMinutes, CrsElapsedTimeMinutes, DistanceMiles. Các cờ gồm IsDepDelayed15, IsArrDelayed15, IsCancelled, IsDiverted, IsCompleted, HasDelayReason.

DepDelay/ArrDelay giữ độ lệch **có dấu**: âm là sớm, 0 là đúng lịch. Không thay null bằng zero.

| Cột ngoài CSV | Biến đổi và tập đủ điều kiện |
| --- | --- |
| DepDelayMinutes | max(DepDelay, 0) khi không hủy và DepDelay không thiếu; còn lại null; vẫn có thể gồm diverted |
| ArrDelayMinutes | max(ArrDelay, 0) khi không hủy, không diverted và ArrDelay không thiếu; còn lại null |
| Excess_Delay_Minutes | max(ArrDelayMinutes − 15, 0); giữ null khi ArrDelayMinutes null |
| IsArrDelayed15 | 1 khi không hủy, không diverted và ArrDelay >= 15; còn lại 0 |
| ScheduledDepTime | CRS_DEP_TIME kiểu HHMM; 2400 → 0000; số nguyên có thể hiển thị 0 |
| ScheduledDepHour / ScheduledDepMinute | Giờ 0–23 / phút 0–59 lấy bằng chia nguyên/phần dư, không làm tròn giờ; không phải UTC |

Ngưỡng **15 phút cho Excess_Delay_Minutes là giả định triển khai**, cần đối chiếu Mapping/Grain gốc khi nhận được. Chậm đúng 15 phút thuộc Delayed nhưng excess = 0; chậm 20 phút có excess = 5. Không đồng nhất excess với tổng phút nguyên nhân.

CancellationCode giữ A = hãng, B = thời tiết, C = hệ thống không lưu, D = an ninh; blank nếu không có mã. Cờ IsCompleted chỉ xác nhận không hủy/không diverted, không bảo đảm có ArrDelay. Các chuyến không hủy/không diverted nhưng thiếu ArrDelay có thể mang nhãn On-time do nhánh ELSE của SQL; chúng **không** đủ điều kiện cho tử số hoặc mẫu số OTP. Không loại cả chuyến chỉ vì thiếu taxi, thời gian bay hoặc delay; ghi nhận chất lượng theo trường. Thiếu khóa/sai kiểu/ngoài hợp đồng phải được báo cáo, không âm thầm bù bản ghi.

### 6.2.2. FactDelayReason (xuất FactDelay.csv)

Bảng nội bộ `fact_delay_reason` xuất thành **FactDelay.csv**, grain **một chuyến × một nguyên nhân có DelayMinutes > 0**. DelayFactKey là khóa chính; cặp FlightKey–DelayReasonKey phải duy nhất.

| Cột CSV | Vai trò |
| --- | --- |
| DelayFactKey | Surrogate key của dòng nguyên nhân |
| FlightKey | Lineage đến chuyến gốc để đối chiếu, không tạo quan hệ fact-to-fact |
| DateKey | Ngày bay, dimension dùng chung |
| CarrierKey | Hãng, dimension dùng chung |
| OriginAirportKey | Sân bay đi, dimension dùng chung |
| DelayReasonKey | Loại nguyên nhân trong DimDelayReason |
| DelayMinutes | Phút chậm dương của nguyên nhân này |

Đó là **đúng bảy cột** của FactDelay. Không có DestAirportKey, RouteKey hoặc FlightStatusKey; các chiều đó không tự lọc nguyên nhân. ETL unpivot năm cột DELAY_DUE_CARRIER, DELAY_DUE_WEATHER, DELAY_DUE_NAS, DELAY_DUE_SECURITY, DELAY_DUE_LATE_AIRCRAFT. Null và 0 không sinh dòng nhưng chuyến vẫn ở FactFlight. Tổng phút nguyên nhân chỉ được hòa giải với ArrDelay trên tập nghiệp vụ áp dụng và bằng số liệu thực chạy, không giả định mọi chuyến có nguyên nhân hoặc mọi phút luôn hòa giải.

### 6.2.3. Ghi chú về việc tách fact table

Tách fact bảo toàn hai grain khác nhau: FactFlight giữ chỉ tiêu chuyến; FactDelay giữ đóng góp nhiều nguyên nhân cho cùng chuyến. Nếu nối rồi COUNTROWS, một chuyến có ba nguyên nhân bị đếm ba lần. **Total Flights phải đếm FactFlight**, không đếm FactDelay hay cộng số chuyến từng nguyên nhân để lấy tổng. Khi phân tích số chuyến có nguyên nhân cần định nghĩa DISTINCTCOUNT(FlightKey) riêng; đó không phải tổng số chuyến của toàn bộ nguồn.

FactDelay lưu thưa (sparse storage): chỉ lưu nguyên nhân > 0, tránh tạo năm dòng 0/null cho mọi chuyến; việc không có dòng nguyên nhân không làm biến mất chuyến khỏi FactFlight. Phút delay trên hai fact không phải các đại lượng để cộng chung. Các dimension conformed Date/Carrier/OriginAirport lọc song song hai fact, **không có quan hệ fact-to-fact active**.

Surrogate key được đánh số theo thứ tự xác định khi rebuild. Cùng dữ liệu, quy tắc sắp xếp và phiên bản mã phải tái tạo cùng bộ khóa; đây **không phải** cam kết khóa bền vững khi thêm/xóa dữ liệu hoặc đổi tập mã. ETL Pandas từ chối bộ khóa chuyến trùng để tránh tie-break tùy ý. Refresh phải thay đồng bộ tám bảng; không ghép fact mới với dimension cũ, không coi ROW_NUMBER là SCD hay incremental.

## 6.3. Dimension table(s)

Sáu dimension cung cấp nhãn, nhóm và điều kiện lọc. DateKey là khóa lịch YYYYMMDD; các khóa hãng/sân bay/tuyến là surrogate rebuild-only như 6.2.3. Mọi khóa phía dimension trong quan hệ 1:* phải duy nhất và không blank.

### 6.3.1. DimDate

Grain **một ngày lịch** từ 01/01/2022 đến 31/12/2023, kể cả ngày không có chuyến; khóa chính DateKey, FullDate là ngày đầy đủ. Không dựng lịch chỉ từ các ngày xuất hiện trong fact.

| Nhóm | Cột và ý nghĩa |
| --- | --- |
| Năm/quý | Year; Quarter 1–4; QuarterName Q1–Q4 |
| Tháng | Month 1–12; MonthName “Tháng n”; MonthYear YYYY-MM |
| Ngày/tuần | DayOfMonth, DayOfYear; WeekOfYear theo ISO; WeekdayNumber ISO 1 = Thứ Hai, 7 = Chủ Nhật; WeekdayName |
| Cuối tuần | IsWeekend kiểu Boolean, true cho Thứ Bảy/Chủ Nhật |
| Mùa | Season: Xuân = tháng 3–5, Hạ = 6–8, Thu = 9–11, Đông = 12/1/2 |

Season là nhóm lịch theo tháng, không phải dữ liệu thời tiết quan sát hoặc mùa khí tượng riêng từng sân bay. Mark as date table với FullDate. Hierarchy/sort nằm ở 6.4.2; giờ lịch nằm trong FactFlight, không trong DimDate.

### 6.3.2. DimCarrier

Grain **một CarrierCode** xuất hiện trong 2022–2023; khóa chính CarrierKey, khóa nghiệp vụ CarrierCode. Các thuộc tính gồm CarrierName, DotCode, AirlineDot (chuỗi nguồn AIRLINE_DOT). Giữ mã hãng kiểu Text, DotCode kiểu Whole number; không dùng tên hãng làm khóa.

SQL hiện hữu chọn MIN của tên/mã DOT/chuỗi AirlineDot theo mã hãng; ETL Pandas dừng khi mapping không rõ ràng thay vì tự chọn một danh tính khác. Không suy ra dimension SCD hoặc lịch sử đổi tên từ bảng một dòng/mã này. DimCarrier lọc cả hai fact theo CarrierKey.

### 6.3.3. DimAirport

Grain **một AirportCode** xuất hiện ở đầu đi hoặc đầu đến, khóa chính AirportKey. Các cột: AirportCode, AirportName, City, State, StateName, Region, RegionName, CountryCode, Latitude, Longitude, DataSource. State là alias của state_code, không phải tên bang đầy đủ. AirportCode là mã nghiệp vụ; tên thành phố không đủ để nhận diện sân bay.

Tên, vị trí và State lấy từ seed `../analytics/seeds/airport_master.csv`, không có geocoding ngầm. DataSource phân biệt ourairports, manual, fallback_raw_city, missing. OurAirports (https://ourairports.com/data/) là nguồn công khai, không phải danh mục lịch sử BTS chính thức; seed là snapshot tái lập. Override thủ công cần xem provenance và xác minh lịch sử trước khi dùng làm bằng chứng vị trí. Kiểm tra phạm vi 2022–2023 ghi nhận XWA (Williston Basin, OurAirports), không có ISN; override ISN trong seed không có nghĩa mã đó xuất hiện trong CSV bàn giao.

Bốn Census regions là Northeast, Midwest, South, West cho các bang và DC (https://www2.census.gov/geo/pdfs/maps-data/maps/reference/us_regdiv.pdf). PR/VI/GU/AS/MP là lãnh thổ, **không thuộc bốn vùng Census chính thức**. Seed gán PR/VI vào South, GU/AS/MP vào West là nhóm phân tích dự án, không được chú thích là phân vùng Census chính thức cho lãnh thổ. Khi báo cáo Census cần loại lãnh thổ hoặc tách nhóm minh bạch; giữ State/CountryCode/DataSource để nhận diện. Other/Khác biểu thị chưa phân vùng, không phải vùng Census thứ năm. Không tự bù tọa độ thiếu bằng 0.

DimAirport có vai trò đi active và đến inactive với FactFlight; với FactDelay chỉ có vai trò đi. Không nối thêm dimension này vào DimRoute.

### 6.3.4. DimRoute

Grain **một cặp có hướng OriginCode–DestCode**, A–B khác B–A; RouteKey là khóa chính. Các cột: OriginCode, DestCode, RouteName (A-B), OriginAirportKey, DestAirportKey, DistanceMiles, DistanceGroup, DistanceGroupOrder.

DistanceMiles là trung bình khoảng cách theo cặp đi–đến, làm tròn hai chữ số; không chọn tùy ý một khoảng cách khi nguồn có nhiều giá trị. Khoảng cách FactFlight vẫn ở grain chuyến. DistanceGroup gồm <500, 500–<1000, 1000–<1500, 1500–<2000, >=2000 dặm; DistanceGroupOrder 1–5 dùng để sort. Hai khóa sân bay trong DimRoute phục vụ lineage, **không tạo quan hệ DimAirport–DimRoute**. DimRoute chỉ lọc FactFlight; không giả định lọc được FactDelay.

### 6.3.5. DimDelayReason

Grain **một loại nguyên nhân BTS**, khóa chính DelayReasonKey. Các thuộc tính: Code, ReasonName, Description, SourceColumn, SortOrder. Code/ReasonName là alias ngoài CSV của delay_reason_code/delay_reason_name.

| DelayReasonKey | Code | ReasonName | SourceColumn |
| --- | --- | --- | --- |
| 1 | CARRIER | Hãng hàng không | DELAY_DUE_CARRIER |
| 2 | WEATHER | Thời tiết | DELAY_DUE_WEATHER |
| 3 | NAS | Hệ thống không lưu | DELAY_DUE_NAS |
| 4 | SECURITY | An ninh | DELAY_DUE_SECURITY |
| 5 | LATE_AIRCRAFT | Tàu bay đến muộn | DELAY_DUE_LATE_AIRCRAFT |

SortOrder 1–5 theo thứ tự trên. Dimension chỉ lọc FactDelay, không lọc FactFlight. Mã nguyên nhân phút chậm này khác CancellationCode A–D; không gộp hai bộ mã.

### 6.3.6. DimFlightStatus

Grain **một trạng thái tổng hợp**, khóa chính FlightStatusKey. Các cột: StatusCode, StatusName, Description, IsCompleted, IsDisrupted, SortOrder. StatusCode/StatusName là alias ngoài CSV của flight_status_code/flight_status_name.

| FlightStatusKey | StatusCode | StatusName | IsCompleted | IsDisrupted |
| --- | --- | --- | --- | --- |
| 1 | On-time | Đúng giờ | true | false |
| 2 | Delayed | Chậm chuyến | true | true |
| 3 | Cancelled | Hủy chuyến | false | true |
| 4 | Diverted | Chuyển hướng | false | true |

SortOrder 1–4 theo bảng. Phân loại ưu tiên Cancelled, sau đó Diverted, sau đó ArrDelay >= 15 là Delayed, còn lại On-time. **Chỉ giữ bốn trạng thái**, không tạo dimension tám trạng thái trùng lặp; lý do hủy A–D nằm ở FactFlight[CancellationCode]. Nhãn On-time có thể gồm dòng thiếu ArrDelay do ELSE; đo OTP phải dùng điều kiện eligible, không COUNT theo nhãn. DimFlightStatus chỉ lọc FactFlight.

## 6.4. Star Schema và bàn giao Power BI

### 6.4.1. Sơ đồ Star Schema

Gói này cung cấp cấu hình để Phúc dựng sơ đồ thật, **không đính kèm ảnh Model View hay `.pbix` giả**. Trong Power BI Desktop, đặt FactFlight và FactDelay ở trung tâm; Date/Carrier/Airport là các chiều dùng chung, Route/FlightStatus chỉ nối FactFlight, DelayReason chỉ nối FactDelay. Sau khi dựng đúng ma trận dưới đây, chụp Model View thật để bổ sung báo cáo môn học và lưu `.pbix` thật.

Tám file cần nhập từ `data/processed/`: DimDate.csv, DimCarrier.csv, DimAirport.csv, DimRoute.csv, DimDelayReason.csv, DimFlightStatus.csv, FactFlight.csv, FactDelay.csv. Các đường dẫn này tương đối với `SkyOps_Technical/`. Get data → Text/CSV, UTF-8, delimiter dấu phẩy; tên bảng bằng tên file không có đuôi. Trong Power Query, kiểm tra header/locale/blank trước Close & Apply; không import thêm bản SQL snake_case của cùng bảng.

| Nhóm cột | Kiểu Power BI | Thiết lập |
| --- | --- | --- |
| Mọi *Key; số thứ tự lịch; ScheduledDepTime/Hour/Minute; FlightNumber; DotCode; SortOrder; DistanceGroupOrder | Whole number | Khóa/mã số: Do not summarize |
| FullDate | Date | Mark as date table: DimDate[FullDate] |
| IsWeekend; IsCompleted/IsDisrupted trong DimFlightStatus | True/False | Giữ Boolean của dimension |
| IsCancelled, IsDiverted, IsCompleted, IsDepDelayed15, IsArrDelayed15, HasDelayReason trong FactFlight | Whole number 0/1 | DAX so sánh số, không phải chuỗi hoặc Boolean |
| DepDelay, ArrDelay, các *Minutes, DistanceMiles, Latitude, Longitude | Decimal number | Giữ blank; tọa độ Do not summarize |
| Các mã, CancellationCode, State và nhãn | Text | Giữ mã nguyên vẹn, không suy thành số |

CSV biểu diễn null bằng trường rỗng: không Fill Down hay Replace null thành zero. Kiểm tra toàn bộ kiểu thay vì tin autodetect; gán Data category Latitude/Longitude cho tọa độ, không gán lãnh thổ thành vùng Census. Có thể ẩn khóa/cột kỹ thuật khỏi Report View.

Quy tắc đổi tên chung snake_case → PascalCase là viết hoa đầu mỗi token: date_key → DateKey, dot_code → DotCode, is_arr_delayed15 → IsArrDelayed15, scheduled_dep_hour → ScheduledDepHour. Ngoại lệ cố định:

| Nội bộ SQL | CSV / Power BI |
| --- | --- |
| state_code | State |
| flight_status_code / flight_status_name | StatusCode / StatusName |
| delay_reason_code / delay_reason_name | Code / ReasonName |
| excess_delay_minutes | Excess_Delay_Minutes |
| fact_delay_reason (tên bảng) | FactDelay |

### 6.4.2. Mô tả Star Schema

Mỗi dòng dưới đây có cardinality **1:* và Cross-filter direction = Single từ dimension (1) sang fact (*)**. Khóa phía 1 duy nhất, không blank; hai phía cùng kiểu. Xóa quan hệ tự dò không khớp ma trận. Không bật Both.

| Bảng/cột phía 1 | Bảng/cột phía * | Trạng thái |
| --- | --- | --- |
| DimDate[DateKey] | FactFlight[DateKey] | Active |
| DimCarrier[CarrierKey] | FactFlight[CarrierKey] | Active |
| DimAirport[AirportKey] | FactFlight[OriginAirportKey] | Active |
| DimAirport[AirportKey] | FactFlight[DestAirportKey] | Inactive |
| DimRoute[RouteKey] | FactFlight[RouteKey] | Active |
| DimFlightStatus[FlightStatusKey] | FactFlight[FlightStatusKey] | Active |
| DimDate[DateKey] | FactDelay[DateKey] | Active |
| DimCarrier[CarrierKey] | FactDelay[CarrierKey] | Active |
| DimAirport[AirportKey] | FactDelay[OriginAirportKey] | Active |
| DimDelayReason[DelayReasonKey] | FactDelay[DelayReasonKey] | Active |

Không tạo quan hệ fact-to-fact, đặc biệt FactFlight[FlightKey] → FactDelay[FlightKey]. Hai fact lọc qua conformed dimensions, không truyền filter qua nhau. Không tạo DimAirport → DimRoute dù có khóa sân bay ở DimRoute; đó là lineage. Không tạo các quan hệ khác ngoài ma trận.

Slicer ngày/hãng/sân bay đi lọc cả hai fact. Slicer tuyến/trạng thái/giờ chỉ lọc FactFlight; slicer nguyên nhân chỉ lọc FactDelay. Các KPI trên cùng trang phải thể hiện giới hạn này; không bật Both để che chiều còn thiếu ở một fact.

Mặc định DimAirport đại diện sân bay đi. `Avg Arr Delay (Destination)` dùng USERELATIONSHIP để bật đường đến và **CROSSFILTER(..., NONE)** để tắt đường đi của FactFlight trong phép tính; không đồng thời ép cả hai vai trò. Measure không đổi vai trò sân bay cho FactDelay; không gắn nhãn nguyên nhân theo sân bay đến khi chưa có mapping/thiết kế tương ứng.

Hierarchy lịch: Year → QuarterName → MonthName → FullDate. Sort QuarterName by Quarter, MonthName by Month, WeekdayName by WeekdayNumber; MonthYear YYYY-MM sắp theo thời gian. Sort DistanceGroup by DistanceGroupOrder, StatusName và ReasonName by SortOrder trong dimension tương ứng.

Không thể tạo một hierarchy duy nhất `DimDate: Year → Month → Date → Hour` vì ScheduledDepHour nằm ở **FactFlight**. Có thể dùng các trường DimDate và FactFlight[ScheduledDepHour] thành các cấp trục/matrix của visual, không gọi đó là hierarchy đơn bảng DimDate. Không nhân 24 dòng/ngày trong DimDate vì phá tính duy nhất DateKey. DimTime riêng chỉ thiết kế khi có yêu cầu thực.

Gợi ý trang tổng quan: Total Flights, OTP Rate, Cancellation Rate, Avg Arr Delay và xu hướng MonthYear. Trang nguyên nhân dùng ReasonName, Total Delay Minutes, Delay Cause %, chỉ dùng slicer có đường lọc FactDelay. Trang sân bay ghi rõ vai trò và đơn vị phút. Hiển thị blank thành “Không đủ dữ liệu” nếu thích hợp, không tự biến thành thành tích 0%. Chưa có xác nhận publish dịch vụ/dashboard; Phúc kiểm tra tương tác visual và lưu artifact Power BI thật sau khi dựng.

## 6.6. Measure Definitions và DAX

### 6.6.1. Bảng Measure Definitions

Tạo từng measure riêng qua Modeling → New measure, bắt đầu với `powerbi/dax_measures/01_core_kpis.dax`, sau đó `02_delay_metrics.dax`. Tệp gồm định nghĩa riêng phân cách bằng dòng trống/chú thích, không phải query để dán nguyên khối vào DAX Query View. Tên bảng/cột phải đúng CSV; **chưa có xác nhận thực thi DAX trong Power BI**.

| Measure | Định nghĩa nghiệp vụ và mẫu số | Format string |
| --- | --- | --- |
| Total Flights | COUNTROWS FactFlight trong filter context, không đếm FactDelay | #,0 |
| Eligible Arrivals | Không hủy, không diverted, ArrDelay gốc không blank | #,0 |
| On Time Flights | Eligible Arrivals có ArrDelay có dấu < 15, gồm chuyến sớm | #,0 |
| OTP Rate | On Time Flights / Eligible Arrivals | 0.00% |
| Cancelled Flights | FactFlight có IsCancelled = 1 | #,0 |
| Cancellation Rate | Cancelled Flights / Total Flights; mẫu số gồm cả hủy/diverted trong context | 0.00% |
| Avg Dep Delay | AVERAGE DepDelayMinutes; không hủy, DepDelay gốc có giá trị; gồm cả diverted đủ điều kiện | #,0.00 |
| Avg Arr Delay | AVERAGE ArrDelayMinutes; chỉ Eligible Arrivals | #,0.00 |
| Avg Arr Delay (Destination) | Như Avg Arr Delay nhưng DimAirport lọc đến thay vì đi | #,0.00 |
| Total Delay Minutes | SUM FactDelay[DelayMinutes], tổng phút nguyên nhân ghi nhận | #,0.00 |
| Delay Cause % | Total Delay Minutes / tổng phút sau REMOVEFILTERS(DimDelayReason) | 0.00% |

Không thêm Priority Score: chưa có công thức/chuẩn hóa/trọng số nguồn để dựng chỉ số chính thức.

### 6.6.2. Giải thích cách viết DAX

Các measure đếm COALESCE thành 0 khi tập rỗng. DIVIDE không truyền alternateResult: mẫu số 0/blank trả **BLANK, không phải 0%**. Tử số 0/mẫu số dương trả 0%. Không nhân thêm 100 với format %; không dùng FORMAT() biến kết quả thành text, đặt format string trong Measure tools.

Avg Dep/Arr Delay cố ý dùng **clipped delay**: chuyến sớm/đúng giờ đủ điều kiện đóng góp 0 nhưng vẫn trong mẫu số. Không phải trung bình chỉ chuyến chậm hoặc trung bình độ lệch có dấu. AVERAGE bỏ blank, giữ zero là một quan sát; không COALESCE null thành zero trước trung bình. Không có quan sát đủ chuẩn thì trung bình BLANK. Eligible Arrivals kiểm tra ArrDelay gốc để dòng thiếu không lọt vào OTP dù nhãn là On-time.

**Filter context** đến từ slicer, page/report/visual filters và hàng/cột visual; một ô matrix theo hãng là filter context, không phải row context. CALCULATE đánh giá measure với context được sửa. KEEPFILTERS giao điều kiện đủ chuẩn với filter hiện có thay vì thay thế điều kiện cùng cột. OTP dưới slicer chỉ On-time hoặc cancellation rate dưới slicer Cancelled là tỷ lệ **trong tập đã lọc**, không phải tỷ lệ toàn bộ hãng. Muốn card độc lập trạng thái phải thống nhất ngữ nghĩa trước, không tự bỏ filter.

**Row context** xuất hiện trong calculated column hoặc iterator như SUMX/FILTER, không tự xuất hiện từng dòng fact chỉ vì measure hiển thị. CALCULATE có thể chuyển row context thành filter context khi có row context; các measure hiện tại không cần iterator cho SUM/AVERAGE/COUNTROWS đơn giản. Không cộng tỷ lệ từng hàng để ra tổng; tổng được đánh giá lại trong filter context tổng.

REMOVEFILTERS(DimDelayReason) bỏ mọi filter của dimension nguyên nhân ở mẫu số, giữ ngày/hãng/sân bay đi đang lọc FactDelay. Nếu slicer chỉ chọn một phần nguyên nhân, mẫu số vẫn gồm **tất cả** nguyên nhân trong context còn lại, không phải tổng phần chọn (không dùng ALLSELECTED). Dùng ReasonName/Code của dimension làm trục/slicer; filter trực tiếp FactDelay[DelayReasonKey] không bị REMOVEFILTERS của dimension xóa. Không có phút nguyên nhân thì DIVIDE trả BLANK. Tổng phút nguyên nhân khác Excess_Delay_Minutes và tổng ArrDelay có dấu.

Destination measure dùng USERELATIONSHIP cộng CROSSFILTER tắt Origin như 6.4.2; kiểm tra kết quả với nhóm DestAirportKey, không OriginAirportKey. Công thức này chỉ đổi đường lọc FactFlight, không biến FactDelay thành fact có sân bay đến.

#### Tái tạo ETL và nhận dữ liệu

Từ gốc repository, chạy Pandas theo thứ tự:

```text
python -m pip install -r SkyOps_Technical/src/requirements.txt
python SkyOps_Technical/src/02_build_dimensions.py --raw "duong-dan-thuc/flights_sample_3m.csv"
python SkyOps_Technical/src/03_build_facts.py --raw "duong-dan-thuc/flights_sample_3m.csv"
python pipelines/validate_handoff.py
```

Chỉ cài dependencies nếu môi trường chưa đáp ứng. `--raw` trỏ tới nguồn thực, mặc định `SkyOps_Technical/data/raw/flights_sample_3m.csv`; `--output-dir` mặc định `SkyOps_Technical/data/processed/`; cả hai hỗ trợ `--chunksize 100000`. Dùng cùng raw/output; 03 cần sáu dimensions đã tạo bởi 02, không tự dựng lại. Đọc chunk và lọc 2022–2023 trước khi nối nhưng vẫn cần RAM cho tập đã lọc, không phải streaming hoàn toàn. Hai script in số dòng; không tự sinh validation_report.json.

`validate_handoff.py` kiểm tra chính CSV nạp Power BI, ghi `outputs/validation_report.json` và cập nhật `_manifest.json` với SHA-256. Nếu đã dựng warehouse dbt ở gốc repo, chạy `python pipelines/validate_handoff.py --database warehouse.duckdb` để đối chiếu toàn bộ giá trị tám bảng; `DimRoute.DistanceMiles` so sánh ở 8 chữ số thập phân để tránh sai khác cộng số thực. Không có `--database` chỉ kiểm tra hợp đồng CSV, không tuyên bố đã đối chiếu dbt.

Đợt bàn giao này đã chạy hai script Pandas trên nguồn thật: CSV đạt 46/46 kiểm tra có đối soát dbt; dbt build đạt PASS=146, ERROR=0; hợp đồng warehouse đạt 186/186. Chi tiết/hash và KPI xem JSON, không suy rằng DAX đã được chạy.

FactFlight.csv vượt 100 MB nên dùng Git LFS. Khi nhận qua Git, dùng Git client/Git LFS, clone và checkout đúng nhánh bàn giao rồi chạy `git lfs pull` ở repository. FactFlight.csv phải là CSV có header, không phải pointer `version https://git-lfs.github.com/spec/v1`. Hoặc tải **ZIP dữ liệu thực đính kèm GitHub Release** của đợt bàn giao và giải nén giữ cấu trúc `SkyOps_Technical/data/processed/`. Source ZIP tự động của GitHub không bảo đảm chứa CSV LFS thực. Dùng URL/nhánh/tag được người bàn giao công bố, không suy ra URL chưa tồn tại; đối chiếu byte/hash trong report sau tải.

#### Đối chiếu và tạo Word từ bằng chứng

`outputs/validation_report.json` phải thuộc lần export hiện tại. Word chỉ trích status, checks, bảng và metrics thực có, không chép số dòng/KPI cũ thành kết quả hiện tại. Thiếu report không đồng nghĩa PASS; thiếu một chỉ tiêu thì không bổ sung con số giả. dbt, CSV validation và Power BI có phạm vi khác nhau; PASS dbt/CSV không chứng nhận DAX/visual. Giữ report và CSV cùng đợt, đối chiếu hash nếu có.

Sau import, Phúc đối chiếu số dòng tám bảng, ma trận 1:*, active/inactive, tổng KPI và một lát cắt năm/hãng. Các ca cần thử trong Power BI: ArrDelay âm/0/14/15, thiếu ArrDelay, hủy, diverted, tập rỗng, đi khác đến, slicer nguyên nhân và tổng matrix. Đây là hướng dẫn nhận bàn giao, không tuyên bố các bước đã thực hiện.

Generator `outputs/build_handoff_doc.py` đọc README này, hai tệp DAX và report nếu có; không cần Word gốc/ảnh/`.pbix`. Cần `python-docx`; không cài lại nếu đã có. Từ `SkyOps_Technical/`, sau khi có report cuối cùng:

```text
python outputs/build_handoff_doc.py
python outputs/build_handoff_doc.py --validation-report outputs/validation_report.json --output outputs/SkyOps_BanGiao_KyThuat_6.2-6.6.docx
```

Word dùng A4, Times New Roman, tiêu đề phân cấp, bảng có header lặp và phụ lục DAX đơn cách. Thiếu report mặc định thì ghi chưa có bằng chứng, không ghi PASS; chỉ định report không tồn tại hoặc JSON lỗi thì dừng. Có report mới phải sinh lại Word. Generator không chạy ETL/dbt/Power BI và không tự chứng thực report khớp CSV hiện tại ngoài bằng chứng đầu vào.

Bàn giao gồm tám CSV thực, ETL, report validation thực, README, hai tệp DAX và Word sinh từ các nguồn đó. `.pbix`, Model View và ảnh báo cáo thật được Phúc bổ sung sau khi dựng và kiểm chứng; hướng dẫn không phải artifact Power BI đã hoàn thành.

