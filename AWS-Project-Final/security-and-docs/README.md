# 🍽️ Bếp Thông Minh — Website Gợi Ý & Hướng Dẫn Nấu Ăn Theo Nguyên Liệu
**AWS Serverless Lab** · S3 · API Gateway · Lambda · DynamoDB · Amazon Location Service · Amazon Bedrock

---

## 👥 Phân Công Nhiệm Vụ

| Thành Viên | Phụ Trách Thư Mục | Nhiệm Vụ Chi Tiết |
|---|---|---|
| **Thành viên 1** | `frontend/` | Thiết kế UI dạy nấu ăn, tích hợp API URL, GPS trình duyệt, bộ đếm giờ từng bước, upload lên S3 |
| **Thành viên 2** | `backend/` | Tạo bảng `MealRecipesTable`, chuẩn hóa dữ liệu 20 món, chạy `seed_recipes.py`, viết và deploy Lambda function |
| **Thành viên 3** | `security-and-docs/` | Tạo Place Index (Location Service), kích hoạt Bedrock, cấu hình IAM Policy, tạo API Gateway, kiểm tra CORS, viết tài liệu |

---

## 🏗️ Kiến Trúc Hệ Thống

Người dùng
│
▼
[S3 Static Website]  ←── frontend/index.html
│
├──► HTTP GET  /search?ingredients=trứng,cà chua
├──► HTTP GET  /nearby-stores?ingredients=thịt&tier=mid&lat=10.7769&lng=106.7009
└──► HTTP POST /chat  {"message": "Trời mưa lạnh nấu gì ấm bụng?"}
│
▼
[API Gateway]  ←── HTTP API
│
│  Trigger
▼
[AWS Lambda: RecipeSearchFunction]  ←── backend/lambda_function.py (Python 3.12)
│
├──► boto3.resource("dynamodb")       ──► [DynamoDB: MealRecipesTable] (dữ liệu từ backend/seed_data.json)
├──► boto3.client("location")         ──► [Amazon Location Service: BepThongMinhPlaceIndex]
└──► boto3.client("bedrock-runtime")  ──► [Amazon Bedrock: amazon.nova-micro-v1:0]


---

## 🚀 Hướng Dẫn Triển Khai

### Bước 1 — Tạo Bảng DynamoDB & Import Dữ Liệu (Thành viên 2)

1. Vào **AWS Console → DynamoDB → Create table** (Region `ap-southeast-1`)
2. Cấu hình bảng:
   - **Table name:** `MealRecipesTable`
   - **Partition key:** `RecipeID` (String)
3. Mở **AWS CloudShell** (hoặc terminal có sẵn AWS CLI), tải lên 2 file `backend/seed_data.json` và `backend/seed_recipes.py`, sau đó chạy lệnh import hàng loạt 20 món (`R01` – `R20`):
   ```bash
   python3 -m pip install boto3
   python3 backend/seed_recipes.py --table MealRecipesTable --file backend/seed_data.json --region ap-southeast-1

    Vào tab Explore table items của bảng MealRecipesTable để kiểm tra đủ 20 bản ghi.

Bước 2 — Kích Hoạt Amazon Location Service & Amazon Bedrock (Thành viên 3)

    Tạo Place Index trên Amazon Location Service (ap-southeast-1):

        Vào Amazon Location Service → Places → Place indexes → Create place index

        Name: BepThongMinhPlaceIndex

        Data provider: chọn Esri (hoặc Grab) → bấm Create place index

    Kích hoạt mô hình trên Amazon Bedrock (ap-southeast-1):

        Vào Amazon Bedrock → Model access

        Đảm bảo mô hình Amazon Nova Micro (amazon.nova-micro-v1:0) ở trạng thái Access granted

Bước 3 — Tạo IAM Role cho Lambda (Thành viên 3)

    Vào IAM → Policies → Create policy → JSON tab

    Paste nội dung từ file security-and-docs/iam_policy.json vào và đặt tên: RecipesLambdaPolicy
    JSON

    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Sid": "ReadMealRecipesTable",
          "Effect": "Allow",
          "Action": [
            "dynamodb:Scan",
            "dynamodb:Query",
            "dynamodb:GetItem"
          ],
          "Resource": "arn:aws:dynamodb:ap-southeast-1:*:table/MealRecipesTable"
        },
        {
          "Sid": "SearchNearbyStores",
          "Effect": "Allow",
          "Action": [
            "geo:SearchPlaceIndexForText"
          ],
          "Resource": "arn:aws:geo:ap-southeast-1:*:place-index/BepThongMinhPlaceIndex"
        },
        {
          "Sid": "InvokeBedrockChefAI",
          "Effect": "Allow",
          "Action": [
            "bedrock:InvokeModel"
          ],
          "Resource": "arn:aws:bedrock:ap-southeast-1::foundation-model/amazon.nova-micro-v1:0"
        },
        {
          "Sid": "WriteCloudWatchLogs",
          "Effect": "Allow",
          "Action": [
            "logs:CreateLogGroup",
            "logs:CreateLogStream",
            "logs:PutLogEvents"
          ],
          "Resource": "arn:aws:logs:ap-southeast-1:*:log-group:/aws/lambda/RecipeSearchFunction:*"
        }
      ]
    }

    Vào IAM → Roles → Create role → Trusted entity: Lambda → gắn policy RecipesLambdaPolicy → đặt tên Role: RecipesLambdaRole

Bước 4 — Tạo Lambda Function (Thành viên 2)

    Vào Lambda → Create function (ap-southeast-1)

    Cấu hình:

        Function name: RecipeSearchFunction

        Runtime: Python 3.12

        Execution role: RecipesLambdaRole (đã tạo ở Bước 3)

    Paste toàn bộ nội dung file backend/lambda_function.py vào code editor → bấm Deploy

    Vào Configuration → General configuration → Edit, tăng Timeout lên 15 sec

    (Tùy chọn) Thêm Environment Variables:

        DYNAMODB_TABLE = MealRecipesTable

        PLACE_INDEX_NAME = BepThongMinhPlaceIndex

        BEDROCK_MODEL_ID = amazon.nova-micro-v1:0

Bước 5 — Tạo API Gateway (Thành viên 3)

    Vào API Gateway → Create API → HTTP API

    Thêm Integration:

        Integration type: Lambda

        Lambda function: RecipeSearchFunction

    Thêm các Routes trỏ về Integration vừa tạo:

        GET /search & OPTIONS /search

        GET /nearby-stores & OPTIONS /nearby-stores

        POST /chat & OPTIONS /chat

    Cấu hình CORS:

        Allow origins: *

        Allow methods: GET, POST, OPTIONS

        Allow headers: Content-Type, Authorization

    Deploy → chọn stage prod

    Sao chép Invoke URL (ví dụ: https://rb4u5du9z3.execute-api.ap-southeast-1.amazonaws.com/prod)

Bước 6 — Cập Nhật Frontend và Upload lên S3 (Thành viên 1)

    Mở file frontend/index.html

    Tìm dòng:
    JavaScript

    const API_BASE = "[https://rb4u5du9z3.execute-api.ap-southeast-1.amazonaws.com/prod](https://rb4u5du9z3.execute-api.ap-southeast-1.amazonaws.com/prod)";

    và cập nhật đúng Invoke URL từ Bước 5.

    Vào S3 → Create bucket:

        Bucket name: recipe-suggestion-website (phải là tên duy nhất toàn cầu)

        Region: ap-southeast-1

    Tắt Block all public access → xác nhận

    Vào tab Properties → Static website hosting → Enable

        Index document: index.html

    Vào tab Permissions → Bucket policy → Paste policy sau:
    JSON

    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Sid": "PublicReadGetObject",
          "Effect": "Allow",
          "Principal": "*",
          "Action": "s3:GetObject",
          "Resource": "arn:aws:s3:::recipe-suggestion-website/*"
        }
      ]
    }

    Upload file frontend/index.html lên bucket

    Truy cập Endpoint URL của S3 static website để kiểm tra

✅ Kiểm Tra Hoàn Chỉnh
Hạng mục	Cách kiểm tra	Kết quả mong đợi
DynamoDB	Vào Items của MealRecipesTable → kiểm tra 20 món	Hiển thị đủ 20 bản ghi (R01 – R20)
Lambda	Test với event {"queryStringParameters": {"ingredients": "trứng"}}	Status 200, trả về JSON danh sách món
API Gateway	Gọi /search, /nearby-stores, /chat	HTTP 200, trả về đúng dữ liệu món, địa điểm và phản hồi AI
S3 Website	Mở Endpoint URL	Trang web hiển thị, bộ đếm giờ từng bước, định vị chỗ mua và chatbot hoạt động
CORS	Dùng DevTools Network tab	Không có lỗi CORS
CloudWatch	Lambda → Monitor → Logs	Thấy log Loaded 20 recipes from MealRecipesTable, không lỗi quyền
📁 Cấu Trúc Thư Mục

AWS-Project-Final/
├── frontend/
│   └── index.html              # Giao diện dạy nấu ăn (HTML5 + Tailwind + Vanilla JS)
├── backend/
│   ├── lambda_function.py      # Code Lambda Python 3.12 (DynamoDB + Location + Bedrock)
│   ├── seed_data.json          # Dữ liệu 20 món ăn chuẩn hóa (R01–R20)
│   └── seed_recipes.py         # Script import dữ liệu vào DynamoDB
└── security-and-docs/
    ├── iam_policy.json         # IAM Policy (Least Privilege)
    └── README.md               # File này

🔐 Nguyên Tắc Bảo Mật (Least Privilege)

    Lambda chỉ có quyền Scan, Query, GetItem trên đúng bảng MealRecipesTable

    Lambda chỉ có quyền geo:SearchPlaceIndexForText trên đúng BepThongMinhPlaceIndex

    Lambda chỉ có quyền bedrock:InvokeModel trên đúng mô hình amazon.nova-micro-v1:0

    Lambda chỉ ghi log vào /aws/lambda/RecipeSearchFunction:* trên CloudWatch

    S3 bucket chỉ cho phép đọc file tĩnh (s3:GetObject)

    API Gateway có CORS headers giới hạn method (GET, POST, OPTIONS)

    Input validation trong Lambda: giới hạn độ dài 200 ký tự cho bộ lọc, 500 ký tự cho câu hỏi chat, chặn ký tự injection

Dự án AWS Serverless Lab — Nhóm thực hiện: Thành viên 1, Thành viên 2, Thành viên 3
