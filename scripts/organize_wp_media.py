#!/usr/bin/env python3

import argparse
import base64
import csv
import json
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

MAX_REST_PER_PAGE = 100


def guess_category(text):
    text = (text or "").lower()

    if "ux" in text or "lexus" in text:
        return "lexus/interior"

    if "muv" in text:
        return "muv/exterior"

    if "fish" in text or "釣" in text:
        return "tsurikue/fishing"

    return "unknown"


def guess_article(category):
    mapping = {
        "lexus/interior": "レクサスUX内装レビュー",
        "muv/exterior": "ムーバレー体験記事",
        "tsurikue/fishing": "釣り体験記事",
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

    args = parser.parse_args()
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
    thumbnail_saved_count = 0
    thumbnail_error_count = 0

    for idx, item in enumerate(data, start=1):
        title = (
            item.get("title", {})
            .get("rendered", "")
            .strip()
        )

        alt = item.get("alt_text", "")
        source_url = item.get("source_url", "")

        filename = safe_filename(urlparse(source_url).path, f"media-{item.get('id') or idx}")

        category = guess_category(
            f"{title} {alt} {filename}"
        )

        possible_article = guess_article(category)
        local_path = ""

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
                local_path = str(Path(saved_path).relative_to(output_dir))
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

        image_rows.append(
            {
                "filename": filename,
                "title": title,
                "alt": alt,
                "category": category,
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