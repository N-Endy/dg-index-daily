/* Near-miss Flashscore score confirm + manual FT entry */
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
      "<h3 class=\"dg-score-confirm-title\">Possible Flashscore match</h3>" +
      '<p class="dg-score-confirm-fixture"></p>' +
      '<ul class="dg-score-confirm-list"></ul>' +
      '<div class="dg-score-manual-form" hidden>' +
      '<label>Home <input type="number" min="0" step="1" class="dg-score-manual-home" value="0"></label> ' +
      '<label>Away <input type="number" min="0" step="1" class="dg-score-manual-away" value="0"></label> ' +
      '<button type="button" class="dg-score-manual-save">Save FT</button>' +
      "</div>" +
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

  function openNearMissDialog(btn) {
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
    dlg.querySelector(".dg-score-confirm-title").textContent =
      "Possible Flashscore match";
    dlg.querySelector(".dg-score-confirm-fixture").textContent =
      home + " vs " + away;
    const list = dlg.querySelector(".dg-score-confirm-list");
    list.hidden = false;
    list.innerHTML = "";
    dlg.querySelector(".dg-score-manual-form").hidden = true;
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

  function openManualDialog(btn) {
    const fixtureId = btn.getAttribute("data-fixture-id");
    const home = btn.getAttribute("data-home") || "";
    const away = btn.getAttribute("data-away") || "";
    const dlg = ensureDialog();
    dlg.querySelector(".dg-score-confirm-title").textContent = "Enter final score";
    dlg.querySelector(".dg-score-confirm-fixture").textContent =
      home + " vs " + away;
    dlg.querySelector(".dg-score-confirm-list").hidden = true;
    dlg.querySelector(".dg-score-confirm-list").innerHTML = "";
    const form = dlg.querySelector(".dg-score-manual-form");
    form.hidden = false;
    form.querySelector(".dg-score-manual-home").value = "0";
    form.querySelector(".dg-score-manual-away").value = "0";
    const status = dlg.querySelector(".dg-score-confirm-status");
    status.hidden = true;
    status.textContent = "";
    const save = form.querySelector(".dg-score-manual-save");
    save.onclick = () => {
      const fthg = Number(form.querySelector(".dg-score-manual-home").value);
      const ftag = Number(form.querySelector(".dg-score-manual-away").value);
      submitManual(fixtureId, fthg, ftag, btn, status);
    };
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

  async function submitManual(fixtureId, fthg, ftag, triggerBtn, statusEl) {
    statusEl.hidden = false;
    statusEl.textContent = "Saving…";
    try {
      const resp = await fetch("/api/score/manual", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          fixture_id: Number(fixtureId),
          fthg: Number(fthg),
          ftag: Number(ftag),
        }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) {
        statusEl.textContent =
          data.error ||
          (resp.status === 403
            ? "Unlock required — open /score-link/unlock?token=…"
            : "Could not save");
        return;
      }
      applyFinal(triggerBtn, data.ft_score);
      closeDialog();
    } catch (err) {
      statusEl.textContent = "Network error";
    }
  }

  function applyFinal(triggerBtn, ftScore) {
    const meta = triggerBtn.closest(".strip-meta");
    if (!meta) return;
    const awaiting = meta.querySelector(".ft-awaiting");
    if (awaiting) awaiting.remove();
    const fid = triggerBtn.getAttribute("data-fixture-id");
    document
      .querySelectorAll(
        '.dg-score-near-miss[data-fixture-id="' +
          fid +
          '"], .dg-score-manual[data-fixture-id="' +
          fid +
          '"]'
      )
      .forEach((b) => b.remove());
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
    const near = e.target.closest(".dg-score-near-miss");
    if (near) {
      e.preventDefault();
      openNearMissDialog(near);
      return;
    }
    const manual = e.target.closest(".dg-score-manual");
    if (manual) {
      e.preventDefault();
      openManualDialog(manual);
    }
  });
})();
