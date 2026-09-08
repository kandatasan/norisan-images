#!/usr/bin/env python3
import argparse, json, os, re, subprocess
from pathlib import Path
from urllib.parse import quote, urlparse


def wp_request(base_url, username, app_password, path):
    url = base_url.rstrip('/') + path
    cmd = [
        'curl','--silent','--show-error','--location',
        '--connect-timeout','10','--max-time','60',
        '--retry','2','--retry-delay','2','--retry-connrefused',
        '--user',f'{username}:{app_password}',
        '--request','GET',
        '--header','Accept: application/json',
        '--header','User-Agent: norisan-images/tsurikue-post-inspector',
        '--write-out','\n%{http_code}',url,
    ]
    proc = subprocess.run(cmd,capture_output=True,text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or 'WordPress API curl failed').strip())
    body, sep, status_text = proc.stdout.rpartition('\n')
    if not sep or not status_text.isdigit():
        raise RuntimeError('Invalid WordPress API response status')
    status = int(status_text)
    try:
        parsed = json.loads(body) if body else None
    except Exception:
        parsed = {'raw_response': body[:2000]}
    if status < 200 or status >= 300:
        raise RuntimeError(f'WordPress API HTTP {status}: {parsed}')
    return parsed


def raw_field(post, key):
    v = post.get(key, '')
    if isinstance(v, dict):
        return v.get('raw') or v.get('rendered') or ''
    return v or ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--request', required=True)
    ap.add_argument('--report', required=True)
    args = ap.parse_args()

    req = json.loads(Path(args.request).read_text(encoding='utf-8'))
    query = str(req.get('query') or '').strip()
    if not query:
        raise SystemExit('request.query is required')

    wp_url = os.environ.get('WP_URL','').strip()
    wp_user = os.environ.get('WP_USER','').strip()
    wp_password = os.environ.get('WP_APP_PASSWORD','').strip()
    if not all([wp_url,wp_user,wp_password]):
        raise SystemExit('WP_URL/WP_USER/WP_APP_PASSWORD are required')

    host = (urlparse(wp_url).hostname or '').lower()
    if host not in {'tsurikue.com','www.tsurikue.com'}:
        raise SystemExit(f'Refusing non-tsurikue host: {host}')

    fields = 'id,date,modified,slug,status,link,title,excerpt,content,featured_media,categories,tags'
    search = quote(query, safe='')
    f = quote(fields, safe=',')
    candidates = []
    for status in ('draft','pending','future','private','publish'):
        data = wp_request(wp_url,wp_user,wp_password,
            f'/wp-json/wp/v2/posts?context=edit&search={search}&status={status}&per_page=20&_fields={f}')
        if data:
            candidates.extend(data)

    # Exact-ish title hits first, otherwise return search candidates.
    qnorm = re.sub(r'\s+','',query).lower()
    def score(p):
        title = re.sub(r'\s+','',raw_field(p,'title')).lower()
        return (2 if qnorm and qnorm in title else 0) + (1 if p.get('status')=='draft' else 0)
    candidates.sort(key=score, reverse=True)

    result = {'ok': True, 'query': query, 'host': host, 'candidate_count': len(candidates), 'posts': []}
    for post in candidates[:10]:
        content = raw_field(post,'content')
        img_ids = sorted({int(x) for x in re.findall(r'wp-image-(\d+)', content)})
        srcs = sorted(set(re.findall(r'<img[^>]+src=["\']([^"\']+)', content, flags=re.I)))
        image_meta = []
        ids = list(img_ids)
        if post.get('featured_media'):
            ids.append(int(post['featured_media']))
        for mid in sorted(set(ids)):
            try:
                m = wp_request(wp_url,wp_user,wp_password,
                    f'/wp-json/wp/v2/media/{mid}?context=edit&_fields=id,date,slug,link,source_url,alt_text,caption,title,media_details')
                image_meta.append(m)
            except Exception as exc:
                image_meta.append({'id': mid, 'error': str(exc)})
        # Also search media library by query, useful when images are not embedded yet.
        media_search = []
        try:
            media_search = wp_request(wp_url,wp_user,wp_password,
                f'/wp-json/wp/v2/media?context=edit&search={search}&per_page=30&_fields=id,date,slug,link,source_url,alt_text,caption,title,media_details') or []
        except Exception:
            pass
        result['posts'].append({
            'id': post.get('id'), 'status': post.get('status'), 'slug': post.get('slug'),
            'date': post.get('date'), 'modified': post.get('modified'), 'link': post.get('link'),
            'title': raw_field(post,'title'), 'excerpt': raw_field(post,'excerpt'),
            'content': content, 'featured_media': post.get('featured_media'),
            'categories': post.get('categories') or [], 'tags': post.get('tags') or [],
            'embedded_image_ids': img_ids, 'embedded_image_srcs': srcs,
            'image_meta': image_meta, 'media_search': media_search,
        })

    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'ok': True, 'query': query, 'candidate_count': len(candidates)},ensure_ascii=False))

if __name__ == '__main__':
    main()
