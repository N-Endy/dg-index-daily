/**
 * DataGaffer Tactical Terminal — Client Board Controller
 * Features: Instant client-side search, keyboard shortcuts, reduced motion handling.
 */
(function () {
  "use strict";

  // 1. Motion Preferences
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    document.documentElement.classList.add("reduce-motion");
  } else {
    document.documentElement.classList.add("js-ready");
  }

  // 2. Instant Search Filter
  const searchInput = document.getElementById("board-search-input");
  if (!searchInput) return;

  const dayBlocks = Array.from(document.querySelectorAll(".day-block"));
  const allStrips = Array.from(document.querySelectorAll(".fixture-strip"));

  // Pre-index text content for sub-millisecond filtering
  const stripData = allStrips.map((strip) => {
    const text = (strip.textContent || "").toLowerCase();
    const dayBlock = strip.closest(".day-block");
    return { el: strip, text: text, dayBlock: dayBlock };
  });

  function performFilter() {
    const q = (searchInput.value || "").trim().toLowerCase();
    if (!q) {
      // Show all
      stripData.forEach((item) => {
        item.el.hidden = false;
      });
      dayBlocks.forEach((block) => {
        block.hidden = false;
      });
      return;
    }

    const visibleDays = new Set();
    stripData.forEach((item) => {
      const match = item.text.includes(q);
      item.el.hidden = !match;
      if (match && item.dayBlock) {
        visibleDays.add(item.dayBlock);
      }
    });

    dayBlocks.forEach((block) => {
      block.hidden = !visibleDays.has(block);
    });
  }

  searchInput.addEventListener("input", performFilter);

  // Keyboard Shortcuts: "/" to focus, "Escape" to clear
  window.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== searchInput) {
      // Only capture if not typing in another input/select
      if (
        document.activeElement &&
        (document.activeElement.tagName === "INPUT" ||
          document.activeElement.tagName === "SELECT" ||
          document.activeElement.tagName === "TEXTAREA")
      ) {
        return;
      }
      e.preventDefault();
      searchInput.focus();
      searchInput.select();
    } else if (e.key === "Escape" && document.activeElement === searchInput) {
      if (searchInput.value) {
        searchInput.value = "";
        performFilter();
      } else {
        searchInput.blur();
      }
    }
  });
})();
