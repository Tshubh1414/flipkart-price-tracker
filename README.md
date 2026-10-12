# Flipkart Price Tracker

A small Python project that checks a product page on a GitHub Actions schedule and stores successful observations in `price_history.csv`.

## Important limitations

- This project uses best-effort HTML parsing, not an official live-price feed. Flipkart may change page markup, require browser-rendered data, or block automated requests. If the price cannot be identified confidently, the script fails and does not write a fabricated price.
- Check the store's terms and use only permitted access methods. Do not bypass CAPTCHA, access controls, or rate limits.
- GitHub Actions scheduled jobs are not a persistent process and cannot run every second. The workflow is configured for every five minutes, subject to GitHub's scheduling delays.
- The CSV is committed to the repository. In a public repository, product URL and price history will be public.
- The workflow logs when a target is reached; it does not yet send Telegram notifications.

## Files

- `tracker.py`: one price check and CSV append
- `requirements.txt`: Python dependencies
- `.github/workflows/price-tracker.yml`: scheduled/manual workflow
- `price_history.csv`: generated after a successful check

## Setup

1. Create a GitHub repository and upload these files.
2. Open **Settings → Secrets and variables → Actions → Variables**.
3. Add:
   - `PRODUCT_URL`: the full product URL
   - `TARGET_PRICE`: target price in rupees, e.g. `2000`
4. Open **Settings → Actions → General → Workflow permissions** and enable **Read and write permissions**.
5. Open the **Actions** tab, select **Product Price Tracker**, then click **Run workflow**.
6. Inspect the run logs. If it succeeds, `price_history.csv` will be committed to the repository.

## Test locally

```bash
python -m pip install -r requirements.txt
# Windows PowerShell:
$env:PRODUCT_URL = "https://www.flipkart.com/your-product-link"
$env:TARGET_PRICE = "2000"
python tracker.py
```

Do not commit credentials, cookies, or personal data.
