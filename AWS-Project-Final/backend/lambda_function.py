"""
Bep Thong Minh - AWS Lambda API (Teaching & Cooking Assistant)
Runtime: Python 3.12
Handler: lambda_function.lambda_handler
DynamoDB table: MealRecipesTable (partition key RecipeID, String)
Integrations: Amazon DynamoDB, Amazon Location Service (geo-places v2), Amazon Bedrock
Supports API Gateway REST API v1 and HTTP API v2 events.
"""
import base64
import json
import logging
import math
import os
import random
import unicodedata
from decimal import Decimal
from urllib.parse import parse_qs

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "MealRecipesTable")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-micro-v1:0")

MAX_INPUT_LENGTH = 200
MAX_KEYWORDS = 10

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)
geo_places_client = boto3.client("geo-places", region_name=REGION)
bedrock_client = boto3.client("bedrock-runtime", region_name=REGION)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Content-Type": "application/json; charset=utf-8",
}


def json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body, ensure_ascii=False, default=json_default),
    }


def normalize(value):
    text = str(value or "").strip().lower()
    text = text.replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def csv_values(value, field_name):
    if not value:
        return []
    if len(value) > MAX_INPUT_LENGTH:
        raise ValueError(f"Tham số {field_name} vượt quá {MAX_INPUT_LENGTH} ký tự.")
    values = [part.strip() for part in value.split(",") if part.strip()]
    if any(len(part) > 60 for part in values):
        raise ValueError(f"Mỗi giá trị trong {field_name} tối đa 60 ký tự.")
    return [v for v in values[:MAX_KEYWORDS] if not any(c in v for c in "<>{}$`\\;")]


def query_params(event):
    params = event.get("queryStringParameters") or {}
    if params:
        return params
    raw = event.get("rawQueryString", "")
    return {k: v[0] for k, v in parse_qs(raw).items() if v}


def parse_json_body(event):
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    try:
        return json.loads(body)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("Dữ liệu gửi lên không phải JSON hợp lệ.")


def get_all_recipes():
    items = []
    kwargs = {}
    while True:
        result = table.scan(**kwargs)
        items.extend(result.get("Items", []))
        last_key = result.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs = {"ExclusiveStartKey": last_key}
    items.sort(key=lambda x: str(x.get("RecipeID", "")))
    logger.info("Loaded %s recipes from %s", len(items), TABLE_NAME)
    return items


def has_any(values, search_terms):
    normalized_values = [normalize(v) for v in values]
    for term in search_terms:
        t = normalize(term)
        if any(t in v or v in t for v in normalized_values if v and t):
            return True
    return False


def has_tag(recipe, field, tag):
    return tag in (recipe.get(field) or [])


def matches(recipe, filters):
    ingredients = recipe.get("Ingredients") or []
    allergens = recipe.get("Allergens") or []
    nutrition = recipe.get("Nutrition") or {}

    if filters["ingredients"] and not has_any(ingredients, filters["ingredients"]):
        return False
    if filters["health"] and not has_tag(recipe, "HealthTags", filters["health"]):
        return False
    if filters["need"] and not (
        has_tag(recipe, "HealthTags", filters["need"])
        or has_tag(recipe, "DietTags", filters["need"])
    ):
        return False
    if filters["mood"] and not has_tag(recipe, "MoodTags", filters["mood"]):
        return False
    if filters["allergies"] and (
        has_any(ingredients, filters["allergies"])
        or has_any(allergens, filters["allergies"])
    ):
        return False
    if filters["avoid"] and has_any(ingredients, filters["avoid"]):
        return False

    numeric_rules = (
        ("maxTime", "CookingTimeMinutes"),
        ("maxBudget", "EstimatedCostVND"),
    )
    for param, field in numeric_rules:
        if filters[param] is not None:
            try:
                value = float(recipe.get(field))
            except (TypeError, ValueError):
                return False
            if value > filters[param]:
                return False

    for param, field in (("maxSodium", "SodiumMg"), ("maxCarbs", "CarbsG")):
        if filters[param] is not None:
            try:
                value = float(nutrition[field])
            except (KeyError, TypeError, ValueError):
                return False
            if value > filters[param]:
                return False
    return True


def ingredient_score(recipe, keywords):
    ingredients = [normalize(v) for v in (recipe.get("Ingredients") or [])]
    return sum(1 for term in keywords if any(normalize(term) in ing for ing in ingredients))


def haversine_meters(lat1, lon1, lat2, lon2):
    r = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return int(2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


# 1. TÌM ĐIỂM MUA NGUYÊN LIỆU GẦN VỊ TRÍ & PHÙ HỢP TÚI TIỀN (AMAZON LOCATION GEO-PLACES V2)
def handle_nearby_stores(params):
    ingredients = (params.get("ingredients") or "thực phẩm tươi").strip()
    if len(ingredients) > MAX_INPUT_LENGTH:
        raise ValueError(f"Tham số ingredients vượt quá {MAX_INPUT_LENGTH} ký tự.")
    tier = (params.get("tier") or "mid").strip().lower()

    try:
        lat = float(params.get("lat", 10.7769))
        lng = float(params.get("lng", 106.7009))
    except (TypeError, ValueError):
        lat, lng = 10.7769, 106.7009

    tier_config = {
        "low": {
            "search_text": f"chợ truyền thống tạp hóa {ingredients}",
            "advice": "Ngân sách tiết kiệm (< 35.000đ): Ưu tiên chợ truyền thống hoặc tạp hóa để mua lẻ đúng định lượng.",
        },
        "mid": {
            "search_text": "Bách Hóa Xanh WinMart+ Co.op Food",
            "advice": "Ngân sách vừa phải (35.000đ – 70.000đ): Siêu thị mini bình ổn giá, tiện lợi và sạch sẽ.",
        },
        "high": {
            "search_text": "siêu thị Co.opmart Tops Market thực phẩm sạch",
            "advice": "Ngân sách thoải mái (> 70.000đ): Siêu thị lớn và cửa hàng thực phẩm hữu cơ chuẩn nguồn gốc.",
        },
    }
    selected = tier_config.get(tier, tier_config["mid"])

    try:
        res = geo_places_client.search_text(
            QueryText=selected["search_text"],
            BiasPosition=[lng, lat],
            MaxResults=5,
            Language="vi",
        )
        stores = []
        for item in res.get("ResultItems", []):
            pos = item.get("Position", [lng, lat])
            addr_label = (item.get("Address") or {}).get("Label") or item.get("Title", "")
            dist_m = item.get("Distance") or haversine_meters(lat, lng, pos[1], pos[0])
            stores.append({
                "name": item.get("Title", "Điểm bán thực phẩm"),
                "address": addr_label,
                "distanceMeters": int(dist_m),
                "lat": pos[1],
                "lng": pos[0],
            })
        stores.sort(key=lambda s: s["distanceMeters"])
        return response(200, {
            "source": "amazon-location-geo-places",
            "advice": selected["advice"],
            "stores": stores,
        })
    except Exception as exc:
        logger.warning("Amazon Location geo-places fallback triggered: %s", exc)
        return response(200, {
            "source": "fallback",
            "advice": selected["advice"],
            "stores": [],
        })


# 2. CHATBOT ĐẦU BẾP AI TÂM LÝ THEO TÂM TRẠNG & THỜI TIẾT (AMAZON BEDROCK)
def find_recipes_by_mood_weather(msg, items):
    norm_msg = normalize(msg)
    target_moods = []
    target_cats = []

    if any(k in norm_msg for k in ["mua", "lanh", "se lanh", "buon", "stress", "ap luc", "am bung", "co don"]):
        target_moods.append("comforting")
        target_cats.extend(["soup", "main"])
    if any(k in norm_msg for k in ["nong", "oi buc", "nang", "ngan", "nhe bung", "giam can", "thanh dam", "mat"]):
        target_moods.extend(["fresh", "light"])
        target_cats.extend(["salad", "side", "soup"])
    if any(k in norm_msg for k in ["met", "duoi", "ue oai", "can nang luong", "doi", "tap gym", "vui"]):
        target_moods.append("energizing")
        target_cats.append("main")

    direct = [
        r for r in items
        if normalize(r.get("Name", "")) in norm_msg
        or any(normalize(ing) in norm_msg for ing in (r.get("Ingredients") or []))
    ]
    if direct:
        return direct

    scored = []
    for r in items:
        score = 0
        r_moods = r.get("MoodTags") or []
        if any(m in r_moods for m in target_moods):
            score += 2
        if r.get("Category") in target_cats:
            score += 1
        if ("chay" in norm_msg or "thanh dam" in norm_msg) and "vegetarian" in (r.get("DietTags") or []):
            score += 3
        if score > 0:
            scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored]


def handle_chat(event):
    payload = parse_json_body(event)
    user_message = str(payload.get("message") or "").strip()
    if not user_message:
        raise ValueError("Vui lòng nhập tâm trạng, thời tiết hoặc câu hỏi của bạn.")
    if len(user_message) > 500:
        raise ValueError("Câu hỏi tối đa 500 ký tự.")

    items = get_all_recipes()
    menu_context = "\n".join([
        f"- [{r.get('RecipeID')}] {r.get('Name')}: loại {r.get('Category')}, "
        f"tâm trạng phù hợp: {', '.join(r.get('MoodTags') or [])}, "
        f"chi phí ~{r.get('EstimatedCostVND')}đ, nấu {r.get('TotalTimeMinutes') or r.get('CookingTimeMinutes', 20)} phút, "
        f"nguyên liệu: {', '.join(r.get('Ingredients') or [])}"
        for r in items
    ])

    system_prompt = (
        "Bạn là Đầu Bếp AI Tâm Lý trên website hướng dẫn dạy nấu ăn Việt Nam. "
        "Người dùng có thể hỏi gợi ý món ăn theo TÂM TRẠNG (vui, buồn, stress, mệt mỏi, cần năng lượng, ăn thanh đạm), "
        "THỜI TIẾT (trời mưa, trời lạnh, nắng nóng, oi bức), ngân sách hoặc nguyên liệu sẵn có.\n"
        "Quy tắc trả lời:\n"
        "1. Đồng cảm nhẹ nhàng với tâm trạng hoặc thời tiết của người dùng (1 câu ngắn).\n"
        "2. Gợi ý 1–2 món phù hợp nhất TỪ DANH SÁCH BÊN DƯỚI (ghi rõ tên món chính xác, chi phí và thời gian nấu), giải thích ngắn gọn vì sao món đó hợp với tâm trạng/thời tiết hiện tại.\n"
        "3. Khích lệ người dùng bấm nút xem hướng dẫn từng bước bên dưới.\n"
        "Trả lời bằng tiếng Việt tự nhiên, ấm áp, ngắn gọn (tối đa 5 câu).\n\n"
        f"Danh sách công thức hiện có:\n{menu_context}"
    )

    matched_recipes = find_recipes_by_mood_weather(user_message, items)
    suggested_id = matched_recipes[0].get("RecipeID") if matched_recipes else None
    suggested_name = matched_recipes[0].get("Name") if matched_recipes else None

    try:
        res = bedrock_client.converse(
            modelId=BEDROCK_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            system=[{"text": system_prompt}],
            inferenceConfig={"maxTokens": 450, "temperature": 0.7},
        )
        reply_text = res["output"]["message"]["content"][0]["text"]
        return response(200, {
            "reply": reply_text,
            "suggestedRecipeId": suggested_id,
            "suggestedRecipeName": suggested_name,
            "source": "amazon-bedrock",
        })
    except Exception as exc:
        logger.warning("Amazon Bedrock fallback triggered: %s", exc)
        if matched_recipes:
            top = matched_recipes[0]
            second = matched_recipes[1] if len(matched_recipes) > 1 else None
            extra = f" hoặc <strong>{second.get('Name')}</strong> (~{second.get('EstimatedCostVND')}đ)" if second else ""
            reply = (
                f"Với thời tiết và tâm trạng hiện tại của bạn, một bữa ăn tự nấu sẽ giúp tinh thần dễ chịu hơn nhiều! "
                f"Mình gợi ý món <strong>{top.get('Name')}</strong> (~{top.get('EstimatedCostVND')}đ, "
                f"{top.get('TotalTimeMinutes') or top.get('CookingTimeMinutes', 20)} phút){extra}. "
                f"Bạn bấm vào nút bên dưới để xem hướng dẫn chi tiết từng bước nhé!"
            )
        else:
            reply = (
                "Dù tâm trạng hay thời tiết hôm nay thế nào, tự vào bếp nấu một món ngon cũng giúp thư giãn rất tốt! "
                "Bạn hãy cho mình biết bạn đang thích ăn món nóng hổi (canh/kho) hay món thanh mát (salad/xào) để mình chọn công thức chuẩn nhất nhé."
            )
        return response(200, {
            "reply": reply,
            "suggestedRecipeId": suggested_id,
            "suggestedRecipeName": suggested_name,
            "source": "fallback",
        })


# 3. TÌM KIẾM CÔNG THỨC NẤU ĂN TỪ DYNAMODB
def handle_recipe_search(params, context):
    filters = {
        "ingredients": csv_values(params.get("ingredients", ""), "ingredients"),
        "allergies": csv_values(params.get("allergies", ""), "allergies"),
        "avoid": csv_values(params.get("avoid", ""), "avoid"),
        "health": (params.get("health") or "").strip(),
        "need": (params.get("need") or "").strip(),
        "mood": (params.get("mood") or "").strip(),
        "maxTime": None,
        "maxBudget": None,
        "maxSodium": None,
        "maxCarbs": None,
    }
    allowed_tags = {
        "health": {"low_sodium", "controlled_carb", "low_saturated_fat", "light", "high_protein"},
        "need": {"high_protein", "vegetarian", "vegan", "low_calorie"},
        "mood": {"comforting", "fresh", "energizing", "light"},
    }
    for key, allowed in allowed_tags.items():
        if filters[key] and filters[key] not in allowed:
            return response(400, {"error": f"Giá trị bộ lọc {key} không hợp lệ."})

    numeric_limits = {
        "maxTime": (600, 0),
        "maxBudget": (10000000, 0),
        "maxSodium": (100000, 0),
        "maxCarbs": (1000, 0),
    }
    for key, (upper, lower) in numeric_limits.items():
        raw = params.get(key)
        if raw not in (None, ""):
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return response(400, {"error": f"Tham số {key} phải là số."})
            if value < lower or value > upper:
                return response(400, {"error": f"Tham số {key} nằm ngoài giới hạn cho phép."})
            filters[key] = value

    logger.info(
        "RequestId=%s filters=%s random=%s",
        getattr(context, "aws_request_id", "local"),
        filters,
        params.get("random") == "true",
    )
    recipes = get_all_recipes()
    matched = [item for item in recipes if matches(item, filters)]
    matched.sort(key=lambda item: ingredient_score(item, filters["ingredients"]), reverse=True)
    if params.get("random", "false").lower() == "true":
        random.shuffle(matched)
        matched = matched[:6]

    return response(200, {
        "recipes": matched,
        "count": len(matched),
        "filters": filters,
        "source": "dynamodb",
    })


def lambda_handler(event, context):
    method = (
        event.get("httpMethod")
        or (event.get("requestContext", {}).get("http") or {}).get("method")
        or "GET"
    ).upper()
    raw_path = event.get("rawPath") or event.get("path") or ""
    route_key = event.get("routeKey") or ""

    if method == "OPTIONS":
        return response(200, {"message": "CORS OK"})

    try:
        if method == "POST" and ("chat" in route_key or raw_path.endswith("/chat")):
            return handle_chat(event)

        if method == "GET" and ("nearby-stores" in route_key or raw_path.endswith("/nearby-stores")):
            return handle_nearby_stores(query_params(event))

        if method == "GET":
            return handle_recipe_search(query_params(event), context)

        return response(405, {"error": "Phương thức HTTP không được hỗ trợ."})
    except ValueError as exc:
        return response(400, {"error": str(exc)})
    except ClientError:
        logger.exception("AWS service request failed")
        return response(500, {"error": "Không truy cập được dịch vụ AWS. Kiểm tra tên bảng MealRecipesTable và IAM Role."})
    except Exception:
        logger.exception("Unhandled Lambda error")
        return response(500, {"error": "Lỗi nội bộ. Xem CloudWatch Logs để biết chi tiết."})
