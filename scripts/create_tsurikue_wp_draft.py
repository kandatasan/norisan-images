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
        raise RuntimeError(f"Refusing WordPress write: WP_URL host is not tsurikue.com (host={host or 'empty'})")


def wp_request(base_url, username, app_password, path, method="GET", payload=None):
    url = base_url.rstrip("/") + path
    cmd = [
        "curl", "--silent", "--show-error", "--location",
        "--connect-timeout", "10", "--max-time", "60",
        "--retry", "2", "--retry-delay", "2", "--retry-connrefused",
        "--user", f"{username}:{app_password}",
        "--request", method,
        "--header", "Accept: application/json",
        "--header", "User-Agent: tsurikue-draft-automation/1.0",
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
        raise RuntimeError("WordPress API response did not include a valid HTTP status")
    status = int(status_text)
    try:
        parsed_body = json.loads(body) if body else None
    except Exception:
        parsed_body = {"raw_response": body[:1000]}
    if status < 200 or status >= 300:
        raise RuntimeError(f"WordPress API HTTP {status}: {parsed_body}")
    return parsed_body


def extract_raw(post, field):
    value = post.get(field, {})
    if isinstance(value, dict):
        return value.get("raw") or value.get("rendered") or ""
    return value or ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    req_path = Path(args.request)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    req = json.loads(req_path.read_text(encoding="utf-8"))

    required = ["title", "slug", "content_path", "status"]
    missing = [k for k in required if not req.get(k)]
    if missing:
        raise SystemExit(f"Missing request keys: {missing}")
    if req["status"] != "draft":
        raise SystemExit("Only status=draft is allowed")

    title = str(req["title"]).strip()
    slug = str(req["slug"]).strip()
    content_path = Path(str(req["content_path"]))
    if content_path != Path("drafts") / slug / "content.html":
        raise SystemExit("content_path must be drafts/<slug>/content.html")
    if not content_path.exists():
        raise SystemExit("Draft content.html does not exist")
    content = content_path.read_text(encoding="utf-8")
    if len(content.strip()) < 1000:
        raise SystemExit("Draft content is suspiciously short")

    wp_url = os.environ.get("WP_URL", "").strip()
    wp_user = os.environ.get("WP_USER", "").strip()
    wp_password = os.environ.get("WP_APP_PASSWORD", "").strip()
    if not all([wp_url, wp_user, wp_password]):
        raise SystemExit("WP_URL/WP_USER/WP_APP_PASSWORD are required")

    report = {"ok": False, "title": title, "slug": slug, "status": "draft"}
    try:
        validate_site(wp_url)
        encoded_slug = parse.quote(slug, safe="")
        fields = parse.quote("id,slug,status,title,content,link", safe=",")

        existing = None
        for existing_status in ("draft", "pending", "future", "private", "publish"):
            status_q = parse.quote(existing_status, safe="")
            posts = wp_request(
                wp_url, wp_user, wp_password,
                f"/wp-json/wp/v2/posts?context=edit&slug={encoded_slug}&status={status_q}&per_page=10&_fields={fields}"
            )
            if posts:
                existing = posts[0]
                break

        payload = {"title": title, "content": content, "status": "draft"}
        excerpt = str(req.get("excerpt") or "").strip()
        if excerpt:
            payload["excerpt"] = excerpt

        if existing:
            post_id = int(existing["id"])
            if existing.get("status") != "draft":
                raise RuntimeError(f"Slug already exists with status={existing.get('status')} and post_id={post_id}")
            if extract_raw(existing, "title") == title and extract_raw(existing, "content") == content:
                report.update({"ok": True, "post_id": post_id, "link": existing.get("link"), "message": "Matching draft already exists; no duplicate created."})
            else:
                wp_request(wp_url, wp_user, wp_password, f"/wp-json/wp/v2/posts/{post_id}?context=edit", method="POST", payload=payload)
                report.update({"ok": True, "post_id": post_id, "message": "Existing draft updated."})
        else:
            payload["slug"] = slug
            created = wp_request(wp_url, wp_user, wp_password, "/wp-json/wp/v2/posts?context=edit", method="POST", payload=payload)
            post_id = int(created["id"])
            report.update({"ok": True, "post_id": post_id, "message": "WordPress draft created."})

        verify = wp_request(wp_url, wp_user, wp_password, f"/wp-json/wp/v2/posts/{post_id}?context=edit&_fields={fields}")
        ok = (
            verify.get("status") == "draft"
            and verify.get("slug") == slug
            and extract_raw(verify, "title") == title
            and extract_raw(verify, "content") == content
        )
        if not ok:
            raise RuntimeError("Draft verification failed")
        report.update({"ok": True, "verify_status": verify.get("status"), "verify_slug": verify.get("slug"), "link": verify.get("link")})
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
