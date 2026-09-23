/**
 * POST /api/add { url } — checks the link right away, adds it to the watch
 * list, and starts the checker so the first snapshot is taken immediately.
 */

const {
  updateSites, runCheckNow, UserError, normaliseUrl, probe, sameUrl, postHandler,
} = require("./_github");

const MAX_SITES = Number(process.env.MAX_SITES) || 100;

module.exports = postHandler(async ({ url: raw }) => {
  const url = await normaliseUrl(raw);
  const page = await probe(url);
  if (!page.ok) {
    throw new UserError(page.status
      ? `${url} answered with an error (HTTP ${page.status}), so it wasn't added.`
      : `${url} ${page.error}, so it wasn't added.`);
  }
  await updateSites((sites) => {
    if (sites.some((s) => sameUrl(s.url, url))) throw new UserError(`${url} is already being watched.`);
    if (sites.length >= MAX_SITES) throw new UserError(`The watch list is full (${MAX_SITES} sites).`);
    const added = new Date().toISOString().slice(0, 10);
    return { sites: [...sites, { url, added }], message: `add ${url}` };
  });
  const checking = await runCheckNow();
  return { url, title: page.title, checking };
});
