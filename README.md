# Vinted Scanner & Notifier

## Overview

Vinted Scanner is a Python script designed to periodically search for new items listed on [Vinted](https://www.vinted.com). Since Vinted does not provide built-in automatic notifications for saved searches, this project sends notifications when a scheduled scan finds a new matching item.

With this script, you no longer need to manually open the Vinted app and search for your favorite items every time. Instead, the script runs periodically and sends notifications via **e-mail**, **Slack**, or **Telegram** whenever a new item is detected. It also keeps track of already analyzed items to prevent duplicate notifications.

## Features

- **Automated Search**: The script performs searches on Vinted according to your custom queries.
- **Automatic Notifications**: Receive notifications through:
  - **Email**
  - **Slack**
  - **Telegram**
- **Duplicate Prevention**: The script logs items that have already been analyzed, so you won't receive multiple notifications for the same item.
- **Interactive Query Builder**: Discover localized categories and contextual
  filters without manually looking up Vinted IDs.
- **Periodic Execution**: The script is designed to be executed periodically, such as through a cron job.

## Getting Started

### Prerequisites

- Python 3.11 or higher

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/drego85/VintedScanner.git
   cd VintedScanner
   ```

2. Install the required dependencies:
   ```bash
   pip3 install -r requirements.txt
   ```

3. Copy the configuration file `Config.sample.py` to `Config.py`:
   ```bash
   cp Config.sample.py Config.py
   ```

### Configuration

To customize the script for your needs, you must configure the `Config.py` file.

> [!WARNING]
> The search configuration format changed in September 2026 when Vinted
> replaced the legacy
> `/api/v2/catalog/items` endpoint. The old `catalog_ids`, `brand_ids`,
> `size_ids`, `status_ids`, `color_ids`, and `material_ids` keys are no longer
> supported. Existing `Config.py` files must be updated to use the `filters`
> dictionary shown below. The scanner exits with a configuration error when it
> detects a legacy key.

> [!IMPORTANT]
> This independent project is not affiliated with or endorsed by Vinted. It
> uses undocumented internal API endpoints that may change without notice. Use
> the scanner responsibly and comply with the rules applicable to your Vinted
> marketplace.

1. **SMTP Settings (for Email notifications)**:
   - Set your SMTP username, password, server, and recipient address in the configuration file:
     ```python
     smtp_username = "your_email@example.com"
     smtp_psw = "your_password"
     smtp_server = "smtp.example.com"
     smtp_toaddrs = ["Recipient <recipient@example.com>"]
     ```

2. **Slack Webhook (for Slack notifications)**:
   - Set the `slack_webhook_url` with your Slack Incoming Webhook URL:
     ```python
     slack_webhook_url = "https://hooks.slack.com/services/..."
     ```

3. **Telegram Bot (for Telegram notifications)**:
   - Set the Telegram bot token and chat ID:
     ```python
     telegram_bot_token = "your_bot_token"
     telegram_chat_id = "your_chat_id"
     ```

4. **Vinted Marketplace**:
   - Set the marketplace URL and locale for the country to search. The scanner
     derives the matching country-specific API host automatically:
     ```python
     vinted_url = "https://www.vinted.it"
     vinted_locale = "it-IT"
     ```
   - Change the domain and locale for other marketplaces, for example
     `https://www.vinted.fr` with `fr-FR` or `https://www.vinted.de` with
     `de-DE`.

5. **Vinted Search Queries**:
   - Define one or more searches in the same configuration file. Filter values
     must be lists of Vinted IDs; use an empty list to disable a filter:
     ```python
     queries = [
         {
             "page": "1",
             "per_page": "96",
             "search_text": "jeans",
             "order": "newest_first",
             "filters": {
                 "catalog": [],
                 "brand": ["417"],  # Example brand ID
                 "size": [],
                 "status": [],
                 "color": [],
                 "material": [],
             },
         },
         {
             "page": "1",
             "per_page": "96",
             "search_text": "t-shirt",
             "order": "newest_first",
             "filters": {
                 "catalog": ["2996"],  # Example category ID
                 "brand": [],
                 "size": [],
                 "status": [],
                 "color": [],
                 "material": [],
             },
         },
     ]
     ```

   **Notes on search parameters**:
   - `search_text`: Keyword to search (leave blank for all items).
   - `filters`: Dynamic Vinted filters. Supported examples include `catalog`,
     `brand`, `size`, `status`, `color`, and `material`.
   - Multiple IDs can be selected in a filter, for example
     `"brand": ["417", "53"]`.
   - `price_from`, `price_to`, and `currency` can be added at query level when
     price filtering is required.
   - `order`: Keep this set to `newest_first` so newly listed items are checked
     before older results.

The scanner initializes an anonymous session on the configured marketplace and
then searches through `https://api.<marketplace-domain>/svc-catalogue/items`.
Browser cookies, access tokens, CSRF tokens, and Cloudflare cookies must not be
copied into the configuration.

### Interactive query builder

Run the query builder to discover categories, brands, sizes, colors,
conditions, materials, and other filters available for the selected category:

```bash
python3 vinted_query_builder.py
```

The wizard uses the marketplace and locale from `Config.py`, optionally shows
a matching-item preview, and prints a query dictionary ready to copy into the
`queries` list. Category names must be searched in the language identified by
`vinted_locale` (for example, Italian with `it-IT`). The builder displays the
active locale in the category prompt. Queries always use `newest_first`, which
is required to reliably detect newly listed items. The builder never modifies
`Config.py`.

Copy the generated dictionary into `queries` in `Config.py`. The sample
configuration intentionally contains no active searches, preventing accidental
notifications before a query has been reviewed.

Categories are cached locally for 24 hours. Force an update with:

```bash
python3 vinted_query_builder.py --refresh-cache
```

The following commands provide direct lookups without starting the wizard:

```bash
python3 vinted_query_builder.py categories jeans
python3 vinted_query_builder.py brands nike --category 183
python3 vinted_query_builder.py filters --category 183 --options
```

Brand and filter results are requested dynamically because their availability
depends on the selected category and current Vinted catalogue.

### Running the Script

To run the script manually, use:

```bash
python3 vinted_scanner.py
```

The script will check for new items based on your queries and send notifications accordingly.
An item is saved as analyzed only after every configured notification succeeds.
If a delivery fails, the item is retried during the next execution. When more
than one channel is enabled, this may repeat a notification on a channel that
had already succeeded.

For a new installation, initialize the local item database before enabling
notifications:

```bash
python3 vinted_scanner.py --dry-run
```

Review the displayed items, then run the scanner normally. This prevents the
current search backlog from generating notifications on the first scheduled
execution. Skip this initialization only if notifications for all currently
matching items are intentional.

### Dry-run mode

To check the configured searches without sending e-mail, Slack, or Telegram
notifications, use:

```bash
python3 vinted_scanner.py --dry-run
```

New items are printed in the terminal and are still saved in the local
`vinted_items.txt` database. Consequently, they will be considered already
analyzed during subsequent executions. This runtime file is created
automatically on first use and is excluded from Git.

To display all available command-line options:

```bash
python3 vinted_scanner.py -h
```

### Automation with Cron

To run the script periodically, you can set up a cron job. For example, to run the script every hour:

1. Open the crontab editor:
   ```bash
   crontab -e
   ```

2. Add the following line to schedule the script to run every hour:
   ```bash
   0 * * * * /usr/bin/python3 /path/to/vinted_scanner.py >> /path/to/logfile.log 2>&1
   ```

This will run the script every hour and log the output to `logfile.log`.

### Logging

Logs are stored next to the script in `vinted_scanner.log`. The local item
database is also resolved relative to the script, so both paths remain stable
when the scanner is started by cron.

### Tests

Run the automated tests with:

```bash
python3 -m unittest discover -s tests -v
```

The GitHub Actions workflow runs the same test suite on every push and pull
request.

### Contributing and Supporting the Project

There are two ways you can contribute to the development of **Vinted Scanner**:

1. **Development Contributions**:

   Please ensure that your code follows best practices and includes relevant tests.

2. **Donation Support**:
   If you find this project useful and would like to support its development, you can also make a donation via [Buy Me a Coffee](https://buymeacoffee.com/andreadraghetti). Your support is greatly appreciated and helps to keep this project going!

   [![Buy Me a Coffee](https://img.shields.io/badge/-Buy%20Me%20a%20Coffee-orange?logo=buy-me-a-coffee&logoColor=white&style=flat-square)](https://buymeacoffee.com/andreadraghetti)

### License

This project is licensed under the [GNU General Public License v3.0](LICENSE).
