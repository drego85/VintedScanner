#!/usr/bin/env python3
"""Discover Vinted filters and generate scanner query dictionaries."""
import argparse
import json
import sys
import time
import unicodedata
from urllib.parse import urlsplit, urlunsplit

import requests

import Config
import vinted_scanner as scanner


CACHE_PATH = scanner.PROJECT_DIR / ".vinted_catalog_cache.json"
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60
MAX_DISPLAYED_OPTIONS = 60


class DiscoveryError(RuntimeError):
    """Raised when a Vinted discovery endpoint returns an invalid response."""


def build_service_url(marketplace_url, path):
    """Build an API URL using the configured marketplace domain."""
    catalog_url = urlsplit(scanner.build_catalog_url(marketplace_url))
    return urlunsplit((catalog_url.scheme, catalog_url.netloc, path, "", ""))


class VintedDiscoveryClient:
    """Read categories and contextual filters from Vinted."""

    def __init__(self, marketplace_url, session=None):
        self.marketplace_url = marketplace_url.rstrip("/")
        self.session = session or scanner.initialize_vinted_session(marketplace_url)
        self.headers = scanner.build_api_headers(marketplace_url)
        self.locale = self.headers.get("Locale")
        anon_id = self.session.cookies.get("anon_id")
        if anon_id:
            self.headers["X-Anon-Id"] = anon_id

    def _get_json(self, path, params=None):
        url = build_service_url(self.marketplace_url, path)
        try:
            response = self.session.get(
                url,
                params=params,
                headers=self.headers,
                timeout=scanner.REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.exceptions.RequestException, ValueError) as error:
            raise DiscoveryError(f"Vinted discovery request failed: {error}") from error

        if not isinstance(data, dict):
            raise DiscoveryError("Vinted discovery response is not a JSON object")
        return data

    def get_catalogs(self):
        data = self._get_json(
            "/svc-navigation/navigation_catalogs",
            {"virtual_categories": "false"},
        )
        catalogs = data.get("catalogs")
        if not isinstance(catalogs, list):
            raise DiscoveryError("Vinted did not return a valid category tree")
        return catalogs

    def get_filters(self, query):
        data = self._get_json(
            "/svc-filters/filters",
            scanner.build_catalog_params(query),
        )
        filters = data.get("filters")
        if not isinstance(filters, list):
            raise DiscoveryError("Vinted did not return a valid filter list")
        return filters

    def get_facets(self, filter_code, query):
        params = scanner.build_catalog_params(query)
        params["filter_code"] = filter_code
        data = self._get_json("/svc-filters/filters/facets", params)
        options = data.get("options")
        if not isinstance(options, list):
            raise DiscoveryError(
                f"Vinted did not return valid options for filter '{filter_code}'"
            )
        return options

    def search_filter(self, filter_code, search_text, query):
        params = scanner.build_catalog_params(query)
        params.update(
            {
                "filter_search_code": filter_code,
                "filter_search_text": search_text,
            }
        )
        data = self._get_json("/svc-filters/filters/search", params)
        options = data.get("options")
        if not isinstance(options, list):
            raise DiscoveryError(
                f"Vinted did not return search results for filter '{filter_code}'"
            )
        return options

    def preview(self, query, limit=5):
        preview_query = dict(query)
        preview_query["page"] = "1"
        preview_query["per_page"] = str(limit)
        try:
            return scanner.get_catalog_items(
                self.session,
                scanner.build_catalog_url(self.marketplace_url),
                scanner.build_catalog_params(preview_query),
                self.headers,
            )
        except scanner.CatalogError as error:
            raise DiscoveryError(str(error)) from error


def load_catalogs(client, refresh=False, cache_path=CACHE_PATH):
    """Load the category tree from a short-lived marketplace-specific cache."""
    locale = client.locale if isinstance(client.locale, str) else None
    if not refresh:
        cached_catalogs = read_catalog_cache(
            client.marketplace_url,
            locale,
            cache_path,
        )
        if cached_catalogs is not None:
            return cached_catalogs

    catalogs = client.get_catalogs()
    write_catalog_cache(client.marketplace_url, locale, catalogs, cache_path)
    return catalogs


def read_catalog_cache(marketplace_url, locale=None, cache_path=CACHE_PATH):
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    if not isinstance(data, dict):
        return None
    fetched_at = data.get("fetched_at")
    if (
        data.get("marketplace_url") != marketplace_url.rstrip("/")
        or data.get("locale") != locale
        or not isinstance(fetched_at, (int, float))
        or time.time() - fetched_at > CACHE_MAX_AGE_SECONDS
        or not isinstance(data.get("catalogs"), list)
    ):
        return None
    return data["catalogs"]


def write_catalog_cache(marketplace_url, locale, catalogs, cache_path=CACHE_PATH):
    data = {
        "marketplace_url": marketplace_url.rstrip("/"),
        "locale": locale,
        "fetched_at": time.time(),
        "catalogs": catalogs,
    }
    try:
        cache_path.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def flatten_catalogs(catalogs, parent_titles=()):
    """Flatten the category tree while retaining human-readable paths."""
    flattened = []
    for catalog in catalogs:
        if not isinstance(catalog, dict):
            continue
        catalog_id = catalog.get("id")
        title = catalog.get("title")
        if catalog_id is None or not title:
            continue

        titles = parent_titles + (str(title),)
        flattened.append(
            {
                "id": str(catalog_id),
                "title": str(title),
                "label": " > ".join(titles),
            }
        )
        children = catalog.get("catalogs") or []
        if isinstance(children, list):
            flattened.extend(flatten_catalogs(children, titles))
    return flattened


def flatten_options(options, parent_titles=()):
    """Flatten grouped facet options such as clothing sizes."""
    flattened = []
    for option in options:
        if not isinstance(option, dict):
            continue
        option_id = option.get("id")
        title = option.get("title")
        if option_id is None or not title:
            continue

        titles = parent_titles + (str(title),)
        children = option.get("options") or []
        if isinstance(children, list) and children:
            flattened.extend(flatten_options(children, titles))
            continue
        flattened.append(
            {
                "id": str(option_id),
                "title": str(title),
                "label": " > ".join(titles),
                "items_count": option.get("items_count"),
            }
        )
    return flattened


def normalize_search_text(value):
    normalized = unicodedata.normalize("NFKD", str(value))
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold()


def search_entries(entries, search_text):
    """Search localized labels without requiring matching accents or case."""
    needle = normalize_search_text(search_text).strip()
    if not needle:
        return list(entries)
    return [
        entry
        for entry in entries
        if needle in normalize_search_text(entry.get("label", entry.get("title", "")))
    ]


def parse_selection(value, option_count, multiple=True):
    """Parse comma-separated numbers and ranges into zero-based indexes."""
    value = value.strip()
    if not value:
        return []

    indexes = []
    for part in value.split(","):
        part = part.strip()
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError("selection ranges must be ascending")
            indexes.extend(range(start - 1, end))
        else:
            indexes.append(int(part) - 1)

    if any(index < 0 or index >= option_count for index in indexes):
        raise ValueError("selection is outside the displayed range")
    indexes = list(dict.fromkeys(indexes))
    if not multiple and len(indexes) != 1:
        raise ValueError("select exactly one option")
    return indexes


def print_entries(entries, output_fn=print):
    for index, entry in enumerate(entries, start=1):
        count = entry.get("items_count")
        count_label = f" ({count} items)" if count is not None else ""
        output_fn(f"{index}. {entry['label']} [{entry['id']}]{count_label}")


def select_entries(entries, multiple=True, input_fn=input, output_fn=print):
    """Display entries and ask the user for one or more numbered choices."""
    displayed = entries[:MAX_DISPLAYED_OPTIONS]
    print_entries(displayed, output_fn)
    if len(entries) > len(displayed):
        output_fn(
            f"Showing the first {len(displayed)} of {len(entries)} results. "
            "Refine the search to see other values."
        )

    prompt = "Select one or more numbers (comma-separated, blank to skip): "
    if not multiple:
        prompt = "Select one number (blank to skip): "
    while True:
        try:
            indexes = parse_selection(input_fn(prompt), len(displayed), multiple)
            return [displayed[index] for index in indexes]
        except (TypeError, ValueError) as error:
            output_fn(f"Invalid selection: {error}")


def ask_yes_no(prompt, default=False, input_fn=input, output_fn=print):
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        answer = input_fn(prompt + suffix).strip().casefold()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        output_fn("Please answer yes or no.")


def choose_category(catalogs, locale=None, input_fn=input, output_fn=print):
    entries = flatten_catalogs(catalogs)
    locale_hint = f"use the {locale} locale language; " if locale else ""
    while True:
        search_text = input_fn(
            f"Category search ({locale_hint}leave empty to search all categories): "
        ).strip()
        matches = search_entries(entries, search_text)
        if not matches:
            output_fn("No matching categories were found. Try another search.")
            continue
        selected = select_entries(
            matches,
            multiple=False,
            input_fn=input_fn,
            output_fn=output_fn,
        )
        return selected[0] if selected else None


def configure_search_filter(client, descriptor, query, input_fn=input, output_fn=print):
    code = descriptor["code"]
    title = descriptor.get("title") or code
    search_text = input_fn(f"\nSearch {title} (leave empty to skip): ").strip()
    if not search_text:
        return []
    options = flatten_options(client.search_filter(code, search_text, query))
    if not options:
        output_fn(f"No matching {title} options were found.")
        return []
    return select_entries(options, input_fn=input_fn, output_fn=output_fn)


def configure_facet(client, descriptor, query, input_fn=input, output_fn=print):
    code = descriptor["code"]
    title = descriptor.get("title") or code
    if not ask_yes_no(
        f"\nConfigure {title}?",
        input_fn=input_fn,
        output_fn=output_fn,
    ):
        return []

    options = flatten_options(client.get_facets(code, query))
    if not options:
        output_fn(f"No {title} options are available for this search.")
        return []

    search_text = input_fn(
        f"Filter {title} values by text (leave empty to show all): "
    ).strip()
    matches = search_entries(options, search_text)
    if not matches:
        output_fn(f"No matching {title} options were found.")
        return []
    return select_entries(matches, input_fn=input_fn, output_fn=output_fn)


def configure_price(query, input_fn=input, output_fn=print):
    if not ask_yes_no(
        "\nConfigure Price?",
        input_fn=input_fn,
        output_fn=output_fn,
    ):
        return
    price_from = input_fn("Minimum price (blank for none): ").strip()
    price_to = input_fn("Maximum price (blank for none): ").strip()
    currency = input_fn("Currency code (blank for marketplace default): ").strip()
    if price_from:
        query["price_from"] = price_from
    if price_to:
        query["price_to"] = price_to
    if currency:
        query["currency"] = currency.upper()


def configure_dynamic_filters(client, query, input_fn=input, output_fn=print):
    """Discover and configure every filter available for the current context."""
    descriptors = client.get_filters(query)
    for descriptor in descriptors:
        if not isinstance(descriptor, dict) or not descriptor.get("code"):
            continue
        code = descriptor["code"]
        query["filters"].setdefault(code, [])
        if code == "price":
            configure_price(query, input_fn, output_fn)
        elif descriptor.get("display_type") == "list_search":
            selected = configure_search_filter(
                client, descriptor, query, input_fn, output_fn
            )
            query["filters"][code] = [entry["id"] for entry in selected]
        else:
            selected = configure_facet(client, descriptor, query, input_fn, output_fn)
            query["filters"][code] = [entry["id"] for entry in selected]


def print_preview(client, query, output_fn=print):
    items = client.preview(query)
    output_fn("\nMatching item preview:")
    if not items:
        output_fn("No matching items were found.")
        return
    for item in items:
        normalized = scanner.normalize_item(item)
        if normalized:
            output_fn(
                f"- {normalized['title']} — {normalized['price']} — "
                f"{normalized['url']}"
            )


def build_query_interactively(client, catalogs, input_fn=input, output_fn=print):
    """Run the interactive wizard and return a scanner query dictionary."""
    output_fn("Vinted query builder\n")
    keyword = input_fn("Search keyword (leave empty for none): ").strip()
    category = choose_category(
        catalogs,
        locale=client.locale,
        input_fn=input_fn,
        output_fn=output_fn,
    )
    query = {
        "page": "1",
        "per_page": "96",
        "search_text": keyword,
        "order": "newest_first",
        "filters": {"catalog": [category["id"]] if category else []},
    }
    configure_dynamic_filters(client, query, input_fn, output_fn)
    return query


def print_generated_query(query, output_fn=print):
    output_fn("\nCopy this dictionary into the queries list in Config.py:\n")
    output_fn(format_query_mapping(query))


def format_query_mapping(mapping, level=0):
    """Format nested query dictionaries while keeping ID lists compact."""
    indentation = "    " * level
    child_indentation = "    " * (level + 1)
    lines = [f"{indentation}{{"]
    entries = list(mapping.items())
    for index, (key, value) in enumerate(entries):
        comma = "," if index < len(entries) - 1 else ""
        key_text = json.dumps(str(key), ensure_ascii=False)
        if isinstance(value, dict):
            nested_lines = format_query_mapping(value, level + 1).splitlines()
            lines.append(f"{child_indentation}{key_text}: {{")
            lines.extend(nested_lines[1:-1])
            lines.append(f"{nested_lines[-1]}{comma}")
        else:
            value_text = json.dumps(value, ensure_ascii=False)
            lines.append(f"{child_indentation}{key_text}: {value_text}{comma}")
    lines.append(f"{indentation}}}")
    return "\n".join(lines)


def build_context_query(category=None, keyword=""):
    return {
        "page": "1",
        "per_page": "96",
        "search_text": keyword,
        "order": "newest_first",
        "filters": {"catalog": [str(category)] if category else []},
    }


def run_categories(client, search_text, refresh_cache=False):
    catalogs = load_catalogs(client, refresh_cache)
    entries = search_entries(flatten_catalogs(catalogs), search_text)
    print_entries(entries[:MAX_DISPLAYED_OPTIONS])
    return 0 if entries else 1


def run_brands(client, search_text, category=None, keyword=""):
    query = build_context_query(category, keyword)
    options = flatten_options(client.search_filter("brand", search_text, query))
    print_entries(options[:MAX_DISPLAYED_OPTIONS])
    return 0 if options else 1


def run_filters(client, category=None, keyword="", show_options=False):
    query = build_context_query(category, keyword)
    descriptors = client.get_filters(query)
    for descriptor in descriptors:
        if not isinstance(descriptor, dict) or not descriptor.get("code"):
            continue
        code = descriptor["code"]
        title = descriptor.get("title") or code
        print(f"{code}: {title}")
        if show_options and code != "price":
            if descriptor.get("display_type") == "list_search":
                print("  Use the brands command to search this filter.")
                continue
            options = flatten_options(client.get_facets(code, query))
            for option in options[:MAX_DISPLAYED_OPTIONS]:
                print(f"  {option['id']}: {option['label']}")
    return 0


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Discover Vinted filters and build scanner queries."
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="download the category tree even when a valid cache exists",
    )
    subparsers = parser.add_subparsers(dest="command")

    categories = subparsers.add_parser(
        "categories", help="search the Vinted category tree"
    )
    categories.add_argument("search", nargs="?", default="")

    brands = subparsers.add_parser("brands", help="search Vinted brands")
    brands.add_argument("search")
    brands.add_argument("--category", help="category ID used as search context")
    brands.add_argument("--keyword", default="", help="item search keyword")

    filters = subparsers.add_parser(
        "filters", help="list filters available for a search context"
    )
    filters.add_argument("--category", help="category ID used as search context")
    filters.add_argument("--keyword", default="", help="item search keyword")
    filters.add_argument(
        "--options",
        action="store_true",
        help="also display available facet IDs and titles",
    )
    return parser.parse_args()


def main():
    """Run a lookup command or the interactive query builder."""
    args = parse_arguments()
    try:
        client = VintedDiscoveryClient(Config.vinted_url)
        if args.command == "categories":
            return run_categories(client, args.search, args.refresh_cache)
        if args.command == "brands":
            return run_brands(client, args.search, args.category, args.keyword)
        if args.command == "filters":
            return run_filters(client, args.category, args.keyword, args.options)

        catalogs = load_catalogs(client, args.refresh_cache)
        query = build_query_interactively(client, catalogs)
        if ask_yes_no("\nPreview matching items?", default=True):
            print_preview(client, query)
        print_generated_query(query)
        return 0
    except requests.exceptions.RequestException as error:
        print(f"Unable to initialize the Vinted session: {error}", file=sys.stderr)
        return 1
    except (AttributeError, DiscoveryError, OSError, ValueError) as error:
        print(f"Query builder error: {error}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nQuery builder cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
