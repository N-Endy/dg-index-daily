/* Fixed back-to-top control (shared layout) */
(function () {
  const btn = document.getElementById("back-to-top");
  if (!btn) return;

  const SHOW_AFTER_PX = 300;

  function syncVisibility() {
    const show = window.scrollY > SHOW_AFTER_PX;
    btn.hidden = !show;
    btn.classList.toggle("is-visible", show);
  }

  btn.addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  window.addEventListener("scroll", syncVisibility, { passive: true });
  syncVisibility();
})();
