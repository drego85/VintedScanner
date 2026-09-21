import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import vinted_query_builder as builder


class CategoryTests(unittest.TestCase):
    catalogs = [
        {
            "id": 1,
            "title": "Women",
            "catalogs": [
                {
                    "id": 183,
                    "title": "Jeans",
                    "catalogs": [],
                }
            ],
        }
    ]

    def test_flatten_catalogs_keeps_full_path(self):
        entries = builder.flatten_catalogs(self.catalogs)

        self.assertEqual(entries[1]["id"], "183")
        self.assertEqual(entries[1]["label"], "Women > Jeans")

    def test_search_entries_ignores_case_and_accents(self):
        entries = [{"id": "1", "label": "Camicie perché eleganti"}]

        self.assertEqual(
            builder.search_entries(entries, "PERCHE"),
            entries,
        )


class FacetTests(unittest.TestCase):
    def test_flatten_options_uses_group_titles(self):
        options = [
            {
                "id": "WOMEN",
                "title": "Women",
                "options": [
                    {"id": "3", "title": "S", "items_count": 12},
                    {"id": "4", "title": "M", "items_count": 8},
                ],
            }
        ]

        flattened = builder.flatten_options(options)

        self.assertEqual(flattened[0]["label"], "Women > S")
        self.assertEqual(flattened[1]["id"], "4")

    def test_parse_selection_supports_lists_and_ranges(self):
        self.assertEqual(
            builder.parse_selection("1,3-4", option_count=5),
            [0, 2, 3],
        )

    def test_parse_selection_rejects_out_of_range_values(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            builder.parse_selection("6", option_count=5)


class CacheTests(unittest.TestCase):
    def test_load_catalogs_reuses_valid_marketplace_cache(self):
        client = Mock(marketplace_url="https://www.vinted.it", locale="it-IT")
        client.get_catalogs.return_value = [{"id": 2, "title": "New"}]

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "catalogs.json"
            builder.write_catalog_cache(
                client.marketplace_url,
                client.locale,
                [{"id": 1, "title": "Cached"}],
                cache_path,
            )

            catalogs = builder.load_catalogs(client, cache_path=cache_path)

        self.assertEqual(catalogs[0]["title"], "Cached")
        client.get_catalogs.assert_not_called()

    def test_expired_cache_is_refreshed(self):
        client = Mock(marketplace_url="https://www.vinted.it", locale="it-IT")
        client.get_catalogs.return_value = [{"id": 2, "title": "New"}]

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "catalogs.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "marketplace_url": client.marketplace_url,
                        "locale": client.locale,
                        "fetched_at": 0,
                        "catalogs": [{"id": 1, "title": "Old"}],
                    }
                ),
                encoding="utf-8",
            )

            catalogs = builder.load_catalogs(client, cache_path=cache_path)

        self.assertEqual(catalogs[0]["title"], "New")
        client.get_catalogs.assert_called_once()

    def test_non_object_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "catalogs.json"
            cache_path.write_text("[]", encoding="utf-8")

            self.assertIsNone(
                builder.read_catalog_cache(
                    "https://www.vinted.it",
                    "it-IT",
                    cache_path,
                )
            )


class ClientTests(unittest.TestCase):
    def test_search_filter_uses_context_and_search_parameters(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"options": [{"id": "53", "title": "Nike"}]}
        session = Mock()
        session.cookies.get.return_value = None
        session.get.return_value = response
        client = builder.VintedDiscoveryClient(
            "https://www.vinted.it",
            session=session,
        )

        options = client.search_filter(
            "brand",
            "nike",
            builder.build_context_query(category=183, keyword="jeans"),
        )

        self.assertEqual(options[0]["id"], "53")
        call = session.get.call_args
        self.assertTrue(call.args[0].endswith("/svc-filters/filters/search"))
        self.assertEqual(call.kwargs["params"]["filter_search_code"], "brand")
        self.assertEqual(call.kwargs["params"]["attribute_ids[catalog]"], "183")


class WizardTests(unittest.TestCase):
    def test_wizard_builds_a_scanner_compatible_query(self):
        client = Mock()
        client.locale = "it-IT"
        client.get_filters.return_value = [
            {"code": "brand", "title": "Brand", "display_type": "list_search"},
            {"code": "status", "title": "Condition", "display_type": "list"},
            {"code": "price", "title": "Price", "display_type": "hybrid_price"},
        ]
        client.search_filter.return_value = [{"id": "53", "title": "Nike"}]
        client.get_facets.return_value = [
            {"id": "1", "title": "New without tags"},
            {"id": "2", "title": "Very good"},
        ]
        answers = iter(
            [
                "jeans",
                "jeans",
                "1",
                "nike",
                "1",
                "y",
                "",
                "1,2",
                "n",
            ]
        )

        query = builder.build_query_interactively(
            client,
            [{"id": 183, "title": "Jeans", "catalogs": []}],
            input_fn=lambda prompt: next(answers),
            output_fn=lambda message: None,
        )

        self.assertEqual(query["search_text"], "jeans")
        self.assertEqual(query["filters"]["catalog"], ["183"])
        self.assertEqual(query["filters"]["brand"], ["53"])
        self.assertEqual(query["filters"]["status"], ["1", "2"])
        self.assertEqual(query["order"], "newest_first")

    def test_generated_query_uses_expanded_braces_and_indentation(self):
        output = []
        query = builder.build_context_query(category=2994, keyword="ajax")

        builder.print_generated_query(query, output_fn=output.append)

        rendered_query = output[1]
        lines = rendered_query.splitlines()
        self.assertEqual(lines[0], "{")
        self.assertEqual(lines[-1], "}")
        self.assertIn('    "filters": {', rendered_query)
        self.assertIn('        "catalog": ["2994"]', rendered_query)

    @patch("vinted_query_builder.parse_arguments")
    @patch("vinted_query_builder.VintedDiscoveryClient")
    def test_keyboard_interrupt_exits_cleanly(self, client, parse_arguments):
        parse_arguments.return_value = SimpleNamespace(
            command=None,
            refresh_cache=False,
        )
        client.side_effect = KeyboardInterrupt

        with patch("sys.stderr"):
            exit_code = builder.main()

        self.assertEqual(exit_code, 130)


if __name__ == "__main__":
    unittest.main()
