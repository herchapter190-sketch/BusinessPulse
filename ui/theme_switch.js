// BusinessPulse light/dark switch (sidebar). Rendered by ui/components.py theme_switch().
//
// Streamlit 1.67 has no API to set the theme from code. Its theme is "follow the
// system" (Custom Theme Auto), and Streamlit re-reads
// matchMedia("(prefers-color-scheme: dark)") whenever the page fires "afterprint".
// So the switch answers that one media query with the viewer's choice and fires
// "afterprint": Streamlit then applies its own light or dark theme to everything
// (widgets, tables, charts, sidebar). No reload, so uploaded data and the session
// stay intact. tests/test_design_system.py checks the hooks still exist in the
// installed Streamlit.
(() => {
  const QUERY = "(prefers-color-scheme: dark)";
  const STORE = "bp-theme";

  if (!window.__bpMatchMedia) {
    window.__bpMatchMedia = window.matchMedia.bind(window);
    window.matchMedia = (query) => {
      const real = window.__bpMatchMedia(query);
      if (query !== QUERY || !window.__bpTheme) return real;
      return {   // same object shape, the viewer's choice as the answer
        matches: window.__bpTheme === "dark", media: real.media, onchange: null,
        addEventListener: real.addEventListener.bind(real), removeEventListener: real.removeEventListener.bind(real),
        addListener: real.addListener.bind(real), removeListener: real.removeListener.bind(real),
        dispatchEvent: real.dispatchEvent.bind(real),
      };
    };
  }

  // With no choice made yet, Streamlit follows the system setting, so the switch
  // shows that (the app's CSS color-scheme reads "normal" in both themes).
  const current = () => (window.__bpMatchMedia(QUERY).matches ? "dark" : "light");

  const mark = (mode) => {
    document.querySelectorAll(".bp-theme-switch button").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
    });
  };

  const apply = (mode, remember) => {
    window.__bpTheme = mode;
    window.dispatchEvent(new Event("afterprint"));   // Streamlit re-reads the colour scheme
    if (remember) {
      try { localStorage.setItem(STORE, mode); } catch (error) { /* storage blocked: still switches */ }
    }
    mark(mode);
  };

  document.querySelectorAll(".bp-theme-switch button").forEach((button) => {
    if (button.dataset.bound) return;
    button.dataset.bound = "1";
    button.addEventListener("click", () => apply(button.dataset.mode, true));
  });

  // The system setting can change while the app is open; with no choice made,
  // Streamlit follows it, so the switch does too (listener added only once).
  if (!window.__bpSystemListener) {
    window.__bpSystemListener = true;
    window.__bpMatchMedia(QUERY).addEventListener("change", () => {
      if (!window.__bpTheme) mark(current());
    });
  }

  let saved = null;
  try { saved = localStorage.getItem(STORE); } catch (error) { saved = null; }
  if ((saved === "light" || saved === "dark") && saved !== window.__bpTheme) {
    apply(saved, false);   // a reload or new tab: restore the viewer's last choice
  } else {
    mark(window.__bpTheme || current());
  }
})();
