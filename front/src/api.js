// Client API MarketLab — le proxy Vite redirige /api vers localhost:8600.

async function requete(url, options) {
  const resp = await fetch(url, options);
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail ?? detail; } catch { /* texte brut */ }
    throw new Error(detail);
  }
  return resp.json();
}

export const getUnivers = () => requete("/api/univers");
export const getOhlcv = (symbole, jours = 365) =>
  requete(`/api/ohlcv/${symbole}?lookback_days=${jours}`);
export const getSignaux = (symbole) => requete(`/api/signaux/${symbole}`);
export const getScreener = (univers) =>
  requete("/api/screener?" + univers.map((u) => `univers=${encodeURIComponent(u)}`).join("&"));
export const getMacro = () => requete("/api/macro");
export const getNews = (symbole) => requete(`/api/news/${symbole}`);
export const postMl = (symbole, params) =>
  requete(`/api/ml/${symbole}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
export const getPaper = () => requete("/api/paper");
export const getOrdres = () => requete("/api/ordres");
export const postOrdres = (chemin, corps) =>
  requete(`/api/ordres/${chemin}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(corps ?? {}),
  });
export const postPaper = (chemin, corps) =>
  requete(`/api/paper/${chemin}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(corps),
  });
