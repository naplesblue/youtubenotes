#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
from urllib.parse import urlparse, parse_qs


def normalize_youtube_url(url: str) -> str:
    """
    尝试把常见的 YouTube 链接规范化。
    这里只做最基础处理，yt-dlp 本身也能处理大多数格式。
    """
    url = url.strip()

    # 允许用户直接传视频 ID
    if "://" not in url and len(url) >= 11:
        return f"https://www.youtube.com/watch?v={url}"

    parsed = urlparse(url)

    # 处理 youtu.be 短链
    if parsed.netloc in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.lstrip("/")
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"

    # 其他情况原样返回
    return url


def get_channel_info_with_ytdlp(video_url: str) -> tuple[str, str]:
    """
    使用 yt-dlp 获取频道名称和频道 ID。
    返回: (channel_name, channel_id)
    """
    cmd = [
        "yt-dlp",
        "--print",
        "%(channel)s\t%(channel_id)s",
        "--no-warnings",
        "--skip-download",
        video_url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "未找到 yt-dlp。请先安装：\n"
            "  pip install -U yt-dlp\n"
            "或\n"
            "  brew install yt-dlp"
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise RuntimeError(f"yt-dlp 执行失败：{stderr or '未知错误'}")

    output = result.stdout.strip()
    if not output:
        raise RuntimeError("未获取到频道信息，可能链接无效或视频不可访问。")

    parts = output.split("\t", 1)
    if len(parts) != 2:
        raise RuntimeError(f"输出格式异常：{output}")

    channel_name, channel_id = parts[0].strip(), parts[1].strip()

    if not channel_id.startswith("UC"):
        raise RuntimeError(f"未解析到有效的 channel_id：{channel_id}")

    return channel_name, channel_id


def build_rss_url(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def main():
    if len(sys.argv) < 2:
        print("用法：")
        print(f"  {sys.argv[0]} 'https://www.youtube.com/watch?v=xxxxxxxxxxx'")
        print(f"  {sys.argv[0]} 'https://youtu.be/xxxxxxxxxxx'")
        print(f"  {sys.argv[0]} '视频ID'")
        sys.exit(1)

    raw_url = sys.argv[1]
    video_url = normalize_youtube_url(raw_url)

    try:
        channel_name, channel_id = get_channel_info_with_ytdlp(video_url)
        rss_url = build_rss_url(channel_id)

        print(f"频道名称: {channel_name}")
        print(f"频道 ID:   {channel_id}")
        print(f"RSS 地址:  {rss_url}")
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()