#!/usr/bin/python3
import sys
import time
import Config
import smtplib
import logging
import requests
from datetime import datetime
import email.message, email.policy, email.utils

# Inizializzo i LOG
logging.basicConfig(filename="vinted_scanner.log",
                    format="%(asctime)s - %(funcName)10s():%(lineno)s - %(levelname)s - %(message)s",
                    level=logging.INFO)

timeoutconnection = 10
list_analyzed_items = []

headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) Gecko/20100101 Firefox/128.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/png,image/svg+xml,*/*;q=0.8',
    'Accept-Language': 'it-IT,it;q=0.8,en-US;q=0.5,en;q=0.3',
    # 'Accept-Encoding': 'gzip, deflate, br, zstd',
    'Referer': 'http://192.168.2.40:9070/',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'cross-site',
    'Sec-GPC': '1',
    'Priority': 'u=0, i',
    'Pragma': 'no-cache',
    'Cache-Control': 'no-cache',
    # Requests doesn't support trailers
    # 'TE': 'trailers',
}

def load_analyzed_item():
    try:
        f = open("vinted_items.txt", "r", errors="ignore")

        for line in f:
            if line:
                line = line.rstrip()
                list_analyzed_items.append(line)

        f.close()

    except IOError as e:
        logging.error(e, exc_info=True)
        sys.exit()
    except Exception as e:
        logging.error(e, exc_info=True)
        raise


def save_analyzed_item(hash):
    try:
        f = open("vinted_items.txt", "a")
        f.write(str(hash) + "\n")
        f.close()
    except IOError as e:
        logging.error(e, exc_info=True)
        sys.exit()
    except Exception as e:
        logging.error(e, exc_info=True)
        raise


def send_email(item_title, item_url, item_image):
    try:

        msg = "Nuovo oggetto Vinted in vendita\n\n"
        msg = msg + f"Titolo: {item_title}\n"
        msg = msg + f"URL: {item_url}\n"
        msg = msg + f"Immagine: {item_image}\n"
        msg = msg + "\n\n"


        em = email.message.EmailMessage(email.policy.SMTP)
        em['To'] = Config.smtp_toaddrs
        em['From'] = Config.smtp_username 
        em['Subject'] = "Vinted Scanner"
        em['Date'] = email.utils.formatdate()
        em['Message-ID'] = email.utils.make_msgid()
        em.set_content(msg)


        smtpserver = smtplib.SMTP(Config.smtp_server, 587)
        smtpserver.ehlo()
        smtpserver.starttls()
        smtpserver.login(Config.smtp_username, Config.smtp_psw)

        smtpserver.send_message(em)

        smtpserver.quit()
    except Exception as e:
        logging.error(e, exc_info=True)
        pass

def main():

    # Carico la lista degli hash gia individuati
    load_analyzed_item()

    # Obtain session Cookies
    url = "https://www.vinted.it"

    session = requests.Session()
    session.post(url, headers=headers, timeout=timeoutconnection)

    cookies = session.cookies.get_dict()
    
    # Call the API to obtain list of items

    # 1499: Bambini > Giochi
    # 1193: Bambini
    params = {
        'page': '1',
        'per_page': '96',
        'search_text': 'faba',
        'catalog_ids': '1193',
        'order': 'newest_first',
    }

    response = requests.get("https://www.vinted.it/api/v2/catalog/items", params=params, cookies=cookies, headers=headers)

    data = response.json()

    if data:
        for item in data["items"]:

            item_id = str(item["id"])
            item_title = item["title"]
            item_url = item["url"]
            item_image = item["photo"]["full_size_url"]

            if item_id not in list_analyzed_items:

                send_email(item_title, item_url, item_image)

                list_analyzed_items.append(item_id)
                save_analyzed_item(item_id)

if __name__ == "__main__":
    main()
