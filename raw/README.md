# 原始备份说明

本目录保留原始 WeiBack HTML 文件，便于复核解析结果。

推荐普通读者直接打开仓库根目录的 `index.html`，它会读取 `media/` 下的图片，显示为按月排列的图文网页。

如果 `index.html` 看不到图片，说明 `media/` 目录没有随仓库一起下载。请重新运行：

```bash
python scripts/build_archive.py --source <原始目录> --output <输出目录> --copy-media
```
