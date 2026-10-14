import { useEffect, useState } from "react";

type ThemeName = "white" | "g100";
const storageKey = "skid2.theme";

function preferredTheme(): ThemeName {
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved === "white" || saved === "g100") return saved;
  } catch {
    // Theme switching remains available when browser storage is disabled.
  }
  return typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "g100"
    : "white";
}

export function useTheme() {
  const [theme, setTheme] = useState<ThemeName>(preferredTheme);

  useEffect(() => {
    document.documentElement.style.colorScheme = theme === "g100" ? "dark" : "light";
  }, [theme]);

  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key === storageKey || event.key === null) setTheme(preferredTheme());
    };
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener("storage", sync);
    };
  }, []);

  function toggleTheme(dark: boolean) {
    const next = dark ? "g100" : "white";
    setTheme(next);
    try {
      localStorage.setItem(storageKey, next);
    } catch {
      // Keep the selected theme for this visit if storage is unavailable.
    }
  }

  return { theme, toggleTheme };
}
