"""解析 — mymblog 单条 JSON → 扁平 dict"""
from __future__ import annotations

import json
import re
from datetime import datetime


def _parse_created_at(s: str) -> int:
    """'Wed May 14 21:15:26 +0800 2025' → 毫秒时间戳"""
    dt = datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
    return int(dt.timestamp() * 1000)


def _clean_source(s: str) -> str:
    """去掉 <a> 标签，取纯文本来源"""
    if not s:
        return ""
    text = re.sub(r"<[^>]+>", "", s).strip()
    return text


def parse_post(raw: dict) -> dict:
    """把 mymblog 单条微博映射为扁平 dict"""
    user = raw.get("user", {}) or {}
    created_at = _parse_created_at(raw["created_at"])

    # 图片精简
    pics = []
    pic_infos = raw.get("pic_infos") or {}
    for pid, info in pic_infos.items():
        large = info.get("large", {}) or {}
        bmiddle = info.get("bmiddle", {}) or {}
        pics.append({
            "pid": pid,
            "url_large": large.get("url", ""),
            "url_bmiddle": bmiddle.get("url", ""),
            "w": large.get("width", 0),
            "h": large.get("height", 0),
        })

    # 视频直链
    page_info = raw.get("page_info") or {}
    media_info = page_info.get("media_info") or {}
    video_url = media_info.get("stream_url", "")

    # 转发原微博精简
    retweeted_json = ""
    rt = raw.get("retweeted_status")
    if rt:
        rt_user = rt.get("user", {}) or {}
        retweeted_json = json.dumps({
            "post_id": rt.get("id", 0),
            "mblogid": rt.get("mblogid", ""),
            "text_raw": rt.get("text_raw", ""),
            "uid": rt_user.get("id", 0),
            "screen_name": rt_user.get("screen_name", ""),
            "created_at": rt.get("created_at", ""),
        }, ensure_ascii=False)

    return {
        "mblogid": raw.get("mblogid", ""),
        "post_id": raw.get("id", 0),
        "uid": user.get("id", 0),
        "text": raw.get("text", ""),
        "text_raw": raw.get("text_raw", ""),
        "long_text": "",
        "is_long_text": 1 if raw.get("isLongText") else 0,
        "source": _clean_source(raw.get("source", "")),
        "region": raw.get("region_name", ""),
        "pics_json": json.dumps(pics, ensure_ascii=False),
        "video_url": video_url,
        "retweeted_json": retweeted_json,
        "reposts_count": raw.get("reposts_count", 0),
        "comments_count": raw.get("comments_count", 0),
        "attitudes_count": raw.get("attitudes_count", 0),
        "created_at": created_at,
        "raw_json": json.dumps(raw, ensure_ascii=False),
    }


def parse_blogger(user: dict) -> dict:
    """从 mymblog 的 user 字段提取博主信息"""
    return {
        "uid": user.get("id", 0),
        "screen_name": user.get("screen_name", ""),
        "avatar": user.get("avatar_large", "") or user.get("profile_image_url", ""),
        "profile_url": user.get("profile_url", ""),
        "verified": 1 if user.get("verified") else 0,
        "raw_json": json.dumps(user, ensure_ascii=False),
    }
