/* Near-miss Flashscore score confirm (MatchPredictor-style !) */
(function () {
  const dialogId = "dg-score-confirm-dialog";

  function ensureDialog() {
    let el = document.getElementById(dialogId);
    if (el) return el;
    el = document.createElement("div");
    el.id = dialogId;
    el.className = "dg-score-confirm-dialog";
    el.hidden = true;
    el.innerHTML =
      '<div class="dg-score-confirm-panel" role="dialog" aria-modal="true">' +
      '<button type="button" class="dg-score-confirm-close" aria-label="Close">&times;</button>' +
      "<h3>Possible Flashscore match</h3>" +
      '<p class="dg-score-confirm-fixture"></p>' +
      '<ul class="dg-score-confirm-list"></ul>' +
      '<p class="dg-score-confirm-status" hidden></p>' +
      "</div>";
    document.body.appendChild(el);
    el.querySelector(".dg-score-confirm-close").addEventListener("click", () => closeDialog());
    el.addEventListener("click", (e) => {
      if (e.target === el) closeDialog();
    });
    return el;
  }

  function closeDialog() {
    const el = document.getElementById(dialogId);
    if (el) el.hidden = true;
  }

  function openDialog(btn) {
    const fixtureId = btn.getAttribute("data-fixture-id");
    const home = btn.getAttribute("data-home") || "";
    const away = btn.getAttribute("data-away") || "";
    let candidates = [];
    try {
      candidates = JSON.parse(btn.getAttribute("data-candidates") || "[]");
    } catch (_) {
      candidates = [];
    }
    const dlg = ensureDialog();
    dlg.querySelector(".dg-score-confirm-fixture").textContent =
      home + " vs " + away;
    const list = dlg.querySelector(".dg-score-confirm-list");
    list.innerHTML = "";
    const status = dlg.querySelector(".dg-score-confirm-status");
    status.hidden = true;
    status.textContent = "";

    candidates.forEach((c) => {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.className = "dg-score-confirm-pick";
      b.textContent =
        (c.home || "?") +
        " vs " +
        (c.away || "?") +
        "  " +
        (c.score || "") +
        (c.league ? " · " + c.league : "") +
        (c.reason ? " — " + c.reason : "");
      b.addEventListener("click", () => confirmPick(fixtureId, c.id, btn, status));
      li.appendChild(b);
      list.appendChild(li);
    });
    dlg.hidden = false;
  }

  async function confirmPick(fixtureId, rowId, bangBtn, statusEl) {
    statusEl.hidden = false;
    statusEl.textContent = "Saving…";
    try {
      const resp = await fetch("/api/score-link/confirm", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          fixture_id: Number(fixtureId),
          flashscore_row_id: Number(rowId),
        }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) {
        statusEl.textContent =
          data.error ||
          (resp.status === 403
            ? "Unlock required — open /score-link/unlock?token=…"
            : "Could not confirm");
        return;
      }
      applyFinal(bangBtn, data.ft_score);
      closeDialog();
    } catch (err) {
      statusEl.textContent = "Network error";
    }
  }

  function applyFinal(bangBtn, ftScore) {
    const meta = bangBtn.closest(".strip-meta");
    if (!meta) return;
    const awaiting = meta.querySelector(".ft-awaiting");
    if (awaiting) awaiting.remove();
    document.querySelectorAll(
      '.dg-score-near-miss[data-fixture-id="' + bangBtn.getAttribute("data-fixture-id") + '"]'
    ).forEach((b) => b.remove());
    let final = meta.querySelector(".ft-score");
    if (!final) {
      final = document.createElement("span");
      final.className = "ft-score";
      final.title = "Full-time score";
      meta.insertBefore(final, meta.querySelector(".agree") || null);
    }
    final.textContent = "Final " + ftScore;
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".dg-score-near-miss");
    if (btn) {
      e.preventDefault();
      openDialog(btn);
    }
  });
})();
