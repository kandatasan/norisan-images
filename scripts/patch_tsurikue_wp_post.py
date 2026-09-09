#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib import parse

ALLOWED_HOSTS = {"tsurikue.com", "www.tsurikue.com"}


def validate_site(base_url):
    parsed = parse.urlparse(base_url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"https", "http"} or host not in ALLOWED_HOSTS:
        raise RuntimeError(f"Refusing WordPress write: unexpected host={host or 'empty'}")


def wp_request(base_url, username, app_password, path, method="GET", payload=None):
    url = base_url.rstrip("/") + path
    cmd = [
        "curl", "--silent", "--show-error", "--location",
        "--connect-timeout", "10", "--max-time", "60",
        "--retry", "2", "--retry-delay", "2", "--retry-connrefused",
        "--user", f"{username}:{app_password}",
        "--request", method,
        "--header", "Accept: application/json",
        "--header", "User-Agent: tsurikue-post-patcher/1.0",
    ]
    if payload is not None:
        cmd += [
            "--header", "Content-Type: application/json; charset=utf-8",
            "--data-binary", json.dumps(payload, ensure_ascii=False),
        ]
    cmd += ["--write-out", "\n%{http_code}", url]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "WordPress API curl failed").strip())
    body, sep, status_text = proc.stdout.rpartition("\n")
    if not sep or not status_text.isdigit():
        raise RuntimeError("Invalid WordPress API response status")
    status = int(status_text)
    try:
        parsed_body = json.loads(body) if body else None
    except Exception:
        parsed_body = {"raw_response": body[:1000]}
    if status < 200 or status >= 300:
        raise RuntimeError(f"WordPress API HTTP {status}: {parsed_body}")
    return parsed_body


def raw_field(post, key):
    value = post.get(key, "")
    if isinstance(value, dict):
        return value.get("raw") or value.get("rendered") or ""
    return value or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--request", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    req = json.loads(Path(args.request).read_text(encoding="utf-8"))
    slug = str(req.get("slug") or "").strip()
    marker = str(req.get("marker") or "").strip()
    block_path = Path(str(req.get("block_path") or ""))
    mode = str(req.get("mode") or "append").strip()
    expected_status = str(req.get("expected_status") or "publish").strip()

    if not slug or not marker or not str(block_path):
        raise SystemExit("slug, marker and block_path are required")
    if mode != "append":
        raise SystemExit("Only mode=append is supported")
    if expected_status != "publish":
        raise SystemExit("Only expected_status=publish is supported")
    if not block_path.exists():
        raise SystemExit(f"Block file does not exist: {block_path}")

    block = block_path.read_text(encoding="utf-8").strip()
    if marker not in block:
        raise SystemExit("Marker must be present inside block content")
    if len(block) < 100:
        raise SystemExit("Block content is suspiciously short")

    wp_url = os.environ.get("WP_URL", "").strip()
    wp_user = os.environ.get("WP_USER", "").strip()
    wp_password = os.environ.get("WP_APP_PASSWORD", "").strip()
    if not all([wp_url, wp_user, wp_password]):
        raise SystemExit("WP_URL/WP_USER/WP_APP_PASSWORD are required")

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"ok": False, "slug": slug, "marker": marker, "mode": mode}

    try:
        validate_site(wp_url)
        encoded_slug = parse.quote(slug, safe="")
        fields = parse.quote("id,slug,status,link,title,content,modified", safe=",")
        posts = wp_request(
            wp_url, wp_user, wp_password,
            f"/wp-json/wp/v2/posts?context=edit&slug={encoded_slug}&status=publish&per_page=10&_fields={fields}"
        )
        if len(posts) != 1:
            raise RuntimeError(f"Expected exactly one published post for slug={slug}, found {len(posts)}")

        post = posts[0]
        post_id = int(post["id"])
        current = raw_field(post, "content")
        report.update({"post_id": post_id, "link": post.get("link"), "status_before": post.get("status")})

        if marker in current:
            report.update({"ok": True, "changed": False, "message": "Marker already present; no write performed."})
        else:
            updated = current.rstrip() + "\n\n" + block + "\n"
            wp_request(
                wp_url, wp_user, wp_password,
                f"/wp-json/wp/v2/posts/{post_id}?context=edit",
                method="POST",
                payload={"content": updated},
            )
            report.update({"ok": True, "changed": True, "message": "Block appended to current published post content."})

        verify = wp_request(
            wp_url, wp_user, wp_password,
            f"/wp-json/wp/v2/posts/{post_id}?context=edit&_fields={fields}"
        )
        verified_content = raw_field(verify, "content")
        if verify.get("status") != expected_status or verify.get("slug") != slug or marker not in verified_content:
            raise RuntimeError("Post verification failed")

        report.update({
            "ok": True,
            "verify_status": verify.get("status"),
            "verify_slug": verify.get("slug"),
            "verify_modified": verify.get("modified"),
            "verify_marker": True,
        })
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        report["error"] = str(exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
