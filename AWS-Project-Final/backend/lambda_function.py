"""
Bep Thong Minh - AWS Lambda API (Teaching & Cooking Assistant)
Runtime: Python 3.12
Handler: lambda_function.lambda_handler
DynamoDB table: MealRecipesTable (partition key RecipeID, String)
Integrations: Amazon DynamoDB, Amazon Location Service, Amazon Bedrock
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
PLACE_INDEX_NAME = os.environ.get("PLACE_INDEX_NAME", "BepThongMinhPlaceIndex")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-micro-v1:0")

MAX_INPUT_LENGTH = 200
MAX_KEYWORDS = 10

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)
location_client = boto3.client("location", region_name=REGION)
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


# 1. TÌM ĐIỂM MUA NGUYÊN LIỆU GẦN VỊ TRÍ & PHÙ HỢP TÚI TIỀN (AMAZON LOCATION SERVICE)
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
        res = location_client.search_place_index_for_text(
            IndexName=PLACE_INDEX_NAME,
            Text=selected["search_text"],
            BiasPosition=[lng, lat],
            MaxResults=5,
            Language="vi",
        )
        stores = []
        for entry in res.get("Results", []):
            place = entry.get("Place", {})
            coords = place.get("Geometry", {}).get("Point", [lng, lat])
            dist_m = haversine_meters(lat, lng, coords[1], coords[0])
            stores.append({
                "name": place.get("Label", "Điểm bán thực phẩm").split(",")[0],
                "address": place.get("Label", ""),
                "distanceMeters": dist_m,
                "lat": coords[1],
                "lng": coords[0],
            })
        stores.sort(key=lambda s: s["distanceMeters"])
        return response(200, {
            "source": "amazon-location-service",
            "advice": selected["advice"],
            "stores": stores,
        })
    except Exception as exc:
        logger.warning("Amazon Location Service fallback triggered: %s", exc)
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
        "Người dùng có thể hỏi gợi ý món ăn
