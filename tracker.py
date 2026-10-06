import json
import os
import smtplib
import ssl
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage

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

            product_group = None
            for script_text in page.locator(
                'script[type="application/ld+json"]'
            ).all_inner_texts():
                try:
                    structured_data = json.loads(script_text)
                except json.JSONDecodeError:
                    continue

                pending = [structured_data]
                while pending:
                    node = pending.pop()
                    if isinstance(node, list):
                        pending.extend(node)
                    elif isinstance(node, dict):
                        node_type = node.get("@type", [])
                        if (
                            node.get("name") == PRODUCT_NAME
                            and (
                                node_type == "ProductGroup"
                                or (
                                    isinstance(node_type, list)
                                    and "ProductGroup" in node_type
                                )
                            )
                        ):
                            product_group = node
                            break
                        pending.extend(
                            value
                            for key, value in node.items()
                            if key in ("@graph", "hasVariant")
                        )
                if product_group:
                    break

            if product_group is None:
                raise RuntimeError(
                    f"Could not find structured product data for {PRODUCT_NAME} "
                    f"on {BASE_URL}"
                )

            offers = []
            for variant in product_group.get("hasVariant", []):
                if variant.get("size") not in ("256 GB", "512 GB"):
                    continue

                variant_offers = variant.get("offers", [])
                if isinstance(variant_offers, dict):
                    variant_offers = [variant_offers]

                for offer in variant_offers:
                    if (
                        offer.get("priceCurrency") != "EUR"
                        or not str(offer.get("availability", "")).endswith(
                            "/InStock"
                        )
                    ):
                        continue
                    try:
                        amount = Decimal(str(offer["price"]))
                    except (KeyError, InvalidOperation):
                        continue
                    offers.append((amount, variant, offer))

            if not offers:
                raise RuntimeError(
                    f"No in-stock 256 GB or 512 GB EUR offers found for "
                    f"{PRODUCT_NAME} on {BASE_URL}"
                )

            amount, variant, offer = min(offers, key=lambda item: item[0])
            whole, fraction = f"{amount:,.2f}".split(".")
            price_text = f"€{whole.replace(',', '.')},{fraction}"
            return {
                "price": price_text,
                "variant": variant["name"],
                "url": offer.get("url", product_group.get("url", page.url)),
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

Current displayed price: {price_data['price']}
Cheapest matching in-stock option: {price_data['variant']}
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
