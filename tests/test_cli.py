import logging
from unittest.mock import patch

import pytest

import crawl_blog
from weibo_blog.crawler import CookieExpiredError


def test_cli_reports_renew_command_only_for_cookie_expiry(caplog):
    with patch.object(
        crawl_blog,
        "main",
        side_effect=CookieExpiredError("expired"),
    ), caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as exc:
            crawl_blog.cli()

    assert exc.value.code == 2
    assert "uv run crawl_blog.py --renew-cookie" in caplog.text
