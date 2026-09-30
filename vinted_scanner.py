#!/usr/bin/env python3
"""Search Vinted and notify users about newly listed matching items."""
import argparse
import email.utils
import hashlib
import html
import json
import logging
import re
import smtplib
import sys
from email.message import EmailMessage
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

import Config


PROJECT_DIR = Path(__file__).resolve().parent
ITEMS_DATABASE_PATH = PROJECT_DIR / "vinted_items.txt"
LOG_PATH = PROJECT_DIR / "vinted_scanner.log"
REQUEST_TIMEOUT_SECONDS = 30

WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) "
        "Gecko/20100101 Firefox/128.0"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/png,image/svg+xml,*/*;q=0.8"
    ),
    "Accept-Language": "it-IT,it;q=0.8,en-US;q=0.5,en;q=0.3",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Sec-GPC": "1",
    "Priority": "u=0, i",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
}

LEGACY_QUERY_KEYS = {
    "brand_ids",
    "catalog_ids",
    "color_ids",
    "material_ids",
    "size_ids",
    "status_ids",
}

CATALOG_QUERY_KEYS = {
    "currency",
    "filters",
    "order",
    "page",
    "per_page",
    "price_from",
    "price_to",
    "search_text",
}

LOCAL_QUERY_KEYS = {"exclude_title_terms"}
QUERY_EXCLUSION_RECORD_PREFIX = "excluded:"

CATALOG_ORDER_VALUES = {
    "newest_first",
    "price_high_to_low",
    "price_low_to_high",
    "relevance",
}


class CatalogError(RuntimeError):
    """Raised when Vinted does not return a valid catalogue response."""


def configure_logging():
    """Configure a rotating log file relative to this script."""
    handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=5_000_000,
        backupCount=5,
    )
    logging.basicConfig(
        handlers=[handler],
        format=(
            "%(asctime)s - %(filename)s - %(funcName)10s():%(lineno)s - "
            "%(levelname)s - %(message)s"
        ),
        level=logging.INFO,
    )


def parse_arguments():
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description=(
            "Search Vinted for new items and send the configured notifications."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "show new items without sending notifications; "
            "found items are still saved in the local database"
        ),
    )
    return parser.parse_args()


def build_catalog_url(marketplace_url):
    """Build the country-specific catalogue API URL."""
    parsed_url = urlsplit(marketplace_url)
    hostname = parsed_url.hostname

    if parsed_url.scheme != "https" or not hostname:
        raise ValueError("vinted_url must be a valid HTTPS URL")

    if hostname.startswith("www."):
        hostname = hostname[4:]

    if not hostname.startswith("vinted."):
        raise ValueError("vinted_url must point to a Vinted marketplace")

    return urlunsplit(
        ("https", f"api.{hostname}", "/svc-catalogue/items", "", "")
    )


def build_api_headers(marketplace_url):
    """Return headers for the Vinted web catalogue API."""
    marketplace_url = marketplace_url.rstrip("/")
    api_headers = {
        "User-Agent": WEB_HEADERS["User-Agent"],
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": WEB_HEADERS["Accept-Language"],
        "Platform": "web",
        "x-next-app": "marketplace-web",
        "Origin": marketplace_url,
        "Referer": f"{marketplace_url}/",
    }

    locale = getattr(Config, "vinted_locale", None)
    if locale:
        api_headers["Locale"] = locale

    return api_headers


def build_catalog_params(query):
    """Validate a configured query and convert its filters to API parameters."""
    if not isinstance(query, dict):
        raise ValueError("each entry in queries must be a dictionary")

    legacy_keys = LEGACY_QUERY_KEYS.intersection(query)
    if legacy_keys:
        legacy_list = ", ".join(sorted(legacy_keys))
        raise ValueError(
            f"unsupported legacy query keys: {legacy_list}; "
            "replace them with the filters dictionary described in README.md"
        )

    unknown_keys = set(query).difference(CATALOG_QUERY_KEYS | LOCAL_QUERY_KEYS)
    if unknown_keys:
        unknown_list = ", ".join(sorted(unknown_keys))
        raise ValueError(f"unsupported query keys: {unknown_list}")

    if "exclude_title_terms" in query:
        normalize_exclude_title_terms(
            query["exclude_title_terms"], "query exclude_title_terms"
        )

    for required_key in ("page", "per_page", "order", "filters"):
        if required_key not in query:
            raise ValueError(f"missing required query key: {required_key}")

    if str(query["order"]) not in CATALOG_ORDER_VALUES:
        raise ValueError(f"unsupported catalogue order: {query['order']}")

    filters = query["filters"]
    if not isinstance(filters, dict):
        raise ValueError("query filters must be a dictionary")

    params = {
        key: value
        for key, value in query.items()
        if key in CATALOG_QUERY_KEYS
        and key != "filters"
        and value not in (None, "")
    }
    for filter_code, filter_ids in filters.items():
        if not isinstance(filter_code, str) or not filter_code:
            raise ValueError("filter names must be non-empty strings")
        if not isinstance(filter_ids, (list, tuple)):
            raise ValueError(f"filter '{filter_code}' must contain a list of IDs")

        normalized_ids = [str(filter_id) for filter_id in filter_ids]
        if any(not filter_id for filter_id in normalized_ids):
            raise ValueError(f"filter '{filter_code}' contains an empty ID")
        if normalized_ids:
            params[f"attribute_ids[{filter_code}]"] = ",".join(normalized_ids)

    return params


def normalize_exclude_title_terms(terms, setting_name):
    """Validate and normalize a list of local title-exclusion terms."""
    if not isinstance(terms, (list, tuple)):
        raise ValueError(f"{setting_name} must be a list of strings")
    if any(not isinstance(term, str) for term in terms):
        raise ValueError(f"{setting_name} must contain only strings")
    return [term.strip() for term in terms if term.strip()]


def validate_notification_config(dry_run=False):
    """Validate notification channels and require one outside dry-run mode."""
    smtp_values = {
        "smtp_username": getattr(Config, "smtp_username", ""),
        "smtp_psw": getattr(Config, "smtp_psw", ""),
        "smtp_server": getattr(Config, "smtp_server", ""),
    }
    smtp_enabled = any(smtp_values.values())
    if smtp_enabled:
        missing = [name for name, value in smtp_values.items() if not value]
        recipients = getattr(Config, "smtp_toaddrs", None)
        if not recipients:
            missing.append("smtp_toaddrs")
        if missing:
            raise ValueError(
                "incomplete email notification configuration: "
                + ", ".join(missing)
            )

    slack_enabled = bool(getattr(Config, "slack_webhook_url", ""))
    telegram_values = {
        "telegram_bot_token": getattr(Config, "telegram_bot_token", ""),
        "telegram_chat_id": getattr(Config, "telegram_chat_id", ""),
    }
    telegram_enabled = any(telegram_values.values())
    if telegram_enabled:
        missing = [name for name, value in telegram_values.items() if not value]
        if missing:
            raise ValueError(
                "incomplete Telegram notification configuration: "
                + ", ".join(missing)
            )

    if not dry_run and not (smtp_enabled or slack_enabled or telegram_enabled):
        raise ValueError(
            "configure at least one notification channel or use --dry-run"
        )


def load_analyzed_items(database_path=ITEMS_DATABASE_PATH):
    """Load item IDs, creating the local database if it does not exist."""
    database_path.touch(exist_ok=True)
    with database_path.open("r", encoding="utf-8", errors="ignore") as database:
        return {line.strip() for line in database if line.strip()}


def save_analyzed_item(item_id, database_path=ITEMS_DATABASE_PATH):
    """Append an item ID to the local database."""
    with database_path.open("a", encoding="utf-8") as database:
        database.write(f"{item_id}\n")


def send_email(item_title, item_price, item_url, item_image, matched_query=None):
    """Send an email notification and report whether it succeeded."""
    try:
        message = EmailMessage()
        message["To"] = Config.smtp_toaddrs
        message["From"] = email.utils.formataddr(
            ("Vinted Scanner", Config.smtp_username)
        )
        message["Subject"] = "Vinted Scanner - New Item"
        message["Date"] = email.utils.formatdate(localtime=True)
        message["Message-ID"] = email.utils.make_msgid()

        body_lines = [item_title, str(item_price)]
        if matched_query:
            body_lines.append(f"Matched query: {matched_query}")
        body_lines.append(f"🔗 {item_url}")
        if item_image:
            body_lines.append(f"📷 {item_image}")
        message.set_content("\n".join(body_lines))

        with smtplib.SMTP(
            Config.smtp_server,
            587,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as smtp_server:
            smtp_server.ehlo()
            smtp_server.starttls()
            smtp_server.ehlo()
            smtp_server.login(Config.smtp_username, Config.smtp_psw)
            smtp_server.send_message(message)
        logging.info("Email notification sent")
        return True
    except (OSError, TypeError, ValueError, smtplib.SMTPException) as error:
        logging.error("Error sending email notification: %s", error)
        return False


def send_slack_message(item_title, item_price, item_url, item_image, matched_query=None):
    """Send a Slack notification and report whether it succeeded."""
    message_lines = [f"*{item_title}*", f"🏷️ {item_price}"]
    if matched_query:
        message_lines.append(f"Matched query: {matched_query}")
    message_lines.append(f"🔗 {item_url}")
    if item_image:
        message_lines.append(f"📷 {item_image}")

    try:
        response = requests.post(
            Config.slack_webhook_url,
            json={"text": "\n".join(message_lines)},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        logging.info("Slack notification sent")
        return True
    except requests.exceptions.RequestException as error:
        logging.error("Error sending Slack notification: %s", error)
        return False


def send_telegram_message(
    item_title, item_price, item_url, item_image, matched_query=None
):
    """Send an HTML-safe image alert when possible, with a text fallback."""
    message_lines = [
        f"<b>{html.escape(str(item_title))}</b>",
        f"🏷️ {html.escape(str(item_price))}",
    ]
    if matched_query:
        message_lines.append(
            f"Matched query: {html.escape(str(matched_query))}"
        )
    message_lines.append(f"🔗 {html.escape(str(item_url))}")
    if item_image:
        message_lines.append(f"📷 {html.escape(str(item_image))}")

    if item_image:
        photo_url = f"https://api.telegram.org/bot{Config.telegram_bot_token}/sendPhoto"
        try:
            response = requests.post(
                photo_url,
                data={
                    "chat_id": Config.telegram_chat_id,
                    "photo": item_image,
                    "caption": "\n".join(message_lines[:-1]),
                    "parse_mode": "HTML",
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            result = response.json()
            if isinstance(result, dict) and result.get("ok"):
                logging.info("Telegram photo notification sent")
                return True
            logging.warning(
                "Telegram rejected the listing photo; trying text instead"
            )
        except (requests.exceptions.RequestException, ValueError):
            logging.warning(
                "Telegram could not send the listing photo; trying text instead"
            )

    url = f"https://api.telegram.org/bot{Config.telegram_bot_token}/sendMessage"
    params = {
        "chat_id": Config.telegram_chat_id,
        "text": "\n".join(message_lines),
        "parse_mode": "HTML",
        "link_preview_options": json.dumps({"is_disabled": True}),
    }
    try:
        response = requests.post(
            url,
            data=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        result = response.json()
        if isinstance(result, dict) and result.get("ok"):
            logging.info("Telegram notification sent")
            return True
        logging.error("Telegram returned an unsuccessful text response")
        return False
    except requests.exceptions.RequestException as error:
        status_code = (
            error.response.status_code
            if error.response is not None
            else "unknown"
        )
        logging.error("Telegram notification failed (HTTP %s)", status_code)
        return False
    except ValueError:
        logging.error("Telegram returned an invalid text response")
        return False


def send_notifications(
    item_title, item_price, item_url, item_image, matched_query=None
):
    """Send all configured notifications and require every one to succeed."""
    results = []
    if Config.smtp_username and Config.smtp_server:
        results.append(
            send_email(
                item_title, item_price, item_url, item_image, matched_query
            )
        )
    if Config.slack_webhook_url:
        results.append(
            send_slack_message(
                item_title, item_price, item_url, item_image, matched_query
            )
        )
    if Config.telegram_bot_token and Config.telegram_chat_id:
        results.append(
            send_telegram_message(
                item_title, item_price, item_url, item_image, matched_query
            )
        )

    if not results:
        logging.warning(
            "No notification channel is configured; item was not saved"
        )
        return False
    return all(results)


def get_catalog_items(session, catalog_url, params, api_headers):
    """Return catalogue items or raise CatalogError for an invalid response."""
    try:
        response = session.get(
            catalog_url,
            params=params,
            headers=api_headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as error:
        raise CatalogError(f"Vinted catalogue request failed: {error}") from error

    try:
        data = response.json()
    except ValueError as error:
        raise CatalogError(
            f"Vinted returned a non-JSON response (HTTP {response.status_code})"
        ) from error

    if not isinstance(data, dict):
        raise CatalogError(
            "Unexpected Vinted response type: "
            f"expected an object, got {type(data).__name__}"
        )

    items = data.get("items")
    if not isinstance(items, list):
        error_message = data.get("message") or data.get("error") or "unknown error"
        raise CatalogError(
            "Vinted response does not contain a valid 'items' list "
            f"(HTTP {response.status_code}, message: {error_message})"
        )
    return items


def normalize_item(item):
    """Validate and normalize the fields used by notifications."""
    if not isinstance(item, dict):
        logging.warning("Skipping an invalid catalogue item: %r", item)
        return None

    item_id = item.get("id")
    item_title = item.get("title")
    item_url = item.get("url")
    if item_id is None or not item_title or not item_url:
        logging.warning("Skipping an incomplete catalogue item (id: %r)", item_id)
        return None
    item_url = urljoin(f"{Config.vinted_url.rstrip('/')}/", str(item_url))

    price_data = item.get("price") or {}
    if not isinstance(price_data, dict):
        price_data = {}
    amount = price_data.get("amount")
    currency = price_data.get("currency_code")
    item_price = f"{amount} {currency}" if amount is not None and currency else "N/A"

    photo_data = item.get("photo") or {}
    if not isinstance(photo_data, dict):
        photo_data = {}
    return {
        "id": str(item_id),
        "title": item_title,
        "price": item_price,
        "url": item_url,
        "image": photo_data.get("full_size_url"),
    }


def print_dry_run_item(item, matched_query=None):
    """Print one normalized item without sending a notification."""
    print(f"Title: {item['title']}")
    print(f"Price: {item['price']}")
    if matched_query:
        print(f"Matched query: {matched_query}")
    print(f"URL: {item['url']}")
    if item["image"]:
        print(f"Image: {item['image']}")
    print()


def title_contains_excluded_term(title, excluded_terms=None):
    """Return whether the title contains a whole-word exclusion."""
    if not isinstance(excluded_terms, (list, tuple)):
        return False
    for term in excluded_terms:
        if not isinstance(term, str) or not term.strip():
            continue
        pattern = rf"(?<!\w){re.escape(term.strip())}(?!\w)"
        if re.search(pattern, str(title), flags=re.IGNORECASE):
            return True
    return False


def process_item(
    item,
    analyzed_items,
    dry_run,
    database_path=ITEMS_DATABASE_PATH,
    matched_query=None,
    exclude_title_terms=None,
    query_key=None,
):
    """Process one item and persist it only after the requested action succeeds."""
    normalized_item = normalize_item(item)
    if not normalized_item:
        return True

    item_id = normalized_item["id"]
    query_exclusion_record = (
        f"{QUERY_EXCLUSION_RECORD_PREFIX}{query_key}:{item_id}"
        if query_key
        else None
    )
    if item_id in analyzed_items or (
        query_exclusion_record and query_exclusion_record in analyzed_items
    ):
        return True

    if title_contains_excluded_term(normalized_item["title"], exclude_title_terms):
        logging.info(
            "Skipping item with an excluded title term: %s",
            normalized_item["title"],
        )
        record = query_exclusion_record or item_id
        save_analyzed_item(record, database_path)
        analyzed_items.add(record)
        return True

    if dry_run:
        print_dry_run_item(normalized_item, matched_query)
        succeeded = True
    else:
        succeeded = send_notifications(
            normalized_item["title"],
            normalized_item["price"],
            normalized_item["url"],
            normalized_item["image"],
            matched_query,
        )

    if succeeded:
        save_analyzed_item(normalized_item["id"], database_path)
        analyzed_items.add(normalized_item["id"])
    return succeeded


def initialize_vinted_session(marketplace_url):
    """Initialize an anonymous Vinted web session."""
    session = requests.Session()
    response = session.get(
        marketplace_url,
        headers=WEB_HEADERS,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return session


def process_queries(session, catalog_url, queries, api_headers, analyzed_items, dry_run):
    """Process every configured query and report whether any query failed."""
    processing_failed = False
    for configured_query in queries:
        if isinstance(configured_query, dict) and "params" in configured_query:
            params = configured_query["params"]
            excluded_terms = configured_query.get("exclude_title_terms", [])
            query_key = configured_query.get("query_key")
        else:
            # Keep accepting raw API parameter dictionaries for direct callers.
            params = configured_query
            excluded_terms = []
            query_key = None

        matched_query = params.get("search_text") or "filters only"
        try:
            items = get_catalog_items(session, catalog_url, params, api_headers)
        except CatalogError as error:
            logging.error("%s", error)
            processing_failed = True
            continue

        for item in items:
            if not process_item(
                item,
                analyzed_items,
                dry_run,
                matched_query=matched_query,
                exclude_title_terms=excluded_terms,
                query_key=query_key,
            ):
                processing_failed = True
    return processing_failed


def load_configuration(dry_run=False):
    """Validate configuration and return the derived API values."""
    catalog_url = build_catalog_url(Config.vinted_url)
    if not isinstance(Config.queries, list) or not Config.queries:
        raise ValueError("queries must contain at least one search")
    queries = []
    for query in Config.queries:
        params = build_catalog_params(query)
        if "exclude_title_terms" in query:
            excluded_terms = normalize_exclude_title_terms(
                query["exclude_title_terms"],
                "query exclude_title_terms",
            )
        else:
            excluded_terms = []

        query_identity = json.dumps(
            {"params": params, "exclude_title_terms": excluded_terms},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        query_key = hashlib.sha256(
            query_identity.encode("utf-8")
        ).hexdigest()[:16]
        queries.append(
            {
                "params": params,
                "exclude_title_terms": excluded_terms,
                "query_key": query_key,
            }
        )
    validate_notification_config(dry_run)
    return catalog_url, queries, build_api_headers(Config.vinted_url)


def main(dry_run=False):
    """Run the scanner and return a process exit code."""
    configure_logging()
    try:
        catalog_url, queries, api_headers = load_configuration(dry_run)
        analyzed_items = load_analyzed_items()
    except (AttributeError, OSError, ValueError) as error:
        message = f"Configuration or local database error: {error}"
        logging.error(message)
        print(message, file=sys.stderr)
        return 2

    try:
        session = initialize_vinted_session(Config.vinted_url)
    except requests.exceptions.RequestException as error:
        logging.error("Unable to initialize the Vinted session: %s", error)
        return 1

    anon_id = session.cookies.get("anon_id")
    if anon_id:
        api_headers["X-Anon-Id"] = anon_id

    try:
        query_failed = process_queries(
            session, catalog_url, queries, api_headers, analyzed_items, dry_run
        )
    except OSError as error:
        logging.error("Unable to update the local database: %s", error)
        return 1
    return 1 if query_failed else 0


if __name__ == "__main__":
    arguments = parse_arguments()
    sys.exit(main(dry_run=arguments.dry_run))
