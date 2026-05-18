#!/usr/bin/env python3

import argparse
import base64
import csv
import json
import mimetypes
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

MAX_REST_PER_PAGE = 100
DEFAULT_AI_IMAGE_LIMIT = 100
DEFAULT_OPENAI_MODEL = "gpt-5-mini"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

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
        "car/lexus": "レクサスUX関連記事",
        "car/tanto": "タント関連記事",
        "genre/car": "車関連記事",
        "genre/fishing": "釣り体験記事",
        "genre/leisure": "レジャー関連記事",
        "genre/travel": "旅行関連記事",
        "genre/gourmet": "グルメ関連記事",
        "location/hiroshima": "広島関連記事",
        "location/shimane": "島根関連記事",
        "location/tottori": "鳥取関連記事",
        "location/yamaguchi": "山口関連記事",
        "location/fukuoka": "福岡関連記事",
        "location/oita": "大分関連記事",
        "location/awaji": "淡路関連記事",
        "bird": "鳥・野鳥関連記事",
        "aquarium": "水槽・魚展示関連記事",
        "unknown": "未分類記事候補",
    }
    return mapping.get(category, "未分類記事候補")


def safe_filename(value, fallback):
    name = unquote((value or "").split("?", 1)[0].split("#", 1)[0])
    name = Path(name).name or fallback
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    return name or fallback


def get_preferred_image_url(item):
    sizes = item.get("media_details", {}).get("sizes", {}) or {}
    for size_name in ("thumbnail", "medium", "medium_large", "large"):
        source_url = sizes.get(size_name, {}).get("source_url")
        if source_url:
            return source_url
    return item.get("source_url", "")


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


def image_file_to_data_url(image_path):
    mime_type, _ = mimetypes.guess_type(image_path.name)
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def extract_response_text(response_data):
    output_text = response_data.get("output_text")
    if output_text:
        return output_text

    output_parts = []
    for output_item in response_data.get("output", []) or []:
        for content_item in output_item.get("content", []) or []:
            if content_item.get("type") in {"output_text", "text"}:
                text = content_item.get("text")
                if text:
                    output_parts.append(text)
    return "\n".join(output_parts)


def parse_ai_category_response(response_text):
    cleaned = (response_text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return "unknown", cleaned[:200] or "AI response was not valid JSON"

    category = parsed.get("category", "unknown")
    if category not in AI_CATEGORIES:
        category = "unknown"

    reason = str(parsed.get("reason", "")).strip()
    if not reason:
        reason = "No reason returned"
    return category, reason[:300]


def classify_thumbnail_with_openai(image_path, model, api_key, timeout):
    data_url = image_file_to_data_url(image_path)
    categories = ", ".join(AI_CATEGORIES)
    prompt = (
        "You classify a small thumbnail for a Japanese life-log media database. "
        "Choose exactly one category from this list: "
        f"{categories}. "
        "Prefer these mappings: torii/shrine/temple -> genre/travel; "
        "sea/beach/sand/outdoor play -> genre/leisure; "
        "Lexus car interior or Lexus vehicle -> car/lexus; Tanto vehicle -> car/tanto; "
        "fish/fishing rods/tackle/catch -> genre/fishing; food/restaurant/cafe -> genre/gourmet; "
        "bird -> bird; aquarium/fish tank/exhibited fish -> aquarium. "
        "If uncertain, use unknown. Return JSON only: "
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

    return parse_ai_category_response(extract_response_text(response_data))


def maybe_classify_thumbnail(image_path, model, api_key, timeout):
    if not api_key:
        return "unknown", "OPENAI_API_KEY is not set", "skipped"
    if not image_path or not image_path.is_file():
        return "unknown", "thumbnail file is missing", "skipped"

    try:
        category, reason = classify_thumbnail_with_openai(image_path, model, api_key, timeout)
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")[:300]
        return "unknown", f"OpenAI HTTP {error.code}: {error_body}", "error"
    except URLError as error:
        return "unknown", f"OpenAI URL error: {error}", "error"
    except TimeoutError as error:
        return "unknown", f"OpenAI timeout: {error}", "error"
    except Exception as error:
        return "unknown", f"OpenAI error: {type(error).__name__}: {error}", "error"

    return category, reason, "classified"


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

        local_path = ""
        thumbnail_path = None

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

        if args.ai_classify and text_category == "unknown":
            if ai_classified_count < args.ai_limit:
                ai_category, ai_reason, ai_status = maybe_classify_thumbnail(
                    thumbnail_path,
                    args.ai_model,
                    openai_api_key,
                    args.timeout,
                )
                ai_classified_count += 1
                if ai_status == "error":
                    ai_error_count += 1
                if ai_category != "unknown":
                    category = ai_category
                    ai_reclassified_count += 1
            else:
                ai_category = "unknown"
                ai_reason = "AI classification limit reached"
                ai_status = "skipped"

        if args.ai_classify:
            ai_log_rows.append(
                {
                    "filename": filename,
                    "local_path": local_path,
                    "text_category": text_category,
                    "ai_category": ai_category or "unknown",
                    "final_category": category,
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
                "local_path",
                "text_category",
                "ai_category",
                "final_category",
                "ai_status",
                "ai_reason",
            ],
        )
        writer.writeheader()
        writer.writerows(ai_log_rows)

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