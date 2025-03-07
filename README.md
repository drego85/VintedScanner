# Vinted Scanner & Notifier

## Overview

Vinted Scanner is a Python script designed to automatically search for new items listed on [Vinted](https://www.vinted.com). Since Vinted does not provide a built-in feature for real-time notifications about new items, this project aims to fill that gap by allowing you to receive notifications as soon as new items matching your search criteria are listed.

With this script, you no longer need to manually open the Vinted app and search for your favorite items every time. Instead, the script runs periodically and sends notifications via **e-mail**, **Slack**, or **Telegram** whenever a new item is detected. It also keeps track of already analyzed items to prevent duplicate notifications.

## Features

- **Automated Search**: The script performs searches on Vinted according to your custom queries.
- **Real-time Notifications**: Get notified instantly via:
  - **Email**
  - **Slack**
  - **Telegram**
- **Duplicate Prevention**: The script logs items that have already been analyzed, so you won't receive multiple notifications for the same item.
- **Periodic Execution**: The script is designed to be executed periodically, such as through a cron job.

## Getting Started

### Prerequisites

- Python 3.6 or higher

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

1. **SMTP Settings (for Email notifications)**:
   - Set your SMTP username, password, server, and recipient address in the configuration file:
     ```python
     smtp_username = "your_email@example.com"
     smtp_psw = "your_password"
     smtp_server = "smtp.example.com"
     smtp_toaddrs = ["Recipient <recipient@example.com>"]
     smtp_from = "sender@example.com"
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

4. **Vinted Search Queries**:
   - In the same configuration file, define the queries you want to execute on Vinted. These queries can include search keywords, catalog categories, or specific brands. You can define multiple queries, and the script will iterate through each one:
     ```python
     queries = [
         {
             'page': '1',
             'per_page': '96',
             'search_text': 'jeans',
             'catalog_ids': '',
             'brand_ids' : '417',  # Example brand ID
             'order': 'newest_first',
         },
         {
             'page': '1',
             'per_page': '96',
             'search_text': 't-shirt',
             'catalog_ids': '',
             'brand_ids' : '',
             'order': 'newest_first',
         }
     ]
     ```

   **Notes on search parameters**:
   - `search_text`: Keyword to search (leave blank for all items).
   - `catalog_ids`: Category ID to search in (leave blank for all categories).
   - `brand_ids`: Brand ID to search for a specific brand.
   - `order`: Sorting order (`newest_first`, `relevance`, `price_high_to_low`, `price_low_to_high`).

### Running the Script

To run the script manually, use:

```bash
python3 vinted_scanner.py
```

The script will check for new items based on your queries and send notifications accordingly.

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

Logs are stored in the `vinted_scanner.log` file. The script uses a rotating log handler to ensure that logs don't grow too large.

c### Contributing and Supporting the Project

There are two ways you can contribute to the development of **Tosint**:

1. **Development Contributions**:

   Please ensure that your code follows best practices and includes relevant tests.

2. **Donation Support**:
   If you find this project useful and would like to support its development, you can also make a donation via [Buy Me a Coffee](https://buymeacoffee.com/andreadraghetti). Your support is greatly appreciated and helps to keep this project going!

   [![Buy Me a Coffee](https://img.shields.io/badge/-Buy%20Me%20a%20Coffee-orange?logo=buy-me-a-coffee&logoColor=white&style=flat-square)](https://buymeacoffee.com/andreadraghetti)

### License

This project is licensed under the GNU General Public License v3.0.