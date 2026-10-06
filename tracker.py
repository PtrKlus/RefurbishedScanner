import os
import re
import smtplib
import ssl
from decimal import Decimal
from email.message import EmailMessage
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

BASE_URL = "https://www.refurbed.be/p/google-pixel-10-pro/"
PRODUCT_NAME = "Google Pixel 10 Pro"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def scrape_product_price() -> dict[str, str]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60_000)

            offers = []
            cards = page.locator(
                "ul.swiper-wrapper a[href*='/p/google-pixel-10-pro/']"
            )
            page.wait_for_function(
                """() => Array.from(document.querySelectorAll(
                    "ul.swiper-wrapper a[href*='/p/google-pixel-10-pro/']"
                )).some(card => /\\d+\\s*GB/.test(card.innerText) &&
                    /€/.test(card.innerText))
                """,
                timeout=30_000,
            )
            for card in cards.all():
                lines = [
                    line.strip()
                    for line in card.inner_text().splitlines()
                    if line.strip()
                ]
                storage_index = next(
                    (
                        index
                        for index, line in enumerate(lines)
                        if re.fullmatch(r"\d+\s*GB", line)
                    ),
                    None,
                )
                price_line = next(
                    (line for line in lines if "€" in line), None
                )
                if storage_index is None or price_line is None:
                    continue

                storage = int(re.search(r"\d+", lines[storage_index]).group())
                if storage < 256:
                    continue

                price_match = re.search(
                    r"€\s*([\d.]+(?:,\d{2})?)", price_line
                )
                if price_match is None:
                    continue
                amount = Decimal(
                    price_match.group(1).replace(".", "").replace(",", ".")
                )

                color = lines[0]
                condition = (
                    lines[storage_index + 1]
                    if storage_index + 1 < len(lines)
                    else "Condition not listed"
                )
                offers.append(
                    (
                        amount,
                        f"{color} - {storage} GB - {condition}",
                        urljoin(page.url, card.get_attribute("href") or ""),
                    )
                )

            if not offers:
                raise RuntimeError(
                    f"No offers of 256 GB or more found for {PRODUCT_NAME} "
                    f"on {BASE_URL}"
                )

            amount, variant, offer_url = min(offers, key=lambda item: item[0])
            whole, fraction = f"{amount:,.2f}".split(".")
            price_text = f"€{whole.replace(',', '.')},{fraction}"
            return {
                "price": price_text,
                "variant": variant,
                "url": offer_url,
            }
        finally:
            browser.close()


def send_email(price_data: dict[str, str]) -> None:
    sender = os.environ.get("SENDER_EMAIL")
    if not sender:
        raise RuntimeError("Set the SENDER_EMAIL environment variable.")

    password = os.environ.get("SENDER_PASSWORD")
    if not password:
        raise RuntimeError("Set the SENDER_PASSWORD environment variable.")

    recipient = os.environ.get("RECEIVER_EMAIL") or sender

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = f"Daily Refurbed price update: {PRODUCT_NAME}"
    message.set_content(
        f"""Daily price check for the {PRODUCT_NAME} on Refurbed.be

Cheapest listed option with at least 256 GB storage: {price_data['price']}
Configuration: {price_data['variant']}
Product page: {price_data['url']}
"""
    )

    with smtplib.SMTP_SSL(
        "smtp.gmail.com", 465, context=ssl.create_default_context()
    ) as server:
        server.login(sender, password)
        server.send_message(message)


def main() -> None:
    print(f"Checking {BASE_URL}")
    price_data = scrape_product_price()
    send_email(price_data)
    recipient = os.environ.get("RECEIVER_EMAIL") or os.environ["SENDER_EMAIL"]
    print(f"Daily price email sent to {recipient}.")


if __name__ == "__main__":
    main()
