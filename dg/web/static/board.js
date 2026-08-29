/**
 * Minimal board motion bootstrap.
 * Animations live in CSS; this only marks the document as JS-ready
 * and respects prefers-reduced-motion (CSS already disables animations).
 */
(function () {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    document.documentElement.classList.add("reduce-motion");
    return;
  }
  document.documentElement.classList.add("js-ready");
})();
