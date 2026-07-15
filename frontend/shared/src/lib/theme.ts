/**
 * Theme manager — applies user's theme preference (light/dark/system) to the
 * document root and persists the choice in localStorage for instant load.
 *
 * The preference is also stored server-side via /me/preferences, but we keep
 * a local copy so the first paint matches the user's last choice.
 */

export type ThemePreference = "light" | "dark" | "system";

const STORAGE_KEY = "eaos:theme";

function resolveEffective(pref: ThemePreference): "light" | "dark" {
  if (pref === "system") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return pref;
}

function applyToDom(effective: "light" | "dark") {
  const root = document.documentElement;
  if (effective === "dark") {
    root.classList.add("dark");
    root.setAttribute("data-theme", "dark");
  } else {
    root.classList.remove("dark");
    root.setAttribute("data-theme", "light");
  }
}

/**
 * Apply a theme preference immediately and persist it locally.
 */
export function setTheme(pref: ThemePreference) {
  localStorage.setItem(STORAGE_KEY, pref);
  applyToDom(resolveEffective(pref));
}

/**
 * Read the locally stored theme preference (no server round-trip). Used on
 * app boot to avoid a flash of incorrect theme.
 */
export function getStoredTheme(): ThemePreference {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark" || stored === "system") return stored;
  return "system";
}

/**
 * Initialise theme on app boot — applies the stored preference or the
 * server-synced value. Should run before first paint (in main.tsx).
 */
export function initTheme() {
  applyToDom(resolveEffective(getStoredTheme()));
}

/**
 * Subscribe to system colour-scheme changes. Returns an unsubscribe function.
 * Only relevant when the user has selected "system".
 */
export function watchSystemTheme(onChange: (effective: "light" | "dark") => void): () => void {
  const mql = window.matchMedia("(prefers-color-scheme: dark)");
  const handler = (e: MediaQueryListEvent) => {
    if (getStoredTheme() === "system") {
      const effective = e.matches ? "dark" : "light";
      applyToDom(effective);
      onChange(effective);
    }
  };
  mql.addEventListener("change", handler);
  return () => mql.removeEventListener("change", handler);
}
