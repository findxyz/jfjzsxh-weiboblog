"""解析层测试"""
import json
import os
from weibo_blog.parser import parse_post, parse_blogger


def load_fixture(name):
    path = os.path.join(os.path.dirname(__file__), "fixtures", name)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_parse_post_plain():
    """纯文本微博字段映射"""
    raw = load_fixture("post_plain.json")
    p = parse_post(raw)
    assert p["mblogid"] == "PrP6QqqEQ"
    assert p["post_id"] == 5166313246299004
    assert p["uid"] == 1401527553
    assert "古埃及文字" in p["text_raw"]
    assert p["is_long_text"] == 0
    assert p["region"] == "发布于 北京"
    assert p["reposts_count"] == 5
    assert p["comments_count"] == 20
    assert p["attitudes_count"] == 393
    assert p["created_at"] == 1747228526000  # Wed May 14 21:15:26 +0800 2025 → ms
    assert p["pics_json"] == "[]"
    assert p["video_url"] == ""
    assert p["retweeted_json"] == ""


def test_parse_post_with_pics():
    """带图微博：pics_json 精简为 pid+large+bmiddle+宽高"""
    raw = load_fixture("post_with_pics.json")
    p = parse_post(raw)
    pics = json.loads(p["pics_json"])
    assert len(pics) == 1
    assert pics[0]["pid"] == "53899d01ly1i1f0qezg9nj20mj0oytjr"
    assert pics[0]["url_large"] == "https://wx3.sinaimg.cn/orj960/53899d01ly1i1f0qezg9nj20mj0oytjr.jpg"
    assert pics[0]["url_bmiddle"] == "https://wx3.sinaimg.cn/wap360/53899d01ly1i1f0qezg9nj20mj0oytjr.jpg"
    assert pics[0]["w"] == 811
    assert pics[0]["h"] == 898


def test_parse_post_with_video():
    """带视频微博：video_url 提取 stream_url"""
    raw = load_fixture("post_with_video.json")
    p = parse_post(raw)
    assert "f.video.weibocdn.com" in p["video_url"]
    assert p["video_url"].endswith(".mp4") or ".mp4?" in p["video_url"]


def test_parse_post_with_retweet():
    """转发微博：retweeted_json 精简为 id/mblogid/text_raw/uid/screen_name/created_at"""
    raw = load_fixture("post_with_retweet.json")
    p = parse_post(raw)
    assert p["retweeted_json"]
    rt = json.loads(p["retweeted_json"])
    assert rt["post_id"] == 4636881109388307
    assert rt["mblogid"] == "KftrEDokj"
    assert "失去人性" in rt["text_raw"]
    assert rt["uid"] == 1401527553
    assert rt["screen_name"] == "tombkeeper"
    assert rt["created_at"] == "Fri May 14 22:21:08 +0800 2021"


def test_parse_post_longtext_flag():
    """isLongText=True 标记为 1"""
    raw = load_fixture("post_longtext.json")
    p = parse_post(raw)
    assert p["is_long_text"] == 1
    assert p["long_text"] == ""  # long_text 由 crawler 补全，parser 只置空


def test_parse_post_source_clean():
    """source 去标签（post_with_pics 的 source='微博网页版'）"""
    raw = load_fixture("post_with_pics.json")
    p = parse_post(raw)
    assert p["source"] == "微博网页版"


def test_parse_blogger_from_user():
    """从微博 user 字段提取博主信息"""
    raw = load_fixture("post_plain.json")
    user = raw["user"]
    b = parse_blogger(user)
    assert b["uid"] == 1401527553
    assert b["screen_name"] == "tombkeeper"
    assert b["profile_url"] == "/u/1401527553"
    assert b["verified"] == 1
    assert "sinaimg.cn" in b["avatar"]
