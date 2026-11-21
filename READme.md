# 🍳 Bếp Thông Minh

Ứng dụng hỗ trợ nấu ăn tại nhà: tìm công thức theo nguyên liệu, xem hướng dẫn từng bước, tìm chợ/siêu thị gần vị trí và hỏi trợ lý đầu bếp AI.

## Chức năng chính

- Tìm và lọc công thức theo nguyên liệu, danh mục, khẩu vị và thời gian.
- Xem chi tiết nguyên liệu, định lượng và các bước nấu.
- Lưu công thức yêu thích và dùng bộ đếm thời gian khi nấu.
- Tìm điểm bán nguyên liệu gần vị trí bằng Amazon Location Service.
  - Dùng GPS của trình duyệt.
  - Hoặc nhập địa chỉ thủ công để tìm quanh địa chỉ đó.
- Chatbot đầu bếp AI dùng Amazon Bedrock để gợi ý món và hỗ trợ câu hỏi nấu ăn.

> Dự án chỉ phục vụ nấu ăn tại nhà. Chức năng đặt đồ ăn/đơn hàng đã được loại khỏi phiên bản này. Kết quả tìm địa điểm không xác nhận cửa hàng đang có sẵn từng nguyên liệu.

## Kiến trúc AWS

```
Trình duyệt
   |
   +--> Amazon S3 (static website)
   |
   +--> Amazon API Gateway
             |
             v
        AWS Lambda (Python 3.12)
          |       |        |
          v       v        v
       DynamoDB  Location  Bedrock
       MealRecipesTable  Places v2  Nova Micro
```

## API được Lambda hỗ trợ

| Method | Route | Mục đích |
|---|---|---|
| GET | `/search` | Tìm/lọc công thức |
| GET | `/nearby-stores` | Tìm cửa hàng gần tọa độ GPS hoặc địa chỉ |
| POST | `/chat` | Gửi câu hỏi cho đầu bếp AI |

Ví dụ tham số tìm cửa hàng:

- GPS: `/nearby-stores?ingredients=trứng&tier=mid&lat=10.7769&lng=106.7009`
- Địa chỉ: `/nearby-stores?ingredients=trứng&tier=mid&address=123%20Nguyen%20Hue%2C%20Quan%201%2C%20TP.HCM`

## Cấu trúc thư mục

```
AWS-Project-Final/
├── frontend/
│   ├── index.html                 # Chuyển hướng đến ứng dụng nấu ăn
│   └── cooking/
│       └── index.html             # Giao diện chính, GPS, nhập địa chỉ, chatbot
├── backend/
│   ├── lambda_function.py         # API Lambda: DynamoDB + Location + Bedrock
│   ├── seed_data.json             # Dữ liệu công thức mẫu
│   ├── seed_recipes.py            # Nạp/cập nhật dữ liệu vào DynamoDB
│   └── RECIPE_DATA_STANDARD.md    # Quy chuẩn dữ liệu công thức
└── security-and-docs/
    └── iam_policy.json            # Quyền IAM cần cho Lambda
```

## Cấu hình backend

Region mặc định: `ap-southeast-1`

Biến môi trường Lambda:

| Key | Giá trị |
|---|---|
| `DYNAMODB_TABLE` | `MealRecipesTable` |
| `BEDROCK_MODEL_ID` | `amazon.nova-micro-v1:0` |

DynamoDB cần bảng `MealRecipesTable` với partition key `RecipeID` kiểu String.

Cài dependency và nạp dữ liệu từ thư mục backend:

```bash
python -m pip install boto3
python seed_recipes.py --table MealRecipesTable --file seed_data.json --region ap-southeast-1
```

Không đưa AWS access key/secret key vào repository. Chỉ cấp cho Lambda các quyền cần thiết trong `security-and-docs/iam_policy.json`; role thực tế cần được kiểm tra theo tên bảng, function và model đã cấu hình trên AWS.

## Cấu hình frontend

Mở `frontend/cooking/index.html` và kiểm tra:

```javascript
const API_BASE = "https://1f10tq33a6.execute-api.ap-southeast-1.amazonaws.com/prod";
```

Đây là endpoint đang được ghi trong file giao diện lấy từ bản của nhóm; cần đối chiếu với **API Gateway → Stages → prod → Invoke URL** trên tài khoản AWS đang triển khai. Nếu Invoke URL khác, thay lại đúng URL đó. API Gateway cần có ba route ở bảng phía trên và bật CORS cho website.

## Lưu ý GPS và chi phí

- Geolocation của trình duyệt cần HTTPS hoặc localhost. Nếu S3 static website đang dùng HTTP, hãy dùng CloudFront HTTPS để GPS hoạt động; nhập địa chỉ thủ công vẫn có thể dùng.
- GPS chỉ được lấy khi người dùng bấm nút và cấp quyền.
- Amazon Location tìm địa điểm phù hợp gần tọa độ/địa chỉ; dữ liệu này không phải tồn kho cửa hàng.
- DynamoDB, Lambda, Location và Bedrock có thể phát sinh chi phí. Kiểm tra AWS Billing/Budgets và xóa tài nguyên demo không còn dùng sau khi đã lưu minh chứng.
