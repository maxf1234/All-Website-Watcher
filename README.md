# Website Watcher

A public page where anyone can submit a website link. GitHub Actions checks every
submitted site every 5 minutes and emails you whenever one changes. It runs entirely
on free GitHub: no server, database, or paid service.

## How it works

```
 public page (GitHub Pages)         GitHub Issues                GitHub Actions
┌──────────────────────────┐  opens  ┌──────────────┐  triggers  ┌────────────────────┐
│ docs/index.html          │ ──────▶ │ "Add website"│ ─────────▶ │ submit.yml         │
│ [ https://…    ] [Watch] │         │  issue form  │            │ adds to sites.json │
│ list of watched sites    │         └──────────────┘            │ closes the issue   │
└──────────────────────────┘                                     └─────────┬──────────┘
                                                                           │
                               every 5 min                                 ▼
                           ┌──────────────────────────────────────────────────────┐
                           │ check.yml → check.py                                 │
                           │ fetch every site → compare with snapshots/ → email   │
                           └──────────────────────────────────────────────────────┘
```

1. A visitor types a link on the page and clicks **Watch**. That opens a prefilled
   GitHub issue, and they click **Create**. (A static page can't safely hold a
   GitHub token, so the issue is how a submission reaches the repo.)
2. `submit.yml` checks the link (real domain, http/https, not a private address, not
   a duplicate), adds it to `docs/sites.json`, replies on the issue, closes it, and
   starts a check right away.
3. `check.yml` runs `check.py` every 5 minutes. Each page is reduced to its visible
   text and links and compared with the last snapshot in `snapshots/`.
4. Each run sends **one digest email** covering everything that happened:
   - **Now watching**: a new site was added (its first snapshot was taken)
   - **Changed**: the text and links that were added and removed
   - **Unreachable**: 3 checks in a row failed
   - **Back up**: a site that was unreachable responds again

The same ideas are used in `website-chase-checker` (text snapshot + diff email),
`Parking-checker` (state committed back to the repo), and `affiliate-2.0`
(adding items through a GitHub form).

## Setup (about 5 minutes)

### 1. Keep the repository public
Actions minutes are unlimited on public repos. A private repo gets 2,000
minutes/month, which a 5-minute schedule uses up in about a week.

### 2. Add the email secrets
**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|---|---|
| `SMTP_USERNAME` | The Gmail address that sends the alerts |
| `SMTP_PASSWORD` | A Gmail [App Password](https://myaccount.google.com/apppasswords) (not your normal password; requires 2-Step Verification) |
| `EMAIL_TO` | The address that receives alerts. Separate several with commas. Defaults to `SMTP_USERNAME`. |
| `SMTP_HOST` / `SMTP_PORT` | Optional, for a provider other than Gmail (defaults `smtp.gmail.com` / `465`) |

### 3. Turn on the public page
**Settings → Pages → Build and deployment**: Source **Deploy from a branch**,
Branch **main**, folder **/docs** → Save. A minute later the page is live at
`https://<your-username>.github.io/website-watcher/`.

### 4. Make sure Issues and Actions are enabled
Issues: **Settings → General → Features → Issues** is checked.
Actions: **Actions** tab → enable workflows if GitHub asks.

### 5. Try it
Submit a link on the page. Within about a minute the issue gets closed with a reply
and you get a **Now watching** email. To test the checker by hand:
**Actions → Check websites → Run workflow**.

## Managing the list

- **Remove a site**: open a new issue with the **Stop watching a website** form (only
  you and collaborators can use it), or delete the entry from `docs/sites.json`.
- **Limit the list**: set a repository *variable* (not a secret) `MAX_SITES`
  (default 100). Anyone can submit a link, so check the **Now watching** emails and
  remove anything you don't want.
## Check frequency

`*/5 * * * *` (every 5 minutes) is the most often GitHub allows for a scheduled
workflow. GitHub starts scheduled runs late when it's busy, so in practice the gap is
often 5–15 minutes. For exact timing you can trigger extra checks from an outside
scheduler (for example cron-job.org) using the `repository_dispatch` event with type
`check`, the same way `Parking-checker` does.

GitHub turns off scheduled workflows in repos with no commits for 60 days. The
checker commits a small heartbeat file every 45 days to prevent that.

## Noisy sites
Pages that show a live clock, a visitor counter, or rotating ads change on every
check and will email often. Remove those or watch a more specific page on the site.
Pages that build their content with JavaScript may look almost empty to the checker,
because it reads the HTML the server sends.

## Files

```
check.py                      checks every site, emails the digest (Python stdlib only)
manage_sites.py               validates a submitted link and edits docs/sites.json
docs/index.html               the public submission page (GitHub Pages)
docs/sites.json               the watch list
snapshots/                    last-seen text of each site + status.json
.github/workflows/check.yml   5-minute schedule
.github/workflows/submit.yml  handles submissions from issues
.github/ISSUE_TEMPLATE/       the "Watch a website" / "Stop watching" forms
```

## Run locally

```bash
DRY_RUN=1 python check.py          # prints the email instead of sending it
ACTION=add ISSUE_BODY=$'### Website URL\n\nexample.com' python manage_sites.py
```
