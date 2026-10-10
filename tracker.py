import json
import os
import re
import smtplib
import ssl
from decimal import Decimal
from email.message import EmailMessage
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import sync_playwright

BASE_URL = "https://www.refurbed.at/p/iphone-16-pro/"
PRODUCT_NAME = "iPhone 16 Pro"
PRODUCT_PATH = f"{urlsplit(BASE_URL).path.rstrip('/')}/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _current_offer(page) -> dict[str, object] | None:
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
                if node_type == "BuyAction" or (
                    isinstance(node_type, list) and "BuyAction" in node_type
                ):
                    action = node.get("object", {})
                    offer = action.get("offers") if isinstance(action, dict) else None
                    if isinstance(offer, dict):
                        return offer
                pending.extend(
                    value
                    for key, value in node.items()
                    if key == "@graph"
                )
    return None


def _selected_option_label(page, selector_id: str) -> str:
    select = page.locator(f"#{selector_id}")
    if select.count() == 0:
        return "not listed"
    text = select.evaluate(
        "element => "
        "element.selectedOptions[0]?.textContent?.trim() || ''"
    )
    return re.split(r"\s*[+-]\s*€", text, maxsplit=1)[0].strip()


def _is_two_esim_option(text: str) -> bool:
    return re.search(r"\b2\s*e[\s-]?sims?\b", text, re.IGNORECASE) is not None


def _selected_sim_option(page) -> str:
    for select in page.locator("select").all():
        option_texts = select.locator("option").all_inner_texts()
        if any(_is_two_esim_option(text) for text in option_texts):
            return select.evaluate(
                "element => "
                "element.selectedOptions[0]?.textContent?.trim() || ''"
            )

    title_match = re.search(
        r"Dual-SIM\s*\([^)]*\)", page.title(), re.IGNORECASE
    )
    return title_match.group(0) if title_match else "not listed"


def scrape_product_price() -> dict[str, str]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            response = page.goto(
                BASE_URL, wait_until="domcontentloaded", timeout=60_000
            )
            if response is None or response.status >= 400:
                status = response.status if response is not None else "no response"
                raise RuntimeError(
                    f"Product page request failed ({status}): {BASE_URL}"
                )

            consent_button = page.get_by_role(
                "button",
                name=re.compile(
                    r"Beperkt gebruik|Limited use|Eingeschränkt nutzen", re.I
                ),
            )
            if consent_button.count() and consent_button.first.is_visible():
                consent_button.first.click(timeout=5_000)

            pending_urls = [BASE_URL]
            queued_urls = {BASE_URL}
            offers = []
            visited_count = 0

            while pending_urls:
                offer_url = pending_urls.pop()
                response = page.goto(
                    offer_url, wait_until="domcontentloaded", timeout=60_000
                )
                if response is None or response.status >= 400:
                    status = response.status if response is not None else "no response"
                    raise RuntimeError(
                        f"Could not load Refurbed configuration ({status}): "
                        f"{offer_url}"
                    )

                if consent_button.count() and consent_button.first.is_visible():
                    consent_button.first.click(timeout=5_000)

                storage_select = page.locator("#product-storage")
                storage_text = (
                    storage_select.evaluate(
                        "select => "
                        "select.selectedOptions[0]?.textContent?.trim() || ''"
                    )
                    if storage_select.count()
                    else page.title()
                )
                storage_match = re.search(r"(\d+)\s*GB", storage_text)
                if storage_match is None:
                    raise RuntimeError(
                        f"Could not determine storage for Refurbed configuration "
                        f"{offer_url}: {storage_text!r}"
                    )
                storage = int(storage_match.group(1))
                visited_count += 1

                sim_option = _selected_sim_option(page)
                sim_selects = [
                    select
                    for select in page.locator("select").all()
                    if any(
                        _is_two_esim_option(text)
                        for text in select.locator("option").all_inner_texts()
                    )
                ]

                if storage >= 256:
                    offer = _current_offer(page)
                    if offer is None:
                        raise RuntimeError(
                            f"Refurbed did not provide structured price data for "
                            f"configuration {offer_url}"
                        )
                    if (
                        offer.get("priceCurrency") == "EUR"
                        and str(offer.get("availability", "")).endswith(
                            "/InStock"
                        )
                    ):
                        color = _selected_option_label(page, "product-color")
                        grade = _selected_option_label(page, "product-grade")
                        battery = _selected_option_label(
                            page, "product-battery"
                        )
                        if not _is_two_esim_option(sim_option):
                            amount = Decimal(str(offer["price"]))
                            sim = re.split(
                                r"\s*[+-]\s*€", sim_option, maxsplit=1
                            )[0].strip()
                            offers.append(
                                (
                                    amount,
                                    f"{color} - {storage} GB - {grade} - "
                                    f"{battery} - {sim}",
                                    offer_url,
                                )
                            )

                for select in page.locator(
                    "select[id^='product-']"
                ).all() + sim_selects:
                    select_id = select.get_attribute("id")
                    option_texts = select.locator("option").all_inner_texts()
                    is_sim_selector = any(
                        _is_two_esim_option(text) for text in option_texts
                    )
                    if select_id not in {
                        "product-storage",
                        "product-color",
                        "product-grade",
                        "product-battery",
                    } and not is_sim_selector:
                        continue
                    if _is_two_esim_option(sim_option) and not is_sim_selector:
                        continue
                    if storage < 256 and select_id != "product-storage":
                        continue

                    options = select.locator("option").evaluate_all(
                        """options => options.map(option => ({
                            text: option.textContent.trim(),
                            value: option.value,
                            disabled: option.disabled
                        }))"""
                    )
                    for option in options:
                        if is_sim_selector and _is_two_esim_option(
                            option["text"]
                        ):
                            continue
                        if option["disabled"] or not option["value"].startswith(
                            PRODUCT_PATH
                        ):
                            continue
                        if select_id == "product-storage":
                            option_storage = re.search(
                                r"(\d+)\s*GB", option["text"]
                            )
                            if (
                                option_storage is None
                                or int(option_storage.group(1)) < 256
                            ):
                                continue

                        next_url = urljoin(BASE_URL, option["value"])
                        if next_url not in queued_urls:
                            queued_urls.add(next_url)
                            pending_urls.append(next_url)

            if not offers:
                raise RuntimeError(
                    f"No offers of 256 GB or more found for {PRODUCT_NAME} "
                    f"after checking {visited_count} available configurations"
                )

            amount, variant, offer_url = min(offers, key=lambda item: item[0])
            whole, fraction = f"{amount:,.2f}".split(".")
            price_text = f"€{whole.replace(',', '.')},{fraction}"
            print(
                f"Checked {visited_count} configurations; found "
                f"{len(offers)} in-stock offers with at least 256 GB."
            )
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
        f"""        Daily price check for the {PRODUCT_NAME} on Refurbed.at

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
