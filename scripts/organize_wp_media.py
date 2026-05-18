#!/usr/bin/env python3

import argparse
import base64
import csv
import html
import json
import mimetypes
import os
import random
import re
import shutil
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

MAX_REST_PER_PAGE = 100
DEFAULT_AI_IMAGE_LIMIT = 100
DEFAULT_OPENAI_MODEL = "gpt-5-mini"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

# Future extension plan: keep category/ai_category stable for now, then add
# main_category and sub_tags columns when multi-tag article-topic grouping starts.
AI_CATEGORIES = [
    "car/lexus",
    "car/tanto",
    "genre/fishing",
    "genre/leisure",
    "genre/travel",
    "genre/gourmet",
    "location/hiroshima",
    "location/shimane",
    "location/tottori",
    "location/yamaguchi",
    "location/fukuoka",
    "location/oita",
    "location/awaji",
    "bird",
    "aquarium",
    "unknown",
]


CATEGORY_RULES = [
    ("car/lexus", ["lexus", "ux", "f sport", "fsport"]),
    ("car/tanto", ["tanto", "タント"]),
    ("genre/fishing", ["fish", "fishing", "釣", "ナマズ", "catfish"]),
    ("genre/gourmet", ["food", "gourmet", "グルメ", "cafe", "café", "カフェ"]),
    ("genre/travel", ["travel", "trip", "shrine", "temple", "鳥居", "神社", "寺"]),
    ("genre/leisure", ["leisure", "レジャー", "camp", "camping", "キャンプ"]),
    ("genre/car", ["car", "車", "クルマ", "ドライブ"]),
    ("location/hiroshima", ["hiroshima", "広島", "miyajima", "宮島", "itsukushima", "厳島"]),
    ("location/shimane", ["shimane", "島根", "izumo", "出雲"]),
    ("location/tottori", ["tottori", "鳥取", "sand", "dunes", "砂丘"]),
    ("location/yamaguchi", ["yamaguchi", "山口"]),
    ("location/fukuoka", ["fukuoka", "福岡"]),
    ("location/oita", ["oita", "ooita", "大分"]),
    ("location/awaji", ["awaji", "淡路"]),
]


def guess_category(text):
    text = (text or "").lower()

    for category, keywords in CATEGORY_RULES:
        if any(keyword in text for keyword in keywords):
            return category

    return "unknown"


def guess_article(category):
    mapping = {
        "car/lexus": "レクサスUXドライブ記事候補",
        "car/tanto": "タントおでかけ記事候補",
        "genre/car": "車・ドライブ記事候補",
        "genre/fishing": "釣り体験記事候補",
        "genre/leisure": "レジャー・おでかけ記事候補",
        "genre/travel": "旅行・観光記事候補",
        "genre/gourmet": "グルメ記事候補",
        "location/hiroshima": "広島観光まとめ候補",
        "location/shimane": "島根旅行記事候補",
        "location/tottori": "鳥取旅行記事候補",
        "location/yamaguchi": "山口旅行記事候補",
        "location/fukuoka": "福岡グルメ・観光記事候補",
        "location/oita": "大分旅行記事候補",
        "location/awaji": "淡路島おでかけ記事候補",
        "bird": "鳥・野鳥観察記事候補",
        "aquarium": "水族館記事候補",
        "unknown": "未分類記事候補",
    }
    return mapping.get(category, "未分類記事候補")


def safe_filename(value, fallback):
    name = unquote((value or "").split("?", 1)[0].split("#", 1)[0])
    name = Path(name).name or fallback
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    return name or fallback


def split_image_dimensions(dimensions):
    if not dimensions or "x" not in dimensions:
        return "", ""
    width, height = dimensions.split("x", 1)
    return width, height


def copy_ai_preview_image(thumbnail_path, preview_dir, idx, filename):
    if not thumbnail_path or not thumbnail_path.is_file():
        return ""

    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_name = f"{idx:03d}-{safe_filename(filename, f'ai-preview-{idx}.jpg')}"
    preview_path = preview_dir / preview_name
    try:
        shutil.copy2(thumbnail_path, preview_path)
    except Exception:
        return ""
    return preview_path.name


def write_ai_preview_index(preview_dir, preview_rows):
    preview_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "<!doctype html>",
        '<html lang="ja">',
        "<head>",
        '  <meta charset="utf-8">',
        "  <title>AI分類対象プレビュー</title>",
        "  <style>",
        "    body { font-family: sans-serif; margin: 24px; }",
        "    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 16px; }",
        "    .card { border: 1px solid #ddd; border-radius: 8px; padding: 8px; background: #fff; }",
        "    img { max-width: 100%; height: auto; display: block; margin-bottom: 8px; }",
        "    .meta { font-size: 12px; overflow-wrap: anywhere; }",
        "  </style>",
        "</head>",
        "<body>",
        "  <h1>AI分類対象プレビュー</h1>",
        f"  <p>AI分類対象画像: {len(preview_rows)} 件</p>",
        '  <div class="grid">',
    ]
    for row in preview_rows:
        preview_name = html.escape(row.get("preview_name", ""))
        filename = html.escape(row.get("filename", ""))
        dimensions = html.escape(row.get("ai_image_dimensions", ""))
        ai_category = html.escape(row.get("ai_category", ""))
        ai_status = html.escape(row.get("ai_status", ""))
        ai_reason = html.escape(row.get("ai_reason", ""))
        lines.extend(
            [
                '    <div class="card">',
                f'      <img src="{preview_name}" alt="{filename}">',
                '      <div class="meta">',
                f"        <strong>{filename}</strong><br>",
                f"        size: {dimensions}<br>",
                f"        category: {ai_category}<br>",
                f"        status: {ai_status}<br>",
                f"        reason: {ai_reason}",
                "      </div>",
                "    </div>",
            ]
        )
    lines.extend(["  </div>", "</body>", "</html>"])
    (preview_dir / "index.html").write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_preferred_image_url(item):
    sizes = item.get("media_details", {}).get("sizes", {}) or {}
    for size_name in ("medium_large", "medium", "large"):
        source_url = sizes.get(size_name, {}).get("source_url")
        if source_url:
            return source_url

    source_url = item.get("source_url", "")
    if source_url:
        return source_url

    return sizes.get("thumbnail", {}).get("source_url", "")


def safe_url_for_request(url):
    parts = urlsplit(url)
    path = quote(parts.path, safe="/%")
    query = quote(parts.query, safe="=&%/:;+,@?")
    fragment = quote(parts.fragment, safe="=&%/:;+,@?")

    netloc = parts.netloc
    if parts.hostname:
        host = parts.hostname.encode("idna").decode("ascii")
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"

        auth = ""
        if parts.username:
            auth = quote(parts.username, safe="%")
            if parts.password:
                auth += ":" + quote(parts.password, safe="%")
            auth += "@"

        port = f":{parts.port}" if parts.port else ""
        netloc = f"{auth}{host}{port}"

    return urlunsplit((parts.scheme, netloc, path, query, fragment))


def build_request(url, token=None, wp_url=None):
    safe_url = safe_url_for_request(url)
    request = Request(safe_url, method="GET")
    if token and wp_url:
        if urlparse(safe_url).netloc == urlparse(safe_url_for_request(wp_url)).netloc:
            request.add_header("Authorization", f"Basic {token}")
    return request


def fetch_url(request, timeout, retries):
    attempts = max(1, retries + 1)
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.status, response.read(), None
        except HTTPError as error:
            return error.code, error.read(), str(error)
        except URLError as error:
            last_error = str(error)
        except TimeoutError as error:
            last_error = str(error)
        except Exception as error:
            last_error = f"{type(error).__name__}: {error}"

        if attempt < attempts:
            time.sleep(min(2 ** (attempt - 1), 5))

    return "error", b"", last_error or "request failed"


def get_image_dimensions(image_path):
    try:
        data = image_path.read_bytes()
    except Exception:
        return ""

    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return f"{width}x{height}"

    if len(data) >= 10 and data[:6] in {b"GIF87a", b"GIF89a"}:
        width = int.from_bytes(data[6:8], "little")
        height = int.from_bytes(data[8:10], "little")
        return f"{width}x{height}"

    if len(data) >= 4 and data[:2] == b"\xff\xd8":
        offset = 2
        while offset + 9 < len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker = data[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
                continue
            if offset + 2 > len(data):
                break
            segment_length = int.from_bytes(data[offset:offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(data):
                break
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }:
                height = int.from_bytes(data[offset + 3:offset + 5], "big")
                width = int.from_bytes(data[offset + 5:offset + 7], "big")
                return f"{width}x{height}"
            offset += segment_length

    if len(data) >= 30 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk_type = data[12:16]
        if chunk_type == b"VP8X" and len(data) >= 30:
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
            return f"{width}x{height}"
        if chunk_type == b"VP8 " and len(data) >= 30:
            width = int.from_bytes(data[26:28], "little") & 0x3FFF
            height = int.from_bytes(data[28:30], "little") & 0x3FFF
            return f"{width}x{height}"
        if chunk_type == b"VP8L" and len(data) >= 25:
            bits = int.from_bytes(data[21:25], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
            return f"{width}x{height}"

    return ""


def image_file_to_data_url(image_path):
    mime_type, _ = mimetypes.guess_type(image_path.name)
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def extract_response_text(response_data):
    output_text = response_data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    output_parts = []

    def collect_text(value):
        if isinstance(value, dict):
            item_type = value.get("type")
            text = value.get("text")
            if item_type in {"output_text", "text", "refusal"} and isinstance(text, str):
                output_parts.append(text)
                return
            for child in value.values():
                collect_text(child)
        elif isinstance(value, list):
            for child in value:
                collect_text(child)

    collect_text(response_data.get("output", []))
    return "\n".join(part for part in output_parts if part)


def strip_markdown_json_fence(response_text):
    cleaned = (response_text or "").strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip(), "markdown_json_fence"
    return cleaned, "plain_text"


def parse_ai_category_response(response_text):
    cleaned, parse_status = strip_markdown_json_fence(response_text)
    parsed = None
    parse_error = ""

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as error:
        parse_error = f"JSONDecodeError: {error}"
        json_match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group(0))
                parse_status = "json_substring"
                parse_error = ""
            except json.JSONDecodeError as substring_error:
                parse_error = f"JSONDecodeError after substring extraction: {substring_error}"

    if not isinstance(parsed, dict):
        fallback_reason = cleaned[:300] or "AI response was not valid JSON"
        return "unknown", fallback_reason, "parse_error", parse_error or "AI response was not a JSON object"

    raw_category = str(parsed.get("category", "unknown")).strip()
    normalized_category = raw_category.lower()
    if normalized_category not in AI_CATEGORIES:
        reason = str(parsed.get("reason", "")).strip() or "No reason returned"
        return (
            "unknown",
            reason[:300],
            "invalid_category",
            f"category {raw_category!r} is not in allowed categories",
        )

    reason = str(parsed.get("reason", "")).strip()
    if not reason:
        reason = "No reason returned"
    return normalized_category, reason[:300], parse_status, ""


def classify_thumbnail_with_openai(image_path, model, api_key, timeout):
    data_url = image_file_to_data_url(image_path)
    categories = ", ".join(AI_CATEGORIES)
    prompt = (
        "You classify a small thumbnail for a Japanese life-log media database. "
        "Choose exactly one best category from this list: "
        f"{categories}. "
        "Be proactive: estimate the closest category even from partial visual clues. "
        "Use unknown only as a last resort when there are no useful visual clues at all. "
        "Prefer these mappings: torii/shrine/temple -> genre/travel; "
        "sea/beach/sand/outdoor play -> genre/leisure; "
        "Lexus car interior or Lexus vehicle -> car/lexus; Tanto vehicle -> car/tanto; "
        "fish/fishing rods/tackle/catch -> genre/fishing; food/restaurant/cafe -> genre/gourmet; "
        "bird -> bird; aquarium/fish tank/exhibited fish -> aquarium; "
        "deer/animals at tourist spots -> genre/travel unless bird is clearly the main subject. "
        "When location-specific evidence is visible or strongly implied, choose the best location category. "
        "Return JSON only: "
        '{"category":"...","reason":"short reason in Japanese"}'
    )
    payload = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": data_url, "detail": "low"},
                ],
            }
        ],
        "max_output_tokens": 200,
    }
    request = Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urlopen(request, timeout=timeout) as response:
        response_data = json.loads(response.read().decode("utf-8"))

    response_text = extract_response_text(response_data)
    category, reason, parse_status, parse_error = parse_ai_category_response(response_text)
    debug_data = {
        "model": model,
        "image": image_path.name,
        "response_text": response_text,
        "parse_status": parse_status,
        "parse_error": parse_error,
        "parsed_category": category,
        "parsed_reason": reason,
        "raw_response": response_data,
    }
    return category, reason, parse_status, parse_error, response_text, debug_data


def maybe_classify_thumbnail(image_path, model, api_key, timeout):
    if not api_key:
        return "unknown", "OPENAI_API_KEY is not set", "skipped", "", "", "", {}
    if not image_path or not image_path.is_file():
        return "unknown", "thumbnail file is missing", "skipped", "", "", "", {}

    try:
        category, reason, parse_status, parse_error, response_text, debug_data = classify_thumbnail_with_openai(image_path, model, api_key, timeout)
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")[:1000]
        debug_data = {
            "model": model,
            "image": image_path.name,
            "http_status": error.code,
            "error_body": error_body,
        }
        return "unknown", f"OpenAI HTTP {error.code}: {error_body[:300]}", "error", "http_error", error_body, "", debug_data
    except URLError as error:
        return "unknown", f"OpenAI URL error: {error}", "error", "url_error", str(error), "", {}
    except TimeoutError as error:
        return "unknown", f"OpenAI timeout: {error}", "error", "timeout", str(error), "", {}
    except Exception as error:
        return "unknown", f"OpenAI error: {type(error).__name__}: {error}", "error", "exception", str(error), "", {}

    return category, reason, "classified", parse_status, parse_error, response_text, debug_data


def write_ai_debug_json(debug_dir, idx, filename, debug_data):
    if not debug_data:
        return ""

    debug_dir.mkdir(parents=True, exist_ok=True)
    debug_name = f"{idx:03d}-{safe_filename(filename, f'ai-debug-{idx}.json')}.json"
    debug_path = debug_dir / debug_name
    debug_path.write_text(
        json.dumps(debug_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return debug_path.name


def download_thumbnail(item, idx, thumbnails_dir, token, wp_url, timeout, retries):
    image_url = get_preferred_image_url(item)
    if not image_url:
        return "", "", "missing_url", "No image URL found"

    media_id = item.get("id") or idx
    filename = safe_filename(urlparse(image_url).path, f"media-{media_id}.jpg")
    local_path = thumbnails_dir / f"{idx:03d}-{media_id}-{filename}"

    try:
        safe_image_url = safe_url_for_request(image_url)
        request = build_request(image_url, token=token, wp_url=wp_url)
    except Exception as error:
        return image_url, "", "invalid_url", f"{type(error).__name__}: {error}"

    status, body, error = fetch_url(request, timeout=timeout, retries=retries)
    if error or status != 200:
        return safe_image_url, "", str(status), error or f"HTTP {status}"

    try:
        local_path.write_bytes(body)
    except Exception as error:
        return safe_image_url, "", "write_error", f"{type(error).__name__}: {error}"

    return safe_image_url, local_path.as_posix(), str(status), ""


def fetch_media_page(wp_url, token, remaining_limit, page, timeout):
    if remaining_limit is None:
        per_page = MAX_REST_PER_PAGE
    else:
        per_page = min(MAX_REST_PER_PAGE, remaining_limit)
    api_url = (
        f"{wp_url}/wp-json/wp/v2/media?"
        + urlencode(
            {
                "per_page": per_page,
                "page": page,
                "orderby": "date",
                "order": "desc",
                "media_type": "image",
            }
        )
    )

    request = Request(api_url, method="GET")
    request.add_header("Authorization", f"Basic {token}")

    try:
        with urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
            return api_url, data, str(response.status)
    except HTTPError as error:
        if error.code == 400 and page > 1:
            return api_url, [], str(error.code)
        raise


def fetch_media_items(wp_url, token, limit, timeout):
    items = []
    run_log_rows = []
    page = 1

    while limit is None or len(items) < limit:
        remaining = None if limit is None else limit - len(items)
        api_url, page_items, status = fetch_media_page(
            wp_url,
            token,
            remaining,
            page,
            timeout,
        )
        run_log_rows.append(
            {
                "method": "GET",
                "url": api_url,
                "status": status,
                "error": "",
            }
        )

        if not page_items:
            break

        if remaining is None:
            items.extend(page_items)
        else:
            items.extend(page_items[:remaining])

        if len(page_items) < MAX_REST_PER_PAGE:
            break

        page += 1

    return items, run_log_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--latest-20", action="store_true")
    parser.add_argument("--limit", default="300")
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--skip-posts", action="store_true")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--ai-classify", action="store_true")
    parser.add_argument("--ai-limit", type=int, default=DEFAULT_AI_IMAGE_LIMIT)
    parser.add_argument("--ai-model", default=os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL))

    args = parser.parse_args()
    if args.ai_limit < 0:
        raise SystemExit("--ai-limit must be zero or a positive integer")
    if str(args.limit).lower() == "all":
        requested_limit = "all"
        fetch_limit = None
    else:
        try:
            requested_limit = int(args.limit)
        except ValueError as error:
            raise SystemExit("--limit must be a positive integer or 'all'") from error
        if requested_limit < 1:
            raise SystemExit("--limit must be a positive integer or 'all'")
        fetch_limit = requested_limit

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    thumbnails_dir = output_dir / "thumbnails"
    if not args.no_download:
        thumbnails_dir.mkdir(parents=True, exist_ok=True)

    wp_url = os.environ["WP_URL"].rstrip("/")
    wp_user = os.environ["WP_USER"]
    wp_password = os.environ["WP_APP_PASSWORD"]

    token = base64.b64encode(
        f"{wp_user}:{wp_password}".encode()
    ).decode()

    data, run_log_rows = fetch_media_items(
        wp_url,
        token,
        fetch_limit,
        args.timeout,
    )

    image_rows = []
    group_rows = []
    ai_log_rows = []
    thumbnail_saved_count = 0
    thumbnail_error_count = 0
    ai_classified_count = 0
    ai_reclassified_count = 0
    ai_error_count = 0
    openai_api_key = os.environ.get("OPENAI_API_KEY", "")
    ai_candidate_indexes = set()
    if args.ai_classify and args.ai_limit > 0:
        unknown_candidate_indexes = []
        for candidate_idx, candidate_item in enumerate(data, start=1):
            candidate_title = (
                candidate_item.get("title", {})
                .get("rendered", "")
                .strip()
            )
            candidate_alt = candidate_item.get("alt_text", "")
            candidate_source_url = candidate_item.get("source_url", "")
            candidate_filename = safe_filename(
                urlparse(candidate_source_url).path,
                f"media-{candidate_item.get('id') or candidate_idx}",
            )
            candidate_category = guess_category(
                f"{candidate_title} {candidate_alt} {candidate_filename} {candidate_source_url}"
            )
            if candidate_category == "unknown":
                unknown_candidate_indexes.append(candidate_idx)

        sample_count = min(args.ai_limit, len(unknown_candidate_indexes))
        ai_candidate_indexes = set(random.sample(unknown_candidate_indexes, sample_count))

    ai_preview_dir = output_dir / "ai-preview"
    ai_debug_dir = output_dir / "ai-debug"
    ai_preview_rows = []
    if args.ai_classify:
        ai_preview_dir.mkdir(parents=True, exist_ok=True)
        ai_debug_dir.mkdir(parents=True, exist_ok=True)

    for idx, item in enumerate(data, start=1):
        title = (
            item.get("title", {})
            .get("rendered", "")
            .strip()
        )

        alt = item.get("alt_text", "")
        source_url = item.get("source_url", "")

        filename = safe_filename(urlparse(source_url).path, f"media-{item.get('id') or idx}")

        text_category = guess_category(
            f"{title} {alt} {filename} {source_url}"
        )
        category = text_category
        ai_category = "unknown" if args.ai_classify else ""
        ai_reason = "text category already matched; AI skipped" if args.ai_classify and text_category != "unknown" else ""
        ai_status = "skipped" if args.ai_classify and text_category != "unknown" else "not_requested"
        ai_parse_status = ""
        ai_parse_error = ""
        ai_response_text = ""
        ai_debug_path = ""

        local_path = ""
        thumbnail_path = None
        ai_image_dimensions = ""

        if not args.no_download:
            thumb_url, saved_path, status, error = download_thumbnail(
                item,
                idx,
                thumbnails_dir,
                token,
                wp_url,
                args.timeout,
                args.retries,
            )
            if saved_path:
                thumbnail_path = Path(saved_path)
                local_path = str(thumbnail_path.relative_to(output_dir))
                ai_image_dimensions = get_image_dimensions(thumbnail_path)
                thumbnail_saved_count += 1
            else:
                thumbnail_error_count += 1
            run_log_rows.append(
                {
                    "method": "GET",
                    "url": thumb_url,
                    "status": status,
                    "error": error,
                }
            )

        preview_name = ""
        if args.ai_classify and text_category == "unknown":
            if idx in ai_candidate_indexes:
                preview_name = copy_ai_preview_image(
                    thumbnail_path,
                    ai_preview_dir,
                    idx,
                    filename,
                )
                (
                    ai_category,
                    ai_reason,
                    ai_status,
                    ai_parse_status,
                    ai_parse_error,
                    ai_response_text,
                    ai_debug_data,
                ) = maybe_classify_thumbnail(
                    thumbnail_path,
                    args.ai_model,
                    openai_api_key,
                    args.timeout,
                )
                ai_debug_name = write_ai_debug_json(ai_debug_dir, idx, filename, ai_debug_data)
                ai_debug_path = f"ai-debug/{ai_debug_name}" if ai_debug_name else ""
                ai_classified_count += 1
                if ai_status == "error":
                    ai_error_count += 1
                if ai_category != "unknown":
                    category = ai_category
                    ai_reclassified_count += 1
            else:
                ai_category = "unknown"
                ai_reason = "not selected for random AI sample"
                ai_status = "skipped"

        if args.ai_classify:
            width, height = split_image_dimensions(ai_image_dimensions)
            ai_log_rows.append(
                {
                    "filename": filename,
                    "width": width,
                    "height": height,
                    "ai_category": ai_category or "unknown",
                    "ai_reason": ai_reason,
                    "local_path": local_path,
                    "preview_path": f"ai-preview/{preview_name}" if preview_name else "",
                    "text_category": text_category,
                    "ai_image_dimensions": ai_image_dimensions,
                    "final_category": category,
                    "ai_status": ai_status,
                    "ai_parse_status": ai_parse_status,
                    "ai_parse_error": ai_parse_error,
                    "ai_response_text": ai_response_text[:1000],
                    "ai_debug_path": ai_debug_path,
                }
            )
            if preview_name:
                ai_preview_rows.append(
                    {
                        "filename": filename,
                        "preview_name": preview_name,
                        "ai_image_dimensions": ai_image_dimensions,
                        "ai_category": ai_category or "unknown",
                        "ai_status": ai_status,
                        "ai_reason": ai_reason,
                    }
                )

        possible_article = guess_article(category)

        image_rows.append(
            {
                "filename": filename,
                "title": title,
                "alt": alt,
                "category": category,
                "ai_category": ai_category,
                "ai_reason": ai_reason,
                "possible_article": possible_article,
                "upload_date": item.get("date", ""),
                "image_url": source_url,
                "local_path": local_path,
            }
        )

        group_rows.append(
            {
                "group_id": f"group-{idx}",
                "match_type": "single",
                "category": category,
                "filenames": filename,
                "image_urls": source_url,
            }
        )

    index_csv = output_dir / "image-index.csv"

    with index_csv.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "filename",
                "title",
                "alt",
                "category",
                "ai_category",
                "ai_reason",
                "possible_article",
                "upload_date",
                "image_url",
                "local_path",
            ],
        )
        writer.writeheader()
        writer.writerows(image_rows)

    groups_csv = output_dir / "image-groups.csv"

    with groups_csv.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "group_id",
                "match_type",
                "category",
                "filenames",
                "image_urls",
            ],
        )
        writer.writeheader()
        writer.writerows(group_rows)

    ai_log_csv = output_dir / "ai-category-log.csv"

    with ai_log_csv.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "filename",
                "width",
                "height",
                "ai_category",
                "ai_reason",
                "local_path",
                "preview_path",
                "text_category",
                "ai_image_dimensions",
                "final_category",
                "ai_status",
                "ai_parse_status",
                "ai_parse_error",
                "ai_response_text",
                "ai_debug_path",
            ],
        )
        writer.writeheader()
        writer.writerows(ai_log_rows)

    if args.ai_classify:
        write_ai_preview_index(ai_preview_dir, ai_preview_rows)

    run_log_csv = output_dir / "media-organizer-run-log.csv"

    with run_log_csv.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "method",
                "url",
                "status",
                "error",
            ],
        )
        writer.writeheader()
        writer.writerows(run_log_rows)

    summary = {
        "requested_limit": requested_limit,
        "fetched_count": len(image_rows),
        "media_count": len(image_rows),
        "group_count": len(group_rows),
        "thumbnail_saved_count": thumbnail_saved_count,
        "thumbnail_error_count": thumbnail_error_count,
        "ai_classification_enabled": args.ai_classify,
        "ai_model": args.ai_model if args.ai_classify else "",
        "ai_limit": args.ai_limit if args.ai_classify else 0,
        "ai_classified_count": ai_classified_count,
        "ai_reclassified_count": ai_reclassified_count,
        "ai_error_count": ai_error_count,
        "ai_preview_count": len(ai_preview_rows),
        "ai_debug_count": len(list(ai_debug_dir.glob("*.json"))) if args.ai_classify and ai_debug_dir.exists() else 0,
        "methods_used": ["GET"],
        "status": "success",
    }

    summary_json = output_dir / "media-organizer-summary.json"

    summary_json.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(
        f"Exported {len(image_rows)} images successfully. "
        f"Saved {thumbnail_saved_count} thumbnails."
    )


if __name__ == "__main__":
    main()