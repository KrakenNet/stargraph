// api-base.jsx — pluggable harbor server target.
//
// Resolution order:
//   1. URL query ?server=http(s)://host:port  (persists to localStorage)
//   2. localStorage("cve_rem.api_base")
//   3. "" (relative to current origin — default)
//
// Strip via ?server= (empty value) or call window.clearApiBase().

(function () {
  const KEY = "cve_rem.api_base";

  function readQuery() {
    try {
      const sp = new URLSearchParams(window.location.search);
      if (!sp.has("server")) return null;
      const raw = (sp.get("server") || "").trim();
      return raw; // "" intentionally clears
    } catch (_e) {
      return null;
    }
  }

  function readStorage() {
    try { return (localStorage.getItem(KEY) || "").trim(); }
    catch (_e) { return ""; }
  }

  function writeStorage(v) {
    try {
      if (v) localStorage.setItem(KEY, v);
      else localStorage.removeItem(KEY);
    } catch (_e) {}
  }

  // Hydrate from URL once at boot.
  const q = readQuery();
  if (q !== null) writeStorage(q);

  function getApiBase() {
    let v = readStorage();
    if (!v) return "";
    return v.replace(/\/+$/, "");
  }

  function apiUrl(path) {
    const base = getApiBase();
    if (!path) return base || "";
    if (/^https?:\/\//i.test(path)) return path;
    if (!base) return path;
    return base + (path.startsWith("/") ? path : "/" + path);
  }

  function wsUrl(path) {
    const base = getApiBase();
    if (base) {
      const proto = base.startsWith("https://") ? "wss://" : "ws://";
      const hostport = base.replace(/^https?:\/\//, "");
      return proto + hostport + (path.startsWith("/") ? path : "/" + path);
    }
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}${path}`;
  }

  function setApiBase(v) {
    writeStorage((v || "").trim());
  }

  function clearApiBase() { writeStorage(""); }

  window.getApiBase = getApiBase;
  window.apiUrl = apiUrl;
  window.wsUrl = wsUrl;
  window.setApiBase = setApiBase;
  window.clearApiBase = clearApiBase;
})();
