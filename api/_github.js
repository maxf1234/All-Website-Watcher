/**
 * Shared helpers for the API routes (files starting with _ are not routes).
 * The watch list lives in docs/sites.json in the GitHub repo; these helpers read
 * and write it through the GitHub API using the GH_TOKEN environment variable.
 */

const dns = require("dns").promises;
const net = require("net");
const crypto = require("crypto");

const REPO = process.env.GH_REPO || "maxf1234/All-Website-Watcher";
const BRANCH = process.env.GH_BRANCH || "main";
const SITES_PATH = "docs/sites.json";
const STATUS_PATH = "snapshots/status.json";

async function gh(path, options = {}) {
  if (!process.env.GH_TOKEN) throw new Error("GH_TOKEN is not set on the server");
  const res = await fetch(`https://api.github.com/repos/${REPO}${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${process.env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "all-website-watcher",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
    },
  });
  return res;
}

/** Reads a JSON file from the repo. Returns { data, sha }, or { data: fallback } if missing. */
async function readJson(path, fallback) {
  const res = await gh(`/contents/${path}?ref=${BRANCH}`);
  if (res.status === 404) return { data: fallback, sha: null };
  if (!res.ok) throw new Error(`GitHub read failed (${res.status})`);
  const file = await res.json();
  return { data: JSON.parse(Buffer.from(file.content, "base64").toString("utf8")), sha: file.sha };
}

/**
 * Applies change(sites) to the watch list and saves it. change returns
 * { sites, message } or throws a UserError. Retries if someone else saved
 * the file at the same moment.
 */
async function updateSites(change) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const { data, sha } = await readJson(SITES_PATH, []);
    const { sites, message } = change(data);
    const res = await gh(`/contents/${SITES_PATH}`, {
      method: "PUT",
      body: JSON.stringify({
        message: `sites: ${message}`,
        content: Buffer.from(JSON.stringify(sites, null, 2) + "\n").toString("base64"),
        sha,
        branch: BRANCH,
      }),
    });
    if (res.ok) return message;
    if (res.status !== 409 && res.status !== 422) throw new Error(`GitHub save failed (${res.status})`);
  }
  throw new Error("The list was busy, please try again.");
}

/** Starts the checker now instead of waiting for the next scheduled run. */
async function runCheckNow() {
  const res = await gh(`/actions/workflows/check.yml/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: BRANCH }),
  });
  return res.ok;
}

class UserError extends Error {}

function isPublicIp(ip) {
  if (net.isIPv4(ip)) {
    const [a, b] = ip.split(".").map(Number);
    return !(a === 0 || a === 10 || a === 127 || a >= 224 ||
      (a === 100 && b >= 64 && b <= 127) || (a === 169 && b === 254) ||
      (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) ||
      (a === 198 && (b === 18 || b === 19)));
  }
  const v6 = ip.toLowerCase();
  if (v6.startsWith("::ffff:")) return isPublicIp(v6.slice(7));
  return !(v6 === "::" || v6 === "::1" || v6.startsWith("fc") || v6.startsWith("fd") ||
    v6.startsWith("fe8") || v6.startsWith("fe9") || v6.startsWith("fea") ||
    v6.startsWith("feb") || v6.startsWith("ff"));
}

/** Returns a clean http(s) URL or throws a UserError explaining why not. */
async function normaliseUrl(raw) {
  raw = String(raw || "").trim();
  if (!raw) throw new UserError("Please enter a website link.");
  if (!/^[a-z]+:\/\//i.test(raw)) raw = "https://" + raw;
  if (raw.length > 500) throw new UserError("That link is too long.");
  let url;
  try { url = new URL(raw); } catch { throw new UserError("That doesn't look like a website link."); }
  if (!/^https?:$/.test(url.protocol) || !url.hostname.includes("."))
    throw new UserError("That doesn't look like a website link.");
  if (url.username || url.password) throw new UserError("Links with a username or password aren't accepted.");
  let addresses;
  try { addresses = await dns.lookup(url.hostname, { all: true }); }
  catch { throw new UserError(`The domain ${url.hostname} doesn't exist.`); }
  if (!addresses.length || !addresses.every((a) => isPublicIp(a.address)))
    throw new UserError(`${url.hostname} points to a private address.`);
  url.hash = "";
  return url.href;
}

/** Fetches the page once so the visitor sees straight away whether it works. */
async function probe(url) {
  try {
    const res = await fetch(url, {
      redirect: "follow",
      signal: AbortSignal.timeout(8000),
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        Accept: "text/html,application/xhtml+xml,*/*;q=0.8",
      },
    });
    const text = (await res.text()).slice(0, 200000);
    const title = (text.match(/<title[^>]*>([^<]*)<\/title>/i) || [])[1];
    return {
      ok: res.ok,
      status: res.status,
      title: title ? title.replace(/\s+/g, " ").trim().slice(0, 120) : null,
    };
  } catch (err) {
    return { ok: false, status: null, error: err.name === "TimeoutError" ? "timed out" : "could not connect" };
  }
}

function sameUrl(a, b) {
  return a.replace(/\/+$/, "").toLowerCase() === b.replace(/\/+$/, "").toLowerCase();
}

function checkPassword(given) {
  const expected = process.env.ADMIN_PASSWORD;
  if (!expected) throw new UserError("Removing sites is turned off (no ADMIN_PASSWORD is set).");
  const a = crypto.createHash("sha256").update(String(given || "")).digest();
  const b = crypto.createHash("sha256").update(expected).digest();
  if (!crypto.timingSafeEqual(a, b)) throw new UserError("Wrong password.");
}

async function readBody(req) {
  if (req.body && typeof req.body === "object") return req.body;
  try { return JSON.parse(req.body || "{}"); } catch { return {}; }
}

/** Wraps a handler: POST only, JSON in and out, friendly errors. */
function postHandler(fn) {
  return async (req, res) => {
    res.setHeader("Cache-Control", "no-store");
    if (req.method !== "POST") return res.status(405).json({ error: "Use POST." });
    try {
      res.status(200).json(await fn(await readBody(req)));
    } catch (err) {
      if (err instanceof UserError) return res.status(400).json({ error: err.message });
      console.error(err);
      res.status(500).json({ error: "Something went wrong on the server. Please try again." });
    }
  };
}

module.exports = {
  SITES_PATH, STATUS_PATH, readJson, updateSites, runCheckNow, UserError,
  normaliseUrl, probe, sameUrl, checkPassword, postHandler,
};
