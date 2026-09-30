import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import vinted_scanner as scanner


class ConfigurationTests(unittest.TestCase):
    def test_build_catalog_url_uses_marketplace_domain(self):
        self.assertEqual(
            scanner.build_catalog_url("https://www.vinted.it"),
            "https://api.vinted.it/svc-catalogue/items",
        )

    def test_build_catalog_params_encodes_dynamic_filters(self):
        params = scanner.build_catalog_params(
            {
                "page": "1",
                "per_page": "96",
                "search_text": "jeans",
                "order": "newest_first",
                "filters": {"brand": [417, "53"], "size": []},
            }
        )

        self.assertEqual(params["attribute_ids[brand]"], "417,53")
        self.assertNotIn("attribute_ids[size]", params)

    def test_query_exclusions_are_local_and_not_sent_to_vinted(self):
        params = scanner.build_catalog_params(
            {
                "page": "1",
                "per_page": "96",
                "search_text": "Switch",
                "order": "newest_first",
                "exclude_title_terms": ["DS"],
                "filters": {},
            }
        )

        self.assertEqual(params["search_text"], "Switch")
        self.assertNotIn("exclude_title_terms", params)

    def test_title_exclusions_are_whole_word_and_have_no_global_fallback(self):
        with patch.object(scanner.Config, "exclude_title_terms", ["DS"], create=True):
            self.assertFalse(scanner.title_contains_excluded_term("Nintendo DS"))

        self.assertTrue(scanner.title_contains_excluded_term("Nintendo ds", ["DS"]))
        self.assertFalse(scanner.title_contains_excluded_term("DSi console", ["DS"]))

    def test_build_catalog_params_rejects_legacy_keys(self):
        with self.assertRaisesRegex(ValueError, "legacy query keys"):
            scanner.build_catalog_params(
                {
                    "page": "1",
                    "per_page": "96",
                    "order": "newest_first",
                    "filters": {},
                    "catalog_ids": "2996",
                }
            )

    def test_empty_query_list_is_rejected(self):
        with patch.object(scanner.Config, "queries", []):
            with self.assertRaisesRegex(ValueError, "at least one search"):
                scanner.load_configuration(dry_run=True)


class DatabaseTests(unittest.TestCase):
    def test_load_creates_missing_database_and_returns_a_set(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "items.txt"

            items = scanner.load_analyzed_items(database_path)

            self.assertEqual(items, set())
            self.assertTrue(database_path.exists())

    def test_load_deduplicates_item_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "items.txt"
            database_path.write_text("123\n123\n456\n", encoding="utf-8")

            self.assertEqual(
                scanner.load_analyzed_items(database_path),
                {"123", "456"},
            )


class NotificationTests(unittest.TestCase):
    @patch("vinted_scanner.requests.post")
    def test_telegram_escapes_html_and_sets_timeout(self, post):
        post.return_value.raise_for_status.return_value = None
        post.return_value.json.return_value = {"ok": True}

        result = scanner.send_telegram_message(
            "A < B & C",
            "10 & 20 EUR",
            "https://example.test/item?a=1&b=2",
            None,
            "gl.<inet>",
        )

        self.assertTrue(result)
        call = post.call_args
        self.assertEqual(call.kwargs["timeout"], scanner.REQUEST_TIMEOUT_SECONDS)
        self.assertIn("A &lt; B &amp; C", call.kwargs["data"]["text"])
        self.assertIn("a=1&amp;b=2", call.kwargs["data"]["text"])
        self.assertIn("Matched query: gl.&lt;inet&gt;", call.kwargs["data"]["text"])

    @patch("vinted_scanner.send_email", return_value=False)
    def test_all_configured_notifications_must_succeed(self, send_email):
        with patch.object(
            scanner.Config, "smtp_username", "user@example.test"
        ), patch.object(
            scanner.Config, "smtp_server", "smtp.example.test"
        ), patch.object(
            scanner.Config, "slack_webhook_url", ""
        ), patch.object(
            scanner.Config, "telegram_bot_token", ""
        ), patch.object(
            scanner.Config, "telegram_chat_id", ""
        ):
            result = scanner.send_notifications("Title", "10 EUR", "url", None)

        self.assertFalse(result)
        send_email.assert_called_once()


class NotificationConfigurationTests(unittest.TestCase):
    notification_defaults = {
        "smtp_username": "",
        "smtp_psw": "",
        "smtp_server": "",
        "smtp_toaddrs": ["User <user@example.test>"],
        "slack_webhook_url": "",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
    }

    def test_normal_run_requires_a_notification_channel(self):
        with patch.multiple(scanner.Config, **self.notification_defaults):
            with self.assertRaisesRegex(ValueError, "at least one"):
                scanner.validate_notification_config(dry_run=False)

    def test_dry_run_does_not_require_a_notification_channel(self):
        with patch.multiple(scanner.Config, **self.notification_defaults):
            scanner.validate_notification_config(dry_run=True)

    def test_partial_telegram_configuration_is_rejected(self):
        values = dict(self.notification_defaults)
        values["telegram_bot_token"] = "token"

        with patch.multiple(scanner.Config, **values):
            with self.assertRaisesRegex(ValueError, "telegram_chat_id"):
                scanner.validate_notification_config(dry_run=False)

    def test_partial_email_configuration_is_rejected(self):
        values = dict(self.notification_defaults)
        values["smtp_username"] = "user@example.test"

        with patch.multiple(scanner.Config, **values):
            with self.assertRaisesRegex(ValueError, "smtp_psw, smtp_server"):
                scanner.validate_notification_config(dry_run=False)


class CatalogueTests(unittest.TestCase):
    def test_missing_items_is_reported_as_catalog_error(self):
        response = Mock(status_code=200)
        response.json.return_value = {"message": "invalid request"}
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response

        with self.assertRaisesRegex(scanner.CatalogError, "valid 'items' list"):
            scanner.get_catalog_items(session, "url", {}, {})

    def test_request_failure_is_reported_as_catalog_error(self):
        session = Mock()
        session.get.side_effect = requests.RequestException("network error")

        with self.assertRaisesRegex(scanner.CatalogError, "request failed"):
            scanner.get_catalog_items(session, "url", {}, {})

    @patch(
        "vinted_scanner.get_catalog_items",
        side_effect=scanner.CatalogError("invalid response"),
    )
    def test_query_failure_is_returned_to_the_caller(self, get_catalog_items):
        with self.assertLogs(level="ERROR"):
            failed = scanner.process_queries(
                Mock(),
                "url",
                [{}],
                {},
                set(),
                dry_run=False,
            )

        self.assertTrue(failed)
        get_catalog_items.assert_called_once()

    @patch("vinted_scanner.process_item", return_value=False)
    @patch("vinted_scanner.get_catalog_items", return_value=[{"id": 123}])
    def test_notification_failure_is_returned_to_the_caller(
        self,
        get_catalog_items,
        process_item,
    ):
        failed = scanner.process_queries(
            Mock(),
            "url",
            [{}],
            {},
            set(),
            dry_run=False,
        )

        self.assertTrue(failed)
        get_catalog_items.assert_called_once()
        process_item.assert_called_once()
        self.assertEqual(
            process_item.call_args.kwargs["matched_query"],
            "filters only",
        )

    @patch("vinted_scanner.save_analyzed_item")
    @patch("vinted_scanner.send_notifications", return_value=True)
    @patch("vinted_scanner.get_catalog_items")
    def test_exclusion_in_one_query_does_not_block_another_query(
        self,
        get_catalog_items,
        send_notifications,
        save_analyzed_item,
    ):
        item = {
            "id": 123,
            "title": "Nintendo DS console",
            "url": "https://example.test/items/123",
            "price": {"amount": "10", "currency_code": "EUR"},
        }
        get_catalog_items.side_effect = [[item], [item]]
        queries = [
            {
                "params": {"search_text": "Switch"},
                "exclude_title_terms": ["DS"],
                "query_key": "switch-query",
            },
            {
                "params": {"search_text": "console"},
                "exclude_title_terms": [],
                "query_key": "console-query",
            },
        ]
        analyzed_items = set()

        failed = scanner.process_queries(
            Mock(), "url", queries, {}, analyzed_items, dry_run=False
        )

        self.assertFalse(failed)
        self.assertIn("excluded:switch-query:123", analyzed_items)
        self.assertIn("123", analyzed_items)
        send_notifications.assert_called_once()
        save_analyzed_item.assert_any_call(
            "excluded:switch-query:123", scanner.ITEMS_DATABASE_PATH
        )


class ItemProcessingTests(unittest.TestCase):
    item = {
        "id": 123,
        "title": "Test item",
        "url": "https://example.test/items/123",
        "price": {"amount": "10", "currency_code": "EUR"},
        "photo": {"full_size_url": "https://example.test/image.jpg"},
    }

    @patch("vinted_scanner.send_notifications", return_value=False)
    def test_failed_notification_does_not_save_item(self, send_notifications):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "items.txt"
            database_path.touch()
            analyzed_items = set()

            succeeded = scanner.process_item(
                self.item,
                analyzed_items,
                dry_run=False,
                database_path=database_path,
                matched_query="gl.inet",
            )

            self.assertFalse(succeeded)
            self.assertEqual(database_path.read_text(encoding="utf-8"), "")
            self.assertEqual(analyzed_items, set())
            send_notifications.assert_called_once()
            self.assertEqual(send_notifications.call_args.args[-1], "gl.inet")

    def test_relative_item_url_is_resolved_against_marketplace(self):
        with patch.object(scanner.Config, "vinted_url", "https://www.vinted.it"):
            normalized = scanner.normalize_item(
                {
                    "id": 123,
                    "title": "Test item",
                    "url": "/items/123-test-item",
                }
            )

        self.assertEqual(
            normalized["url"],
            "https://www.vinted.it/items/123-test-item",
        )

    def test_absolute_item_url_is_preserved(self):
        normalized = scanner.normalize_item(self.item)

        self.assertEqual(normalized["url"], self.item["url"])

    @patch("vinted_scanner.send_notifications", return_value=True)
    def test_successful_notification_saves_item(self, send_notifications):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "items.txt"
            database_path.touch()
            analyzed_items = set()

            succeeded = scanner.process_item(
                self.item,
                analyzed_items,
                dry_run=False,
                database_path=database_path,
            )

            self.assertTrue(succeeded)
            self.assertEqual(database_path.read_text(encoding="utf-8"), "123\n")
            self.assertEqual(analyzed_items, {"123"})
            send_notifications.assert_called_once()

    @patch("vinted_scanner.print_dry_run_item")
    def test_dry_run_prints_and_saves_item(self, print_item):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "items.txt"
            database_path.touch()
            analyzed_items = set()

            succeeded = scanner.process_item(
                self.item,
                analyzed_items,
                dry_run=True,
                database_path=database_path,
                matched_query="gl.inet",
            )

            self.assertTrue(succeeded)
            self.assertEqual(database_path.read_text(encoding="utf-8"), "123\n")
            self.assertEqual(analyzed_items, {"123"})
            print_item.assert_called_once()
            self.assertEqual(print_item.call_args.args[-1], "gl.inet")


class MainTests(unittest.TestCase):
    @patch("vinted_scanner.process_queries", return_value=True)
    @patch("vinted_scanner.initialize_vinted_session")
    @patch("vinted_scanner.load_analyzed_items", return_value=set())
    @patch("vinted_scanner.load_configuration")
    @patch("vinted_scanner.configure_logging")
    def test_notification_failure_produces_exit_code_one(
        self,
        configure_logging,
        load_configuration,
        load_analyzed_items,
        initialize_session,
        process_queries,
    ):
        load_configuration.return_value = ("catalog-url", [{}], {})
        initialize_session.return_value.cookies.get.return_value = None

        exit_code = scanner.main(dry_run=False)

        self.assertEqual(exit_code, 1)
        configure_logging.assert_called_once()
        load_analyzed_items.assert_called_once()
        process_queries.assert_called_once()


if __name__ == "__main__":
    unittest.main()
