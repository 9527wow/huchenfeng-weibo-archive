from __future__ import annotations

import argparse
import csv
import html
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup, NavigableString, Tag


DATE_RE = re.compile(r"(?P<dt>\d{4}/\d{1,2}/\d{1,2}\s+\d{2}:\d{2}:\d{2})(?:\s+发布于\s+(?P<loc>.+))?")
HASHTAG_RE = re.compile(r"#([^#\n]{1,80})#")


def normalize_space(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def node_text(node: Tag | None) -> str:
    if node is None:
        return ""

    parts: list[str] = []

    def walk(child):
        if isinstance(child, NavigableString):
            parts.append(str(child))
            return
        if not isinstance(child, Tag):
            return
        if child.name == "br":
            parts.append("\n")
            return
        if child.name == "img":
            alt = child.get("alt") or child.get("title")
            if alt:
                parts.append(alt)
            return
        for inner in child.children:
            walk(inner)

    for item in node.children:
        walk(item)
    return normalize_space("".join(parts).replace("\u200b", ""))


def parse_detail(text: str) -> tuple[str, str, str]:
    detail = normalize_space(text)
    match = DATE_RE.search(detail)
    if not match:
        return "", "", detail
    dt = datetime.strptime(match.group("dt"), "%Y/%m/%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M:%S"), (match.group("loc") or "").strip(), detail


def clean_src(src: str) -> str:
    src = src.strip()
    if src.startswith("./"):
        src = src[2:]
    return unquote(src).replace("\\", "/")


def is_remote_src(src: str) -> bool:
    return src.startswith("http://") or src.startswith("https://")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_html_file(path: Path, source_root: Path, start_index: int) -> list[dict]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    posts: list[dict] = []
    for offset, wrapper in enumerate(soup.select("div.bk-post-wrapper"), start=1):
        post = wrapper.select_one("div.bk-post")
        if post is None:
            continue

        poster = post.select_one("div.bk-poster")
        author_node = poster.select_one("a.bk-poster-name") if poster else None
        detail_node = poster.select_one(".bk-create-detail") if poster else None
        created_at, location, detail_text = parse_detail(node_text(detail_node))

        text_node = post.select_one("div.bk-post-text")
        text = node_text(text_node).replace(" ​​​", "").strip()

        original_node = None
        for a in post.select("a.bk-link"):
            if node_text(a) == "[原贴链接]":
                original_node = a
                break
        original_url = original_node.get("href", "").strip() if original_node else ""
        original_id = original_url.rstrip("/").split("/")[-1] if original_url else ""

        links = []
        for a in post.select("a[href]"):
            label = node_text(a)
            href = a.get("href", "").strip()
            if not href or label == "[原贴链接]":
                continue
            links.append({"text": label, "url": href})

        media = []
        for img in post.select("div.bk-post-media img.bk-pic"):
            rel = clean_src(img.get("src", ""))
            remote = is_remote_src(rel)
            full = None if remote else (source_root / rel).resolve()
            media.append(
                {
                    "src": rel,
                    "repo_path": rel if remote else f"media/{rel}",
                    "filename": Path(rel).name,
                    "is_remote": remote,
                    "exists": False if remote else full.exists(),
                    "size": None if remote or not full.exists() else full.stat().st_size,
                    "sha256": None if remote or not full.exists() else file_sha256(full),
                }
            )

        retweets = []
        for rt in post.select("div.bk-retweet"):
            rt_author = rt.select_one(".bk-retweeter-name")
            rt_text = rt.select_one(".bk-post-text")
            retweets.append(
                {
                    "author": node_text(rt_author),
                    "author_url": rt_author.get("href", "").strip() if rt_author else "",
                    "text": node_text(rt_text),
                }
            )

        post_id = original_id or f"{path.stem}-{offset:04d}"
        posts.append(
            {
                "id": post_id,
                "sequence": start_index + offset - 1,
                "source_file": path.name,
                "source_index": offset,
                "author": node_text(author_node),
                "author_url": author_node.get("href", "").strip() if author_node else "",
                "created_at": created_at,
                "location": location,
                "created_detail": detail_text,
                "text": text,
                "original_url": original_url,
                "hashtags": sorted(set(HASHTAG_RE.findall(text))),
                "mentions": sorted({node_text(a).lstrip("@") for a in post.select("a.bk-user") if node_text(a).startswith("@")}),
                "links": links,
                "media": media,
                "media_count": len(media),
                "has_video_link": any("video.weibo.com" in item["url"] for item in links),
                "retweets": retweets,
                "retweet_count": len(retweets),
            }
        )
    return posts


def markdown_escape(text: str) -> str:
    return text.replace("|", "\\|")


def make_post_md(post: dict, include_media_preview: bool = False) -> str:
    lines = [
        f"### {post['created_at'] or '未知时间'} · {post['location'] or '未知地点'}",
        "",
        f"- 原帖：{post['original_url'] or '无'}",
        f"- 作者：{post['author']} ({post['author_url']})",
        f"- 图片数：{post['media_count']}；视频链接：{'有' if post['has_video_link'] else '无'}；转发内容：{post['retweet_count']} 条",
    ]
    if post["hashtags"]:
        lines.append(f"- 话题：{', '.join('#' + tag + '#' for tag in post['hashtags'])}")
    if post["mentions"]:
        lines.append(f"- 提及：{', '.join('@' + item for item in post['mentions'])}")
    lines.extend(["", post["text"] or "_无正文_", ""])
    if post["links"]:
        lines.append("相关链接：")
        for item in post["links"]:
            lines.append(f"- [{item['text'] or item['url']}]({item['url']})")
        lines.append("")
    if post["media"]:
        lines.append("媒体文件：")
        for idx, item in enumerate(post["media"], start=1):
            lines.append(f"- 图{idx}: `{item['repo_path']}`")
            if include_media_preview:
                lines.append(f"  ![图{idx}](../{item['repo_path']})")
        lines.append("")
    if post["retweets"]:
        lines.append("转发内容：")
        for item in post["retweets"]:
            lines.append(f"- {item['author']}: {item['text']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def html_text(text: str) -> str:
    return html.escape(text or "", quote=True)


def render_post_html(post: dict) -> str:
    text = "<br>".join(html_text(post["text"]).splitlines()) or "<em>无正文</em>"
    links = []
    if post["original_url"]:
        links.append(f'<a href="{html_text(post["original_url"])}" target="_blank" rel="noreferrer">原帖</a>')
    for item in post["links"]:
        label = item["text"] or item["url"]
        links.append(f'<a href="{html_text(item["url"])}" target="_blank" rel="noreferrer">{html_text(label)}</a>')
    media = "\n".join(
        f'<a class="image-link" href="{html_text(item["repo_path"])}" target="_blank"><img loading="lazy" src="{html_text(item["repo_path"])}" alt="微博图片"></a>'
        for item in post["media"]
    )
    tags = " ".join(f"<span>#{html_text(tag)}#</span>" for tag in post["hashtags"])
    mentions = " ".join(f"<span>@{html_text(name)}</span>" for name in post["mentions"])
    return f"""
<article class="post" data-text="{html_text(post['text'])}" data-month="{html_text(post['created_at'][:7])}">
  <header>
    <div>
      <time>{html_text(post['created_at'] or '未知时间')}</time>
      <span class="location">{html_text(post['location'] or '未知地点')}</span>
    </div>
    <div class="source">{html_text(post['source_file'])} #{post['source_index']}</div>
  </header>
  <p class="body">{text}</p>
  <div class="badges">{tags} {mentions}</div>
  <div class="links">{''.join(links)}</div>
  <div class="media">{media}</div>
</article>
""".strip()


def make_index_html(posts: list[dict], by_month: dict[str, list[dict]]) -> str:
    total_media = sum(p["media_count"] for p in posts)
    months = sorted(by_month, reverse=True)
    nav = "\n".join(
        f'<a href="#m-{month}">{month}<span>{len(by_month[month])}</span></a>'
        for month in months
    )
    sections = []
    for month in months:
        group = by_month[month]
        section_posts = "\n".join(render_post_html(post) for post in group)
        sections.append(
            f"""
<section class="month" id="m-{month}">
  <h2>{month}<span>{len(group)} 条 / {sum(p['media_count'] for p in group)} 图</span></h2>
  {section_posts}
</section>
""".strip()
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="悠悠的户：户晨风微博历史内容归档，收录户晨风微博备份、户晨风2023微博图文时间线、原始 HTML 与结构化数据。">
  <meta name="keywords" content="悠悠的户,户晨风,户晨风微博,户晨风2023,户晨风微博备份,户晨风微博历史内容,户晨风微博归档,户晨风图文时间线">
  <title>悠悠的户：户晨风微博历史内容归档</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f5f2;
      --panel: #ffffff;
      --text: #222;
      --muted: #6a6a63;
      --line: #dedbd2;
      --accent: #9b2f22;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", "Microsoft YaHei", sans-serif;
      line-height: 1.65;
    }}
    .layout {{
      display: grid;
      grid-template-columns: 260px minmax(0, 1fr);
      min-height: 100vh;
    }}
    aside {{
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
      padding: 24px 18px;
      border-right: 1px solid var(--line);
      background: #ece9df;
    }}
    main {{
      max-width: 980px;
      width: 100%;
      padding: 32px 28px 80px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
      line-height: 1.2;
    }}
    .summary {{
      margin: 0 0 18px;
      color: var(--muted);
      font-size: 14px;
    }}
    .search {{
      width: 100%;
      height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 12px;
      background: #fff;
      font-size: 14px;
    }}
    nav {{
      display: grid;
      gap: 6px;
      margin-top: 18px;
    }}
    nav a {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: var(--text);
      text-decoration: none;
      padding: 6px 8px;
      border-radius: 6px;
    }}
    nav a:hover {{ background: rgba(155, 47, 34, .08); }}
    h2 {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 16px;
      margin: 28px 0 14px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--line);
      font-size: 22px;
    }}
    h2 span {{
      color: var(--muted);
      font-size: 13px;
      font-weight: 400;
    }}
    .post {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      margin: 14px 0;
      box-shadow: 0 1px 0 rgba(0, 0, 0, .03);
    }}
    .post header {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
      font-size: 13px;
    }}
    time {{
      color: var(--accent);
      font-weight: 700;
    }}
    .location {{ margin-left: 8px; }}
    .source {{ white-space: nowrap; }}
    .body {{
      margin: 14px 0;
      white-space: normal;
      font-size: 16px;
    }}
    .badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 8px 0;
      color: var(--muted);
      font-size: 13px;
    }}
    .links {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 10px 0;
      font-size: 14px;
    }}
    .links a {{
      color: var(--accent);
      text-decoration: none;
      border-bottom: 1px solid rgba(155, 47, 34, .35);
    }}
    .media {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 10px;
      margin-top: 12px;
    }}
    .image-link {{
      display: block;
      background: #f0eee8;
      border: 1px solid var(--line);
      border-radius: 6px;
      overflow: hidden;
    }}
    .image-link img {{
      display: block;
      width: 100%;
      height: 220px;
      object-fit: cover;
    }}
    .empty {{
      display: none;
      padding: 28px;
      color: var(--muted);
      text-align: center;
    }}
    @media (max-width: 820px) {{
      .layout {{ display: block; }}
      aside {{
        position: static;
        height: auto;
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }}
      main {{ padding: 22px 14px 60px; }}
      .post header {{ display: block; }}
      .source {{ margin-top: 4px; white-space: normal; }}
      .image-link img {{ height: 180px; }}
    }}
  </style>
</head>
<body>
  <div class="layout">
    <aside>
      <h1>悠悠的户</h1>
      <p class="summary">户晨风微博历史内容归档 · 户晨风微博备份 · 户晨风图文时间线</p>
      <p class="summary">{len(posts)} 条微博 · {total_media} 张图片 · {posts[-1]['created_at']} 至 {posts[0]['created_at']}</p>
      <input class="search" id="search" type="search" placeholder="搜索正文、话题、地点">
      <nav>{nav}</nav>
    </aside>
    <main>
      <div class="empty" id="empty">没有匹配结果</div>
      {' '.join(sections)}
    </main>
  </div>
  <script>
    const input = document.getElementById('search');
    const empty = document.getElementById('empty');
    const posts = Array.from(document.querySelectorAll('.post'));
    const months = Array.from(document.querySelectorAll('.month'));
    input.addEventListener('input', () => {{
      const q = input.value.trim().toLowerCase();
      let visible = 0;
      posts.forEach(post => {{
        const haystack = post.innerText.toLowerCase();
        const ok = !q || haystack.includes(q);
        post.style.display = ok ? '' : 'none';
        if (ok) visible += 1;
      }});
      months.forEach(month => {{
        month.style.display = month.querySelector('.post:not([style*="display: none"])') ? '' : 'none';
      }});
      empty.style.display = visible ? 'none' : 'block';
    }});
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--copy-media", action="store_true", help="Copy all media files into output/media. This is about 1.1GB for the current archive.")
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    if not source.exists():
        raise SystemExit(f"source not found: {source}")

    output.mkdir(parents=True, exist_ok=True)
    for name in ["data", "docs", "raw", "scripts"]:
        (output / name).mkdir(parents=True, exist_ok=True)

    posts: list[dict] = []
    for html in sorted(source.glob("*.html")):
        posts.extend(parse_html_file(html, source, len(posts) + 1))

    seen: set[str] = set()
    deduped: list[dict] = []
    duplicate_ids: list[str] = []
    for post in posts:
        key = post["original_url"] or f"{post['source_file']}#{post['source_index']}"
        if key in seen:
            duplicate_ids.append(post["id"])
            continue
        seen.add(key)
        deduped.append(post)
    posts = sorted(deduped, key=lambda p: p["created_at"] or "", reverse=True)

    media_items = []
    for post in posts:
        for item in post["media"]:
            record = dict(item)
            record["post_id"] = post["id"]
            record["created_at"] = post["created_at"]
            record["original_url"] = post["original_url"]
            media_items.append(record)

    write_text(output / "data" / "posts.json", json.dumps(posts, ensure_ascii=False, indent=2))
    write_text(output / "data" / "posts.jsonl", "\n".join(json.dumps(p, ensure_ascii=False) for p in posts) + "\n")

    with (output / "data" / "posts.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "created_at",
                "location",
                "author",
                "text",
                "original_url",
                "media_count",
                "has_video_link",
                "hashtags",
                "mentions",
                "source_file",
                "source_index",
            ],
        )
        writer.writeheader()
        for p in posts:
            writer.writerow(
                {
                    "id": p["id"],
                    "created_at": p["created_at"],
                    "location": p["location"],
                    "author": p["author"],
                    "text": p["text"],
                    "original_url": p["original_url"],
                    "media_count": p["media_count"],
                    "has_video_link": p["has_video_link"],
                    "hashtags": ";".join(p["hashtags"]),
                    "mentions": ";".join(p["mentions"]),
                    "source_file": p["source_file"],
                    "source_index": p["source_index"],
                }
            )

    with (output / "data" / "media-manifest.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["post_id", "created_at", "original_url", "src", "repo_path", "filename", "is_remote", "exists", "size", "sha256"],
        )
        writer.writeheader()
        writer.writerows(media_items)

    by_month: dict[str, list[dict]] = defaultdict(list)
    for p in posts:
        month = p["created_at"][:7] if p["created_at"] else "unknown"
        by_month[month].append(p)

    index_lines = [
        "# 时间线索引",
        "",
        "| 月份 | 微博数 | 图片数 | 视频链接数 | 文件 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for month in sorted(by_month, reverse=True):
        group = by_month[month]
        media_count = sum(p["media_count"] for p in group)
        video_count = sum(1 for p in group if p["has_video_link"])
        month_file = f"{month}.md"
        write_text(
            output / "docs" / "timeline" / month_file,
            f"# {month} 微博归档\n\n"
            + f"共 {len(group)} 条，图片 {media_count} 张，含视频链接 {video_count} 条。\n\n"
            + "\n---\n\n".join(make_post_md(p) for p in group),
        )
        index_lines.append(f"| {month} | {len(group)} | {media_count} | {video_count} | [查看](timeline/{month_file}) |")
    write_text(output / "docs" / "TIMELINE.md", "\n".join(index_lines) + "\n")
    write_text(output / "index.html", make_index_html(posts, by_month))

    topic_counter = Counter(tag for p in posts for tag in p["hashtags"])
    mention_counter = Counter(m for p in posts for m in p["mentions"])
    location_counter = Counter(p["location"] or "未知" for p in posts)
    year_counter = Counter((p["created_at"][:4] if p["created_at"] else "未知") for p in posts)
    month_counter = Counter((p["created_at"][:7] if p["created_at"] else "未知") for p in posts)

    stats_md = [
        "# 数据概览",
        "",
        f"- 微博条目：{len(posts)}",
        f"- 图片引用：{sum(p['media_count'] for p in posts)}",
        f"- 视频链接条目：{sum(1 for p in posts if p['has_video_link'])}",
        f"- 转发内容条目：{sum(1 for p in posts if p['retweet_count'])}",
        f"- 原始 HTML：{len(list(source.glob('*.html')))} 个",
        f"- 去重跳过：{len(duplicate_ids)} 条",
        "",
        "## 按年份",
        "",
        "| 年份 | 条目数 |",
        "| --- | ---: |",
    ]
    for year, count in sorted(year_counter.items(), reverse=True):
        stats_md.append(f"| {year} | {count} |")
    stats_md.extend(["", "## 按月份 Top 20", "", "| 月份 | 条目数 |", "| --- | ---: |"])
    for month, count in month_counter.most_common(20):
        stats_md.append(f"| {month} | {count} |")
    stats_md.extend(["", "## 发布地点 Top 30", "", "| 地点 | 条目数 |", "| --- | ---: |"])
    for loc, count in location_counter.most_common(30):
        stats_md.append(f"| {markdown_escape(loc)} | {count} |")
    stats_md.extend(["", "## 话题 Top 50", "", "| 话题 | 次数 |", "| --- | ---: |"])
    for tag, count in topic_counter.most_common(50):
        stats_md.append(f"| #{markdown_escape(tag)}# | {count} |")
    stats_md.extend(["", "## 提及账号 Top 50", "", "| 账号 | 次数 |", "| --- | ---: |"])
    for name, count in mention_counter.most_common(50):
        stats_md.append(f"| @{markdown_escape(name)} | {count} |")
    write_text(output / "docs" / "STATS.md", "\n".join(stats_md) + "\n")

    raw_readme = [
        "# 原始备份说明",
        "",
        "本目录保留原始 WeiBack HTML 文件，便于复核解析结果。",
        "",
        "推荐普通读者直接打开仓库根目录的 `index.html`，它会读取 `media/` 下的图片，显示为按月排列的图文网页。",
        "",
        "如果 `index.html` 看不到图片，说明 `media/` 目录没有随仓库一起下载。请重新运行：",
        "",
        "```bash",
        "python scripts/build_archive.py --source <原始目录> --output <输出目录> --copy-media",
        "```",
    ]
    write_text(output / "raw" / "README.md", "\n".join(raw_readme) + "\n")
    for html in source.glob("*.html"):
        shutil.copy2(html, output / "raw" / html.name)

    script_src = Path(__file__).resolve()
    script_dst = output / "scripts" / "build_archive.py"
    if script_src != script_dst.resolve():
        shutil.copy2(script_src, script_dst)

    gitignore = [
        "__pycache__/",
        "*.pyc",
    ]
    write_text(output / ".gitignore", "\n".join(gitignore) + "\n")

    gitattributes = [
        "*.md text eol=lf",
        "*.json text eol=lf",
        "*.jsonl text eol=lf",
        "*.csv text eol=lf",
        "*.html text eol=lf",
    ]
    write_text(output / ".gitattributes", "\n".join(gitattributes) + "\n")

    notice = [
        "# 版权与使用说明",
        "",
        "本项目是对公开微博备份材料的整理索引，主要用于资料保存、研究与事实核验。",
        "",
        "- 微博正文、图片、视频链接等原始内容的权利归原作者、平台及相关权利人所有。",
        "- 本仓库整理脚本、字段结构与索引文档可按 MIT License 使用。",
        "- 如权利人认为本归档侵犯权益，请通过 Issue 提供具体链接、权利说明和处理要求。",
        "- 发布前建议人工复核隐私、肖像、未成年人、身份证件、联系方式等敏感信息。",
    ]
    write_text(output / "NOTICE.md", "\n".join(notice) + "\n")

    license_text = [
        "MIT License",
        "",
        "Copyright (c) 2026 Contributors",
        "",
        "Permission is hereby granted, free of charge, to any person obtaining a copy",
        "of this software and associated documentation files (the \"Software\"), to deal",
        "in the Software without restriction, including without limitation the rights",
        "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell",
        "copies of the Software, and to permit persons to whom the Software is",
        "furnished to do so, subject to the following conditions:",
        "",
        "The above copyright notice and this permission notice shall be included in all",
        "copies or substantial portions of the Software.",
        "",
        "THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR",
        "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,",
        "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE",
        "AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER",
        "LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,",
        "OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE",
        "SOFTWARE.",
        "",
        "Note: this license applies to repository scripts and original documentation only;",
        "archived third-party Weibo content is not relicensed.",
    ]
    write_text(output / "LICENSE", "\n".join(license_text) + "\n")

    readme = [
        "# 悠悠的户：户晨风微博历史内容归档",
        "",
        "中文名：**悠悠的户：户晨风微博历史内容归档**。",
        "",
        "这是从本地 WeiBack 备份中整理出的户晨风微博历史内容索引，也可称为“户晨风微博备份”“户晨风微博图文归档”“户晨风2023微博时间线”。项目保留原始 HTML，并额外生成机器可读数据、按月时间线和统计文档，方便检索、复核与二次整理。",
        "",
        "关键词：户晨风、户晨风微博、户晨风2023、户晨风微博备份、户晨风微博历史内容、户晨风微博归档、户晨风图文时间线、悠悠的户。",
        "",
        "## 当前数据",
        "",
        f"- 微博条目：{len(posts)}",
        f"- 时间范围：{posts[-1]['created_at'] if posts else '未知'} 至 {posts[0]['created_at'] if posts else '未知'}",
        f"- 图片引用：{sum(p['media_count'] for p in posts)}",
        f"- 视频链接条目：{sum(1 for p in posts if p['has_video_link'])}",
        f"- 原始 HTML：{len(list(source.glob('*.html')))} 个",
        "",
        "## 目录结构",
        "",
        "```text",
        "data/",
        "  posts.json          # 完整结构化数据",
        "  posts.jsonl         # 一行一条，适合程序处理",
        "  posts.csv           # 表格版正文索引",
        "  media-manifest.csv  # 图片文件清单、大小、SHA-256",
        "media/",
        "  WeiBack-*_files/    # 图片目录；打开 index.html 看图必须保留",
        "docs/",
        "  TIMELINE.md         # 月份索引",
        "  timeline/*.md       # 按月展开的微博正文",
        "  STATS.md            # 统计概览",
        "raw/",
        "  *.html              # 原始 WeiBack HTML",
        "scripts/",
        "  build_archive.py    # 从原始备份重新生成本仓库",
        "```",
        "",
        "## 使用方式",
        "",
        "### 普通浏览",
        "",
        "下载或克隆完整仓库后，直接双击根目录的 `index.html`。它是一个离线静态网页，左侧按月份导航，右侧显示微博正文和图片，也可以在搜索框里搜正文、话题和地点。",
        "",
        "要看到图片，必须保留仓库里的 `media/` 目录，并让它和 `index.html` 在同一级目录。如果只有 `index.html`、`data/`、`docs/`，没有 `media/`，网页仍能显示文字，但图片会加载失败。",
        "",
        "### 在 GitHub 上浏览",
        "",
        "GitHub 网页会优先展示本 README。想看完整图文网页，可以：",
        "",
        "1. 打开仓库的 GitHub Pages 页面（如果仓库开启了 Pages）。",
        "2. 或点击 GitHub 的 `Code` / `Download ZIP` 下载完整仓库，解压后打开 `index.html`。",
        "",
        "如果你准备把图片一起开源，建议用 Git LFS 管理 `media/` 目录，或者把完整媒体包放到 Release 附件里。否则普通 Git 仓库会接近 1.1GB，克隆会比较慢。",
        "",
        "### 数据分析",
        "",
        "如果只想检索或做分析，可以从 [docs/TIMELINE.md](docs/TIMELINE.md) 按月份读 Markdown，或使用 `data/posts.jsonl` / `data/posts.csv`。",
        "",
        "重新生成：",
        "",
        "```bash",
        "python scripts/build_archive.py --source <WeiBack原始目录> --output <输出目录>",
        "```",
        "",
        "如果确实要把图片复制进整理目录：",
        "",
        "```bash",
        "python scripts/build_archive.py --source <WeiBack原始目录> --output <输出目录> --copy-media",
        "```",
        "",
        "注意：如果不加 `--copy-media`，会生成文字、索引和媒体清单，但 `index.html` 无法显示图片。",
        "",
        "## 字段说明",
        "",
        "- `id`：原帖 URL 最后一段，缺失时使用本地生成 ID。",
        "- `created_at` / `location`：从 WeiBack 页面中的发布时间与发布地提取。",
        "- `text`：正文纯文本，保留换行，表情用其 alt/title 文本代替。",
        "- `links`：正文内话题、视频、地点卡片等链接。",
        "- `media`：正文图片清单，含本地路径、文件大小、SHA-256。",
        "- `retweets`：页面中可解析到的转发内容。",
        "",
        "## 发布前检查",
        "",
        "- 人工抽样核对 `docs/timeline/` 与 `raw/` 是否一致。",
        "- 检查图片中是否含身份证件、联系方式、住址、未成年人或其他敏感信息。",
        "- 明确仓库只对整理脚本与索引文档授权；第三方微博内容不重新授权。",
        "- 如需收录图片，先确认 GitHub/LFS 容量与版权风险。",
        "",
        "## 权利说明",
        "",
        "见 [NOTICE.md](NOTICE.md)。",
    ]
    write_text(output / "README.md", "\n".join(readme) + "\n")

    if args.copy_media:
        for item in media_items:
            if item.get("is_remote"):
                continue
            src = source / item["src"]
            dst = output / item["repo_path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.exists() and not dst.exists():
                shutil.copy2(src, dst)

    summary = {
        "output": str(output),
        "posts": len(posts),
        "media_refs": len(media_items),
        "html_files": len(list(source.glob("*.html"))),
        "duplicates_skipped": len(duplicate_ids),
        "copy_media": args.copy_media,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
