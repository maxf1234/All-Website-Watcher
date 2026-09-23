# Website Watcher

A public page where anyone can submit a website link. GitHub Actions checks every
submitted site every 5 minutes and emails you whenever one changes. Everything runs on
free plans: GitHub Actions does the checking and a small Vercel site takes submissions.

## How it works

```
 public page + API (Vercel, free)                          GitHub Actions (free)
┌───────────────────────────────┐   saves docs/sites.json  ┌───────────────────────┐
│ docs/index.html               │ ───────────────────────▶ │ check.yml → check.py  │
│ [ https://…      ] [Watch]    │   starts a check now     │ every 5 minutes:      │
│ list of sites     [Remove]    │ ───────────────────────▶ │ fetch every site,     │
│ api/add · api/remove · sites  │                          │ compare, email digest │
└───────────────────────────────┘                          └───────────────────────┘
```

1. A visitor types a link and clicks **Watch**. `api/add` checks the link right away
   (real domain, public address, the page actually loads, not already on the list)
   and shows the page's title or a clear error on the page. No GitHub account needed.
2. If it's good, the link is saved to `docs/sites.json` in this repo and a check is
   started immediately, which takes the first snapshot and emails you **Now watching**.
3. `check.yml` runs `check.py` every 5 minutes. Each page is reduced to its visible
   text and links and compared with the last snapshot in `snapshots/`.
4. Each run sends **one digest email** covering everything that happened:
   - **Now watching**: a new site was added (its first snapshot was taken)
   - **Changed**: the text and links that were added and removed
   - **Unreachable**: 3 checks in a row failed
   - **Back up**: a site that was unreachable responds again
5. **Remove** next to a site asks for a password (`ADMIN_PASSWORD`), so only you can
   take sites off the list.

The website runs on Vercel because a page on its own can't safely hold the GitHub
key needed to save the list. Vercel keeps the key on the server.

The same ideas are used in `website-chase-checker` (text snapshot + diff email),
`Parking-checker` (state committed back to the repo), and `affiliate-2.0`
(Vercel API functions next to a GitHub-stored JSON file).

## Setup (about 10 minutes)

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

### 3. Create a GitHub key for the website
GitHub → your profile picture → **Settings → Developer settings → Personal access
tokens → Fine-grained tokens → Generate new token**:
- **Repository access**: Only select repositories → `All-Website-Watcher`
- **Permissions → Repository permissions**: **Contents: Read and write** and
  **Actions: Read and write**
- Expiration: the longest you're comfortable with (set a reminder to renew it)

Copy the token (starts with `github_pat_`).

### 4. Deploy the website on Vercel
1. [vercel.com/new](https://vercel.com/new) → import `All-Website-Watcher`.
   Leave the framework as **Other** and the build settings as they are.
2. Before clicking Deploy, open **Environment Variables** and add:

   | Name | Value |
   |---|---|
   | `GH_TOKEN` | the token from step 3 |
   | `ADMIN_PASSWORD` | a password you choose, needed to remove sites |
   | `MAX_SITES` | optional, how many sites can be watched (default 100) |

3. Deploy. Your public page is the `https://….vercel.app` address Vercel gives you.
   If you add or change a variable later, redeploy for it to take effect.

The website only redeploys when `api/`, `docs/index.html` or the config changes, so
the checker's snapshot commits don't use up Vercel's daily deploy limit.

### 5. Try it
Submit a link on the page. You should see the page title right away and get a
**Now watching** email within a minute or two. To run the checker by hand:
**Actions → Check websites → Run workflow**.

## Managing the list

- **Remove a site**: click **Remove** next to it and enter your `ADMIN_PASSWORD`. The
  page remembers the password until you close the tab.
- **Limit the list**: set `MAX_SITES` on Vercel (default 100). Anyone can submit a
  link, so keep an eye on the **Now watching** emails and remove anything you don't want.

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
docs/index.html               the public page
docs/sites.json               the watch list
api/add.js                    POST: check a link and add it
api/remove.js                 POST: remove a link (needs ADMIN_PASSWORD)
api/sites.js                  GET: the list with each site's status
api/_github.js                shared helpers: GitHub API, link validation
snapshots/                    last-seen text of each site + status.json
.github/workflows/check.yml   5-minute schedule
vercel.json                   serves docs/, skips redeploys for snapshot commits
```

## Run the checker locally

```bash
DRY_RUN=1 python check.py          # prints the email instead of sending it
```
