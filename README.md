# Refurbished price tracker

The GitHub Actions workflow checks Refurbed.be's Google Pixel 10 Pro offers
every day at 07:00 UTC and emails the cheapest in-stock configuration with at
least 256 GB of storage. It follows Refurbed's storage, color, appearance, and
battery selectors, so an appearance choice that is unavailable for one
configuration can still be found under another. You can also start it from the
repository's **Actions** tab with **Run workflow**.

## Set up Gmail delivery

1. Revoke the Gmail app password that was previously written in `tracker.py`.
   Create a new app password for this workflow; do not reuse or commit the old
   one.
2. In the GitHub repository, open **Settings → Secrets and variables →
   Actions** and add:
   - `SENDER_EMAIL`: the Gmail address used to send the message.
   - `SENDER_PASSWORD`: a newly generated Gmail app password.
   - `RECEIVER_EMAIL` (optional): the destination address. If omitted, the
     message is sent to `SENDER_EMAIL`.
3. Ensure GitHub Actions is enabled for the repository. The scheduled workflow
   runs from the repository's default branch.

The workflow runs at 07:00 UTC (08:00 in Belgian winter time and 09:00 in
Belgian summer time). Scraping or Gmail errors fail the workflow so they are
visible in its run log rather than sending a placeholder price.

## Run locally

Install the dependencies and Chromium with:

```sh
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Set `SENDER_EMAIL` and `SENDER_PASSWORD`, optionally set `RECEIVER_EMAIL`, and
run `python tracker.py`.
