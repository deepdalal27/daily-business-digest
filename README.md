# Daily Business Digest

Every morning at about **7:30 AM IST**, GitHub Actions collects the previous
24 hours of stories from the free public RSS feeds of:

- The Economic Times
- Business Standard
- Financial Express

It builds a PDF with each story's headline, the publisher's summary, the time
and a link to the full article, then emails it to you. This runs on GitHub's
servers, so your computer can be switched off.

## One-time setup (about 5 minutes)

Google only lets the account owner create an App Password, so this step has
to be done by you.

1. **Turn on 2-Step Verification** for your Google account (if not already on):
   <https://myaccount.google.com/signinoptions/twosv>
2. **Create an App Password**: <https://myaccount.google.com/apppasswords>
   Name it `Daily Digest` and copy the 16-letter password shown.
3. **Add secrets to this repo**: go to
   *Settings → Secrets and variables → Actions → New repository secret* and add:

   | Name                 | Value                                   |
   |----------------------|-----------------------------------------|
   | `GMAIL_ADDRESS`      | `deepuploads27@gmail.com`               |
   | `GMAIL_APP_PASSWORD` | the 16-letter app password from step 2  |
   | `MAIL_TO`            | `deepuploads27@gmail.com` (optional; comma-separate several addresses) |

4. **Test it now**: *Actions → Daily Business Digest → Run workflow*.
   The email should arrive within a couple of minutes.

## Downloading the PDF without email

Each run also saves the PDF for 30 days: open *Actions*, pick a run, and
download **business-digest** under *Artifacts*.

## Customising

- **Sections / feeds**: edit `feeds.json` (newspaper → section → RSS URL).
- **Time**: edit the `cron` line in `.github/workflows/daily-digest.yml`
  (times are UTC; IST = UTC + 5:30).

## Cost

Free. A private repo gets 2,000 GitHub Actions minutes per month, and this
uses about 1 minute per day. Gmail sending is free.

## Note

This uses only the publishers' public RSS feeds (headlines, summaries, links).
The full e-paper editions are paid, copyrighted products and are not included.
