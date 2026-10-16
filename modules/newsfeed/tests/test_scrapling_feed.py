from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from modules.newsfeed.scrapling_feed import (
    FeedSource, HTTPResponse, ScraplingNewsProvider, ScraplingTransport,
    parse_html_listing, parse_json_items, parse_xml_feed,
)


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        if not self.responses:
            raise AssertionError("unexpected network request")
        return self.responses.pop(0)


def approved_source(feed_format="rss", **kwargs):
    values = dict(
        source_id="fixture-feed", feed_url="https://feeds.example.test/rss.xml",
        provider="fixture-provider", publisher="Fixture Publisher", market="US",
        allowed_hosts=("feeds.example.test",), feed_format=feed_format,
        terms_url="https://publisher.example.test/terms", terms_reviewed=True,
        robots_allowed=True, rights_scope="private local metadata use",
        authorized_scopes=("local_personal",), enabled=True, min_poll_seconds=0,
    )
    values.update(kwargs)
    return FeedSource(**values)


class FakeSelector:
    def __init__(self, text):
        self.text = text

    def get(self):
        return self.text


class FakeCard:
    def __init__(self, data):
        self.data = data

    def css(self, selector):
        return FakeSelector(self.data.get(selector))


class FakeDocument:
    def css(self, selector):
        if selector != "article.card":
            raise AssertionError("unexpected selector")
        return [FakeCard({"h2::text": "Fixture HTML Story", "a::attr(href)": "/story/1",
                          "time::attr(datetime)": "2026-10-16T13:00:00Z"})]


class ScraplingFeedTests(unittest.TestCase):
    def test_scrapling_transport_uses_standard_get_and_disables_stealth_proxy_and_redirects(self):
        calls = []
        fake_response = SimpleNamespace(status=200, headers={"Content-Type": "application/json"},
                                        body=b'{"items":[]}', json=lambda: {"items": []})

        def get(url, **kwargs):
            calls.append((url, kwargs))
            return fake_response

        scrapling_module = ModuleType("scrapling")
        fetchers_module = ModuleType("scrapling.fetchers")
        fetchers_module.Fetcher = SimpleNamespace(get=get)
        with patch.dict(sys.modules, {"scrapling": scrapling_module, "scrapling.fetchers": fetchers_module}):
            response = ScraplingTransport().get("https://feeds.example.test/api", {"User-Agent": "test"}, 3.5)

        self.assertEqual(response.status, 200)
        self.assertEqual(response.json_data, {"items": []})
        url, args = calls[0]
        self.assertEqual(url, "https://feeds.example.test/api")
        self.assertEqual(args["headers"], {"User-Agent": "test"})
        self.assertFalse(args["stealthy_headers"])
        self.assertIsNone(args["impersonate"])
        self.assertFalse(args["follow_redirects"])
        self.assertEqual(args["retries"], 0)
        self.assertIsNone(args["proxy"])
        self.assertIsNone(args["proxy_rotator"])
        self.assertTrue(args["verify"])

    def test_rss_parsing_maps_contract_fields_and_never_keeps_description_body(self):
        xml = b'''<?xml version="1.0"?><rss version="2.0"><channel><item>
          <guid>item-1</guid><title>Fixture announcement</title>
          <link>https://publisher.example.test/story/1?utm_source=x</link>
          <pubDate>Fri, 16 Oct 2026 13:00:00 GMT</pubDate>
          <description>Do not retain this body</description><symbol>MOCK</symbol>
        </item></channel></rss>'''
        items = parse_xml_feed(xml, approved_source())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "item-1")
        self.assertEqual(items[0]["title"], "Fixture announcement")
        self.assertEqual(items[0]["published_at"], "Fri, 16 Oct 2026 13:00:00 GMT")
        self.assertNotIn("description", items[0])
        provider = ScraplingNewsProvider([approved_source()], FakeTransport(
            HTTPResponse(200, {"Content-Type": "application/rss+xml"}, xml)))
        result = provider.read("fixture-feed")
        record = result.records[0]
        self.assertEqual(record["record_type"], "news")
        self.assertEqual(record["domain"], "finance")
        self.assertEqual(record["provider"], "fixture-provider")
        self.assertEqual(record["publisher"], "Fixture Publisher")
        self.assertEqual(record["event_ts"], "2026-10-16T13:00:00Z")
        self.assertEqual(record["policy"]["authorized_scopes"], ["local_personal"])
        self.assertNotIn("description", json.dumps(record))

    def test_atom_namespace_and_unzoned_time_are_preserved_without_guessing(self):
        xml = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
          <id>tag:example.test,2026:2</id><title>Atom story</title>
          <link rel="alternate" href="/a/2"/><published>2026-10-16T13:00:00</published>
        </entry></feed>'''
        provider = ScraplingNewsProvider([approved_source(feed_format="atom")], FakeTransport(
            HTTPResponse(200, {}, xml)))
        record = provider.read("fixture-feed").records[0]
        self.assertEqual(record["source_url"], "https://feeds.example.test/a/2")
        self.assertIsNone(record["published_at"])
        self.assertEqual(record["published_at_raw"], "2026-10-16T13:00:00")

    def test_json_api_uses_configured_field_mapping(self):
        source = approved_source(feed_format="json", json_items_path="data.results",
                                 json_fields={"title": "headline", "url": "link",
                                              "published_at": "created", "id": "uuid"})
        data = {"data": {"results": [{"headline": "Fixture API story", "link": "/story/2",
                                       "created": "2026-10-16T13:00:00Z", "uuid": "j2"}]}}
        items = parse_json_items(data, source)
        self.assertEqual(items[0]["title"], "Fixture API story")
        self.assertEqual(items[0]["url"], "https://feeds.example.test/story/2")
        self.assertEqual(items[0]["id"], "j2")

    def test_html_listing_uses_only_explicit_css_selectors(self):
        source = approved_source(feed_format="html", html_selectors={
            "item": "article.card", "title": "h2::text", "url": "a::attr(href)",
            "published_at": "time::attr(datetime)",
        })
        items = parse_html_listing(FakeDocument(), source)
        self.assertEqual(items[0]["title"], "Fixture HTML Story")
        self.assertEqual(items[0]["url"], "https://feeds.example.test/story/1")

    def test_unapproved_source_is_denied_without_transport(self):
        transport = FakeTransport()
        source = approved_source(terms_reviewed=False)
        provider = ScraplingNewsProvider([source], transport)
        result = provider.read(source.source_id)
        self.assertEqual(result.health.status, "denied")
        self.assertEqual(result.health.error_code, "policy_denied")
        self.assertEqual(transport.calls, [])

    def test_empty_authorized_scopes_are_denied_without_transport(self):
        transport = FakeTransport()
        source = approved_source(authorized_scopes=())
        result = ScraplingNewsProvider([source], transport).read(source.source_id)
        self.assertEqual(result.health.status, "denied")
        self.assertEqual(result.health.error_code, "policy_denied")
        self.assertEqual(transport.calls, [])

    def test_exact_host_and_https_are_required(self):
        with self.assertRaises(ValueError):
            approved_source(feed_url="https://evil.example.test/rss.xml").validate()
        with self.assertRaises(ValueError):
            approved_source(feed_url="http://feeds.example.test/rss.xml").validate()

    def test_304_reuses_cached_records_and_429_exposes_retry_after(self):
        xml = b'<rss><channel><item><guid>x</guid><title>T</title><link>https://publisher.example.test/x</link></item></channel></rss>'
        transport = FakeTransport(
            HTTPResponse(200, {"ETag": '"one"'}, xml),
            HTTPResponse(304, {"ETag": '"one"'}, b""),
            HTTPResponse(429, {"Retry-After": "120"}, b""),
        )
        ticks = iter([0.0, 61.0, 122.0, 123.0])
        source = approved_source(min_poll_seconds=60)
        provider = ScraplingNewsProvider([source], transport,
                                         clock=lambda: datetime(2026, 10, 16, 14, 0, tzinfo=timezone.utc),
                                         monotonic=lambda: next(ticks))
        first = provider.read("fixture-feed")
        second = provider.read("fixture-feed")
        third = provider.read("fixture-feed")
        fourth = provider.read("fixture-feed")
        self.assertEqual(first.health.status, "ok")
        self.assertEqual(second.health.status, "not_modified")
        self.assertEqual(len(second.records), 1)
        self.assertEqual(transport.calls[1][1]["If-None-Match"], '"one"')
        self.assertEqual(third.health.status, "failed")
        self.assertEqual(third.health.error_code, "rate_limited")
        self.assertEqual(third.health.retry_after_seconds, 120.0)
        self.assertEqual(fourth.health.status, "rate_limited_local")
        self.assertEqual(fourth.health.error_code, "retry_after")
        self.assertEqual(len(transport.calls), 3)

    def test_feed_failure_returns_cached_metadata_with_stale_status(self):
        xml = b'<rss><channel><item><guid>x</guid><title>T</title><link>https://publisher.example.test/x</link></item></channel></rss>'
        transport = FakeTransport(HTTPResponse(200, {}, xml), HTTPResponse(503, {}, b""))
        source = approved_source(min_poll_seconds=0, max_stale_seconds=30)
        moments = iter([datetime(2026, 10, 16, 14, 0, tzinfo=timezone.utc),
                        datetime(2026, 10, 16, 14, 1, tzinfo=timezone.utc)])
        provider = ScraplingNewsProvider([source], transport, clock=lambda: next(moments),
                                         monotonic=iter([0.0, 61.0]).__next__)
        first = provider.read("fixture-feed")
        second = provider.read("fixture-feed")
        self.assertEqual(first.records[0]["status"], "fresh")
        self.assertEqual(second.health.status, "failed")
        self.assertTrue(second.health.is_stale)
        self.assertEqual(second.records[0]["status"], "stale")
        self.assertEqual(second.records[0]["received_at"], first.records[0]["received_at"])


if __name__ == "__main__":
    unittest.main()
