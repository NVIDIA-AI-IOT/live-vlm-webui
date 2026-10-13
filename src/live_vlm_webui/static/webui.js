/* SPDX-License-Identifier: Apache-2.0 */
"use strict";

(() => {
  function initializeTheme() {
    const toggle = document.getElementById("themeToggle");
    const icon = document.getElementById("themeIcon");
    const text = document.getElementById("themeText");
    if (!toggle || !icon || !text) return;

    const preference = window.matchMedia("(prefers-color-scheme: light)");
    const labels = { auto: "Auto", light: "Light", dark: "Dark" };
    const paths = {
      auto: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8m-4-4v4"/>',
      light: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42"/>',
      dark: '<path d="M20.99 12.96A9 9 0 0 1 11.04 3a9 9 0 1 0 9.95 9.96Z"/>',
    };

    function savedPreference() {
      try {
        const value = localStorage.getItem("theme");
        return value === "light" || value === "dark" ? value : "auto";
      } catch {
        return "auto";
      }
    }

    let theme = savedPreference();

    function applyTheme() {
      const light = theme === "light" || (theme === "auto" && preference.matches);
      const resolvedTheme = light ? "light" : "dark";
      document.body.classList.toggle("light-theme", light);
      document.body.style.colorScheme = resolvedTheme;
      icon.innerHTML = `<svg class="lucide" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[theme]}</svg>`;
      text.textContent = labels[theme];
      toggle.setAttribute("aria-label", `Theme: ${labels[theme]}. Change theme`);
      window.dispatchEvent(new CustomEvent("webui:themechange", {
        detail: { theme, resolvedTheme },
      }));
    }

    toggle.addEventListener("click", () => {
      theme = { auto: "light", light: "dark", dark: "auto" }[theme];
      try {
        if (theme === "auto") localStorage.removeItem("theme");
        else localStorage.setItem("theme", theme);
      } catch {
        // Theme switching still works when the browser disallows persistent storage.
      }
      applyTheme();
    });

    preference.addEventListener("change", () => {
      if (theme === "auto") applyTheme();
    });
    window.addEventListener("storage", (event) => {
      if (event.key === "theme" || event.key === null) {
        theme = savedPreference();
        applyTheme();
      }
    });
    applyTheme();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeTheme, { once: true });
  } else {
    initializeTheme();
  }
})();
