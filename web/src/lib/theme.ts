import { useEffect, useState } from "react";

// Light/dark theme with a crossfade on toggle. The `.theming` class arms a
// global transition BEFORE the token values flip, or both land in one style
// recalc and the swap snaps instead of fading. It is removed right after so
// hover transitions are never affected.

const STORAGE_KEY = "origo-client-theme";

export type ThemeName = "dark" | "light";

function readStored(): ThemeName {
  try {
    const saved = localStorage.getItem(STORAGE_KEY) ?? localStorage.getItem("theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch { /* storage unavailable */ }
  return "dark";
}

function apply(theme: ThemeName) {
  document.documentElement.setAttribute("data-theme", theme === "light" ? "light" : "");
  // Kept in sync for any remaining Tailwind `dark:` styles.
  document.documentElement.classList.toggle("dark", theme === "dark");
  try { localStorage.setItem(STORAGE_KEY, theme); } catch { /* storage unavailable */ }
}

// Apply the persisted theme as early as the module loads so there is no flash.
apply(readStored());

let crossfadeTimer: ReturnType<typeof setTimeout> | undefined;

export function useTheme() {
  const [theme, setTheme] = useState<ThemeName>(readStored);

  useEffect(() => { apply(theme); }, [theme]);

  function toggle() {
    const root = document.documentElement;
    const next: ThemeName = theme === "dark" ? "light" : "dark";
    if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
      root.classList.add("theming");
      // flush the style change so the transition exists before tokens flip
      void root.offsetWidth;
      clearTimeout(crossfadeTimer);
      crossfadeTimer = setTimeout(() => root.classList.remove("theming"), 520);
    }
    // Flip the tokens synchronously, in the same frame the transition was
    // armed. Doing it in the effect (after paint) lands a frame late and the
    // cleanup timer then clips the fade's tail — the "small glitch".
    apply(next);
    setTheme(next);
  }

  return { theme, dark: theme === "dark", toggle };
}
