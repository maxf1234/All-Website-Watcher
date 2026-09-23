/**
 * GET /api/sites — the watch list, with each site's latest status from the checker.
 */

const crypto = require("crypto");
const { SITES_PATH, STATUS_PATH, readJson } = require("./_github");

// Same id scheme as site_id() in check.py.
function siteId(url) {
  const host = new URL(url).host.replace(/[^A-Za-z0-9.-]+/g, "_").slice(0, 60);
  return `${host}-${crypto.createHash("sha1").update(url).digest("hex").slice(0, 10)}`;
}

module.exports = async (req, res) => {
  try {
    const [{ data: sites }, { data: status }] = await Promise.all([
      readJson(SITES_PATH, []),
      readJson(STATUS_PATH, {}),
    ]);
    res.setHeader("Cache-Control", "s-maxage=15, stale-while-revalidate=60");
    res.status(200).json(sites.map((s) => {
      const st = status[siteId(s.url)];
      return {
        url: s.url,
        added: s.added,
        state: !st ? "pending" : st.down ? "down" : "ok",
        lastChange: (st && st.last_change) || null,
      };
    }));
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: "Couldn't load the list." });
  }
};
