#!/usr/bin/env python3
import sys
import time
import json
import Config
import smtplib
import logging
import requests
import email.utils
from datetime import datetime
from email.message import EmailMessage
from logging.handlers import RotatingFileHandler


# Configura il gestore di log per la rotazione dei file
handler = RotatingFileHandler("vinted_scanner.log", maxBytes=5000000, backupCount=5)

logging.basicConfig(handlers=[handler], 
                    format="%(asctime)s - %(filename)s - %(funcName)10s():%(lineno)s - %(levelname)s - %(message)s", 
                    level=logging.INFO)

timeoutconnection = 10
list_analyzed_items = []

headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) Gecko/20100101 Firefox/128.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/png,image/svg+xml,*/*;q=0.8',
    'Accept-Language': 'it-IT,it;q=0.8,en-US;q=0.5,en;q=0.3',
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
}

def load_analyzed_item():
    try:
        with open("vinted_items.txt", "r", errors="ignore") as f:
            for line in f:
                if line:
                    list_analyzed_items.append(line.rstrip())
    except IOError as e:
        logging.error(e, exc_info=True)
        sys.exit()


def save_analyzed_item(hash):
    try:
        with open("vinted_items.txt", "a") as f:
            f.write(str(hash) + "\n")
    except IOError as e:
        logging.error(e, exc_info=True)
        sys.exit()


def send_email(item_title: str, item_price: str, item_url: str, item_image: str):
    """
    Invia una notifica via email riguardo un nuovo articolo su Vinted.
    
    :param item_title: Il titolo dell'articolo.
    :param item_url: L'URL dell'articolo.
    :param item_price: Prezzo dell'articolo
    :param item_image: L'URL dell'immagine dell'articolo.
    """
    try:
        # Crea il messaggio email
        msg = EmailMessage()
        msg['To'] = Config.smtp_toaddrs
        msg['From'] = email.utils.formataddr(('Vinted Scanner', Config.smtp_username))
        msg['Subject'] = "Vinted Scanner - Nuovo Articolo Trovato"
        msg['Date'] = email.utils.formatdate(localtime=True)
        msg['Message-ID'] = email.utils.make_msgid()

        # Formatta il contenuto dell'email
        body = f"{item_title}\n{item_price}\n{item_url}\n{item_image}"

        msg.set_content(body)
        
        # Apertura sicura della connessione SMTP usando il context manager
        with smtplib.SMTP(Config.smtp_server, 587) as smtpserver:
            smtpserver.ehlo()  # Identificazione del client sul server
            smtpserver.starttls()  # Avvia crittografia TLS
            smtpserver.ehlo()  # Rinegoziazione del protocollo sicuro

            # Autenticazione
            smtpserver.login(Config.smtp_username, Config.smtp_psw)
            
            # Invio del messaggio
            smtpserver.send_message(msg)
            logging.info("Email inviata con successo riguardo il nuovo articolo.")
    
    except smtplib.SMTPException as e:
        logging.error(f"Errore SMTP durante l'invio dell'email: {e}", exc_info=True)
    except Exception as e:
        logging.error(f"Errore inatteso durante l'invio dell'email: {e}", exc_info=True)


def send_slack_message(item_title: str, item_price: str, item_url: str, item_image: str):
    """
    Invia un messaggio a un canale Slack tramite un webhook, includendo il prezzo dell'articolo.
    
    :param item_title: Il titolo dell'articolo.
    :param item_url: L'URL dell'articolo.
    :param item_image: L'URL dell'immagine dell'articolo.
    :param item_price: Il prezzo dell'articolo.
    """

    webhook_url = Config.slack_webhook_url 

    # Formatta il contenuto del messaggio
    message = f"*{item_title}*\n🏷️ {item_price}\n🔗 {item_url}\n📷 {item_image}"
    slack_data = {"text": message}

    try:
        response = requests.post(
            webhook_url, 
            data=json.dumps(slack_data),
            headers={"Content-Type": "application/json"},
            timeout=timeoutconnection
        )

        if response.status_code != 200:
            logging.error(f"Richiesta Slack fallita. Status code: {response.status_code}, Response: {response.text}")
        else:
            logging.info("Messaggio inviato con successo a Slack")

    except requests.exceptions.RequestException as e:
        logging.error(f"Errore nell'invio del messaggio Slack: {e}")

def main():

    # Carico la lista degli hash gia individuati
    load_analyzed_item()

    # Obtain session Cookies
    url = "https://www.vinted.it"

    session = requests.Session()
    session.post(url, headers=headers, timeout=timeoutconnection)

    cookies = session.cookies.get_dict()
    
    # Call the API to obtain list of items

    # catalog_ids
    # 1499: Bambini > Giochi
    # 1193: Bambini

    # order: relevance, newest_first, price_high_to_low, price_low_to_high

    # brand_ids
    # 1036110: Faba
    params = {
        'page': '1',
        'per_page': '96',
        'search_text': '',
        'catalog_ids': '',
        'brand_ids' : '1036110',
        'order': 'newest_first',
    }

    response = requests.get("https://www.vinted.it/api/v2/catalog/items", params=params, cookies=cookies, headers=headers)

    data = response.json()

    if data:
        for item in data["items"]:

            print(item)
            item_id = str(item["id"])
            item_title = item["title"]
            item_url = item["url"]
            item_price = item["price"]
            item_image = item["photo"]["full_size_url"]

            if item_id not in list_analyzed_items:

                send_email(item_title, item_price,item_url, item_image)
                send_slack_message(item_title, item_price, item_url, item_image)

                list_analyzed_items.append(item_id)
                save_analyzed_item(item_id)

if __name__ == "__main__":
    main()
