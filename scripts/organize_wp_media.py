#!/usr/bin/env python3

import argparse
import base64
import csv
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--latest-20", action="store_true")
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--skip-posts", action="store_true")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=1)

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    wp_url = os.environ["WP_URL"].rstrip("/")
    wp_user = os.environ["WP_USER"]
    wp_password = os.environ["WP_APP_PASSWORD"]

    api_url = (
        f"{wp_url}/wp-json/wp/v2/media?"
        + urlencode(
            {
                "per_page": 20,
                "orderby": "date",
                "order": "desc",
                "media_type": "image",
            }
        )
    )

    token = base64.b64encode(
        f"{wp_user}:{wp_password}".encode()
    ).decode()

    request = Request(api_url)
    request.add_header("Authorization", f"Basic {token}")

    with urlopen(request, timeout=args.timeout) as response:
        data = json.loads(response.read().decode("utf-8"))

    image_rows = []
    group_rows = []
    run_log_rows = []

    run_log_rows.append(
        {
            "method": "GET",
            "url": api_url,
            "status": "200",
        }
    )

    for idx, item in enumerate(data, start=1):
        title = (
            item.get("title", {})
            .get("rendered", "")
            .strip()
        )

        alt = item.get("alt_text", "")
        source_url = item.get("source_url", "")

        filename = source_url.split("/")[-1]

        category = guess_category(
            f"{title} {alt} {filename}"
        )

        possible_article = guess_article(category)

        image_rows.append(
            {
                "filename": filename,
                "title": title,
                "alt": alt,
                "category": category,
                "possible_article": possible_article,
                "upload_date": item.get("date", ""),
                "image_url": source_url,
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
            ],
        )
        writer.writeheader()
        writer.writerows(run_log_rows)

    summary = {
        "media_count": len(image_rows),
        "group_count": len(group_rows),
        "methods_used": ["GET"],
        "status": "success",
    }

    summary_json = output_dir / "media-organizer-summary.json"

    summary_json.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(
        f"Exported {len(image_rows)} images successfully."
    )


if __name__ == "__main__":
    main()
