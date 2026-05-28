# 户晨风微博历史内容归档

这是从本地 WeiBack 备份中整理出的户晨风微博历史内容索引。项目保留原始 HTML，并额外生成机器可读数据、按月时间线和统计文档，方便检索、复核与二次整理。

## 当前数据

- 微博条目：712
- 时间范围：2023-04-08 11:58:30 至 2025-07-23 17:28:03
- 图片引用：1344
- 视频链接条目：84
- 原始 HTML：4 个

## 目录结构

```text
data/
  posts.json          # 完整结构化数据
  posts.jsonl         # 一行一条，适合程序处理
  posts.csv           # 表格版正文索引
  media-manifest.csv  # 图片文件清单、大小、SHA-256
media/
  WeiBack-*_files/    # 图片目录；打开 index.html 看图必须保留
docs/
  TIMELINE.md         # 月份索引
  timeline/*.md       # 按月展开的微博正文
  STATS.md            # 统计概览
raw/
  *.html              # 原始 WeiBack HTML
scripts/
  build_archive.py    # 从原始备份重新生成本仓库
```

## 使用方式

### 在线浏览

可以直接打开 GitHub Pages：

https://9527wow.github.io/huchenfeng-weibo-archive/

这是和本地 `index.html` 相同的图文浏览页，按月份展示正文和图片。

### 普通浏览

下载或克隆完整仓库后，直接双击根目录的 `index.html`。它是一个离线静态网页，左侧按月份导航，右侧显示微博正文和图片，也可以在搜索框里搜正文、话题和地点。

要看到图片，必须保留仓库里的 `media/` 目录，并让它和 `index.html` 在同一级目录。如果只有 `index.html`、`data/`、`docs/`，没有 `media/`，网页仍能显示文字，但图片会加载失败。

### 在 GitHub 上浏览

GitHub 仓库页会优先展示本 README。想看完整图文网页，可以：

1. 打开上面的 GitHub Pages 页面。
2. 或点击 GitHub 的 `Code` / `Download ZIP` 下载完整仓库，解压后打开 `index.html`。

如果你准备把图片一起开源，建议用 Git LFS 管理 `media/` 目录，或者把完整媒体包放到 Release 附件里。否则普通 Git 仓库会接近 1.1GB，克隆会比较慢。

### 数据分析

如果只想检索或做分析，可以从 [docs/TIMELINE.md](docs/TIMELINE.md) 按月份读 Markdown，或使用 `data/posts.jsonl` / `data/posts.csv`。

重新生成：

```bash
python scripts/build_archive.py --source <WeiBack原始目录> --output <输出目录>
```

如果确实要把图片复制进整理目录：

```bash
python scripts/build_archive.py --source <WeiBack原始目录> --output <输出目录> --copy-media
```

注意：如果不加 `--copy-media`，会生成文字、索引和媒体清单，但 `index.html` 无法显示图片。

## 字段说明

- `id`：原帖 URL 最后一段，缺失时使用本地生成 ID。
- `created_at` / `location`：从 WeiBack 页面中的发布时间与发布地提取。
- `text`：正文纯文本，保留换行，表情用其 alt/title 文本代替。
- `links`：正文内话题、视频、地点卡片等链接。
- `media`：正文图片清单，含本地路径、文件大小、SHA-256。
- `retweets`：页面中可解析到的转发内容。

## 发布前检查

- 人工抽样核对 `docs/timeline/` 与 `raw/` 是否一致。
- 检查图片中是否含身份证件、联系方式、住址、未成年人或其他敏感信息。
- 明确仓库只对整理脚本与索引文档授权；第三方微博内容不重新授权。
- 如需收录图片，先确认 GitHub/LFS 容量与版权风险。

## 权利说明

见 [NOTICE.md](NOTICE.md)。
