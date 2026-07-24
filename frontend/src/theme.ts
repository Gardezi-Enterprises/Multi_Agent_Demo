// Theme toggle persisted to localStorage; falls back to the OS preference.

export function initTheme() {
  try {
    const saved = localStorage.getItem("ta.theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
  } catch {
    /* private mode */
  }
}

export function toggleTheme() {
  const current =
    document.documentElement.getAttribute("data-theme") ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = current === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try {
    localStorage.setItem("ta.theme", next);
  } catch {
    /* ignore */
  }
}
