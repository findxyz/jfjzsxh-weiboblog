import logging
import runpy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import crawl_blog
import weibo_blog.crawler as crawler_module


def test_script_entrypoint_reports_renew_for_cookie_expiry(caplog):
    class ExpiredCrawler:
        def __init__(self, db_path):
            pass

        def crawl_blog(self, uid, full=False, start_page=1):
            raise crawler_module.CookieExpiredError("expired")

    script = Path(__file__).parents[1] / "crawl_blog.py"
    with patch.object(crawler_module, "BlogCrawler", ExpiredCrawler), \
         patch.object(sys, "argv", ["crawl_blog.py", "--uid", "1"]), \
         caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(script), run_name="__main__")

    assert exc.value.code == 2
    assert "uv run crawl_blog.py --renew-cookie" in caplog.text


def test_module_keeps_main_as_the_only_entrypoint():
    assert not hasattr(crawl_blog, "cli")
