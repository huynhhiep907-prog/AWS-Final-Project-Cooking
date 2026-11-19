# 🍽️ Bếp Thông Minh — Hướng Dẫn Triển Khai AWS Serverless
**AWS Serverless Architecture** · Amazon S3 · API Gateway · AWS Lambda · DynamoDB · Amazon Location Service (`geo-places` v2) · Amazon Bedrock

---

## 🎯 Thông Số Hạ Tầng Thực Tế Đã Triển Khai

| Thành phần AWS | Tên / Giá trị cấu hình | Region |
|---|---|---|
| **S3 Static Website** | `http://recipe-suggestion-website.s3-website-ap-southeast-1.amazonaws.com/` | `ap-southeast-1` |
| **API Gateway (HTTP API)** | `https://rb4u5du9z3.execute-api.ap-southeast-1.amazonaws.com/prod` | `ap-southeast-1` |
| **AWS Lambda** | `RecipeSearchFunction` (Python 3.12, Timeout `15s`) | `ap-southeast-1` |
| **IAM Role & Policy** | Role: `RecipesLambdaRole` · Policy: `RecipesLambdaPolicy` | Global |
| **Amazon DynamoDB** | Bảng `RecipesTable` (Partition key: `RecipeID` - String, 20 món `R01`–`R20`) | `ap-southeast-1` |
| **Amazon Location Service** | Places API v2 (`geo-places:SearchText` — không cần tạo Place Index thủ công) | `ap-southeast-1` |
| **Amazon Bedrock** | `amazon.nova-micro-v1:0` (Tự động kích hoạt serverless foundation model) | `ap-southeast-1` |

---

## 🏗️ Kiến Trúc Hệ Thống

```
Người dùng (Trình duyệt / GPS)
    │
    ▼
[Amazon S3 Static Website]  ──► http://recipe-suggestion-website.s3-website-ap-southeast-1.amazonaws.com/
    │
    ├──► GET  /search?ingredients=...&maxBudget=...
    ├──► GET  /nearby-stores?ingredients=...&tier=...&lat=...&lng=...
    └──► POST /chat  {"message": "Trời mưa lạnh nấu gì ấm bụng?"}
    │
    ▼
[Amazon API Gateway (HTTP API - Stage: prod)]  ──► CORS: GET, POST, OPTIONS
    │
    │  Trigger
    ▼
[AWS Lambda: RecipeSearchFunction]  ──► backend/lambda_function.py (Python 3.12)
    │
    ├──► boto3.resource("dynamodb")        ──► [DynamoDB: RecipesTable] (20 món R01–R20)
    ├──► boto3.client("geo-places")        ──► [Amazon Location Places API v2]
    └──► boto3.client("bedrock-runtime")   ──► [Amazon Bedrock: amazon.nova-micro-v1:0]
```

---

## 👥 Phân Công Nhiệm Vụ

| Thành Viên | Phụ Trách Thư Mục | Nhiệm Vụ Chi Tiết |
|---|---|---|
| **Thành viên 1** | `frontend/` | Thiết kế UI dạy nấu ăn (`index.html`), tích hợp `API_BASE`, GPS trình duyệt, đồng hồ đếm ngược từng bước, deploy lên S3 `recipe-suggestion-website`. |
| **Thành viên 2** | `backend/` | Tạo bảng `RecipesTable`, chuẩn hóa dữ liệu 20 món (`seed_data.json`), chạy `seed_recipes.py` qua CloudShell và triển khai `lambda_function.py`. |
| **Thành viên 3** | `security-and-docs/` | Cấu hình IAM Least Privilege Policy (`geo-places` + `bedrock` + `dynamodb`), mở routes & CORS trên API Gateway và hoàn thiện tài liệu. |

---

## 🚀 Quy Trình Triển Khai Từng Bước

### Bước 1 — Tạo Bảng DynamoDB & Nạp 20 Công Thức Qua CloudShell

1. Vào **AWS Console → DynamoDB** (Region `ap-southeast-1` Singapore) → **Create table**:
   * **Table name:** `RecipesTable`
   * **Partition key:** `RecipeID` (`String`)
2. Mở **AWS CloudShell** (biểu tượng `>_` trên thanh điều hướng AWS Console), bấm **Actions → Upload file** để tải 2 file `seed_data.json` (mảng JSON 20 món `R01`–`R20`) và `seed_recipes.py` lên.
3. Chạy lệnh nạp hàng loạt toàn bộ 20 món vào bảng `RecipesTable`:
   ```bash
   python3 -m pip install boto3
   python3 seed_recipes.py --table RecipesTable --file seed_data.json --region ap-southeast-1
   ```
   *(Lưu ý: Nếu cần xóa file cũ trên CloudShell để upload lại, dùng lệnh `rm -f seed_data.json`).*
4. Vào **DynamoDB → Tables → `RecipesTable` → Explore table items** kiểm tra đủ 20 bản ghi (`R01` – `R20`).

---

### Bước 2 — Xác Nhận Amazon Location Service & Amazon Bedrock

1. **Amazon Location Service (`geo-places` v2):** Sử dụng trực tiếp API Places v2 (`geo-places:SearchText`) thông qua quyền IAM, không cần khởi tạo thủ công `Place index` trên Console.
2. **Amazon Bedrock:** Các serverless foundation model (bao gồm `amazon.nova-micro-v1:0`) được tự động kích hoạt khi gọi lần đầu trên Region `ap-southeast-1`.

---

### Bước 3 — Tạo IAM Policy & IAM Role (Least Privilege)

1. Vào **IAM → Policies → Create policy → tab JSON**, dán chính xác cấu hình quyền tối thiểu sau và đặt tên **`RecipesLambdaPolicy`**:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Sid": "ReadRecipesTable",
         "Effect": "Allow",
         "Action": [
           "dynamodb:Scan",
           "dynamodb:Query",
           "dynamodb:GetItem"
         ],
         "Resource": "arn:aws:dynamodb:ap-southeast-1:*:table/RecipesTable"
       },
       {
         "Sid": "SearchNearbyStoresV2",
         "Effect": "Allow",
         "Action": [
           "geo-places:SearchText"
         ],
         "Resource": "*"
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
   ```
2. Vào **IAM → Roles → Create role** → **Trusted entity:** `AWS service` → **Use case:** `Lambda` → gắn policy `RecipesLambdaPolicy` → đặt tên Role: **`RecipesLambdaRole`**.

---

### Bước 4 — Tạo & Cấu Hình AWS Lambda Function

1. Vào **Lambda → Create function** (`ap-southeast-1`):
   * **Function name:** `RecipeSearchFunction`
   * **Runtime:** `Python 3.12`
   * **Execution role:** Use an existing role → `RecipesLambdaRole`
2. Dán toàn bộ mã nguồn `backend/lambda_function.py` (đã tích hợp `boto3.client("geo-places")`) vào tab **Code** và bấm **Deploy**.
3. Vào **Configuration → General configuration → Edit**, đổi **Timeout** từ `3 sec` thành **`15 sec`** → bấm **Save**.
4. Vào **Configuration → Environment variables → Edit**, thêm biến môi trường:
   * **Key:** `DYNAMODB_TABLE` | **Value:** `RecipesTable`
   * **Key:** `BEDROCK_MODEL_ID` | **Value:** `amazon.nova-micro-v1:0`

---

### Bước 5 — Tạo API Gateway (HTTP API) & Cấu Hình CORS

1. Vào **API Gateway → Create API → HTTP API → Build**:
   * **API name:** `BepThongMinhAPI`
   * **Integration:** Lambda → `RecipeSearchFunction` (`ap-southeast-1`)
2. Vào **Develop → Routes**, tạo đầy đủ 3 routes và gắn cùng Integration `RecipeSearchFunction`:
   * `GET /search`
   * `GET /nearby-stores`
   * `POST /chat`
3. Vào **Develop → CORS → Configure**:
   * **Access-Control-Allow-Origin:** `*`
   * **Access-Control-Allow-Methods:** `GET`, `POST`, `OPTIONS`
   * **Access-Control-Allow-Headers:** `Content-Type, Authorization`
4. Vào **Deploy → Stages**, tạo/chọn stage **`prod`** (bật Auto-deploy) và lấy **Invoke URL**:
   `https://rb4u5du9z3.execute-api.ap-southeast-1.amazonaws.com/prod`

---

### Bước 6 — Cập Nhật Frontend & Deploy Lên Amazon S3

1. Mở file `frontend/index.html`, kiểm tra dòng `API_BASE` khớp chính xác với Invoke URL của API Gateway:
   ```javascript
   const API_BASE = "https://rb4u5du9z3.execute-api.ap-southeast-1.amazonaws.com/prod";
   ```
2. Vào **S3 → Create bucket** (`ap-southeast-1`), đặt tên **`recipe-suggestion-website`**, bỏ tích **Block all public access** và xác nhận.
3. Vào tab **Properties → Static website hosting → Edit**, chọn **Enable** và nhập **Index document:** `index.html`.
4. Vào tab **Permissions → Bucket policy → Edit**, dán policy đọc công khai:
   ```json
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
   ```
5. Upload file `frontend/index.html` lên thư mục gốc của bucket và truy cập địa chỉ:
   `http://recipe-suggestion-website.s3-website-ap-southeast-1.amazonaws.com/`

---

## ✅ Bảng Kiểm Tra Nghiệm Thu Hệ Thống

| Hạng mục | Cách kiểm tra | Kết quả mong đợi |
|---|---|---|
| **DynamoDB** | Mở bảng `RecipesTable` → Explore items | Hiển thị đủ 20 công thức (`R01` – `R20`) |
| **API Tìm kiếm** | Gọi `GET /search?ingredients=trứng` | HTTP 200, trả về danh sách món chứa trứng |
| **Location Places v2** | Gọi `GET /nearby-stores?ingredients=thịt&tier=mid&lat=10.7769&lng=106.7009` | HTTP 200, trả về danh sách điểm bán kèm khoảng cách `distanceMeters` |
| **Amazon Bedrock** | Gọi `POST /chat` với `{"message": "Trời mưa lạnh ăn gì?"}` | HTTP 200, trả về lời khuyên từ AI và `suggestedRecipeId` |
| **S3 Website** | Truy cập `http://recipe-suggestion-website.s3-website-ap-southeast-1.amazonaws.com/` | Hiển thị đầy đủ 20 món, bộ lọc ngân sách, đồng hồ đếm giờ bước nấu và chatbot AI |
| **CloudWatch Logs** | Lambda → Monitor → View CloudWatch logs | Ghi nhận `Loaded 20 recipes from RecipesTable`, không phát sinh lỗi phân quyền |

---

## 📁 Cấu Trúc Thư Mục Dự Án

```
AWS-Project-Final/
├── frontend/
│   └── index.html              # Giao diện dạy nấu ăn (HTML5 + Tailwind CSS + Vanilla JS)
├── backend/
│   ├── lambda_function.py      # AWS Lambda API (DynamoDB + Location geo-places v2 + Bedrock)
│   ├── seed_data.json          # Bộ dữ liệu 20 công thức nấu ăn chuẩn hóa (R01–R20)
│   └── seed_recipes.py         # Script Python nạp hàng loạt dữ liệu vào RecipesTable
└── security-and-docs/
    ├── iam_policy.json         # IAM Policy phân quyền tối thiểu (Least Privilege)
    └── README.md               # Tài liệu tổng hợp kiến trúc và hướng dẫn triển khai
```