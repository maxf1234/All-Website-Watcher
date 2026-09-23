/**
 * POST /api/remove { url, password } — removes a site from the watch list.
 * Needs the ADMIN_PASSWORD set on the server.
 */

const { updateSites, UserError, sameUrl, checkPassword, postHandler } = require("./_github");

module.exports = postHandler(async ({ url, password }) => {
  checkPassword(password);
  url = String(url || "");
  await updateSites((sites) => {
    const kept = sites.filter((s) => !sameUrl(s.url, url));
    if (kept.length === sites.length) throw new UserError(`${url} isn't on the watch list.`);
    return { sites: kept, message: `remove ${url}` };
  });
  return { url };
});
