/*
 * Shared schedule editor for playlist items.
 *
 *   scheduleSummary(asset)            -> short text, e.g. "Mon–Fri · 09:00–17:00"
 *   openScheduleEditor(asset, onSave) -> shows a modal; onSave(fields) must return
 *                                        a Promise that rejects with an Error whose
 *                                        message is shown to the user.
 *
 * Fields: start_date, end_date (YYYY-MM-DD), play_days ("12345", Mon=1),
 *         play_time_from, play_time_to (HH:MM). null = no limit.
 */
(function () {
  const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  function esc(str) {
    return String(str)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function fmtDate(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }

  function daysSummary(days) {
    if (!days) return null;
    if (days === "12345") return "Mon–Fri";
    if (days === "67") return "Sat–Sun";
    return days.split("").map(d => DAY_NAMES[+d - 1]).join(", ");
  }

  window.scheduleSummary = function (a) {
    const parts = [];
    const days = daysSummary(a.play_days);
    if (days) parts.push(days);
    if (a.play_time_from && a.play_time_to) parts.push(`${a.play_time_from}–${a.play_time_to}`);
    if (a.start_date && a.end_date) parts.push(`${fmtDate(a.start_date)} – ${fmtDate(a.end_date)}`);
    else if (a.start_date) parts.push(`from ${fmtDate(a.start_date)}`);
    else if (a.end_date) parts.push(`until ${fmtDate(a.end_date)}`);
    return parts.join(" · ");
  };

  let modal = null;

  function buildModal() {
    modal = document.createElement("div");
    modal.className = "fixed inset-0 z-40 hidden items-center justify-center bg-black/40 px-4";
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.innerHTML = `
      <div class="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
        <div class="flex items-start justify-between mb-1">
          <h3 class="text-lg font-semibold text-gray-800">Schedule</h3>
          <button type="button" data-act="close" class="text-gray-400 hover:text-gray-600 text-xl leading-none" aria-label="Close">&times;</button>
        </div>
        <p id="schedName" class="text-sm text-gray-500 mb-5 truncate"></p>

        <form id="schedForm" class="space-y-5">
          <fieldset>
            <legend class="text-sm font-medium text-gray-700 mb-2">Dates <span class="text-gray-400 font-normal">(leave blank for no limit)</span></legend>
            <div class="grid grid-cols-2 gap-3">
              <label class="text-xs text-gray-500">Start
                <input type="date" name="start_date" class="mt-1 w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-400" />
              </label>
              <label class="text-xs text-gray-500">End
                <input type="date" name="end_date" class="mt-1 w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-400" />
              </label>
            </div>
          </fieldset>

          <fieldset>
            <legend class="text-sm font-medium text-gray-700 mb-2">Days</legend>
            <div class="flex flex-wrap gap-1.5" id="schedDays">
              ${DAY_NAMES.map((n, i) => `
                <label class="cursor-pointer">
                  <input type="checkbox" value="${i + 1}" class="peer sr-only" />
                  <span class="inline-block w-11 text-center text-xs font-medium py-1.5 rounded-lg border border-gray-300 text-gray-600
                               peer-checked:bg-indigo-600 peer-checked:border-indigo-600 peer-checked:text-white
                               peer-focus-visible:ring-2 peer-focus-visible:ring-indigo-400">${n}</span>
                </label>`).join("")}
            </div>
            <div class="flex gap-3 mt-2 text-xs">
              <button type="button" data-days="1234567" class="text-indigo-600 hover:underline">Every day</button>
              <button type="button" data-days="12345" class="text-indigo-600 hover:underline">Weekdays</button>
              <button type="button" data-days="67" class="text-indigo-600 hover:underline">Weekends</button>
            </div>
          </fieldset>

          <fieldset>
            <legend class="text-sm font-medium text-gray-700 mb-2">Time of day <span class="text-gray-400 font-normal">(blank = all day)</span></legend>
            <div class="grid grid-cols-2 gap-3">
              <label class="text-xs text-gray-500">From
                <input type="time" name="play_time_from" class="mt-1 w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-400" />
              </label>
              <label class="text-xs text-gray-500">Until
                <input type="time" name="play_time_to" class="mt-1 w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-400" />
              </label>
            </div>
            <p class="text-xs text-gray-400 mt-1.5">An end time earlier than the start runs past midnight (e.g. 22:00 until 02:00).</p>
          </fieldset>

          <p id="schedError" class="hidden text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2"></p>

          <div class="flex items-center justify-between pt-1">
            <button type="button" data-act="clear" class="text-sm text-gray-500 hover:text-red-600">Clear schedule</button>
            <div class="flex gap-2">
              <button type="button" data-act="close" class="text-sm px-4 py-2 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50">Cancel</button>
              <button type="submit" class="text-sm px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white font-medium">Save</button>
            </div>
          </div>
        </form>
      </div>`;
    document.body.appendChild(modal);

    modal.addEventListener("click", e => {
      if (e.target === modal || e.target.closest("[data-act=close]")) closeModal();
      const preset = e.target.closest("[data-days]");
      if (preset) setDays(preset.dataset.days);
      if (e.target.closest("[data-act=clear]")) {
        modal.querySelector("#schedForm").reset();
        setDays("1234567");
      }
    });
    document.addEventListener("keydown", e => {
      if (e.key === "Escape" && !modal.classList.contains("hidden")) closeModal();
    });
  }

  function setDays(days) {
    modal.querySelectorAll("#schedDays input").forEach(cb => { cb.checked = days.includes(cb.value); });
  }

  function closeModal() {
    modal.classList.add("hidden");
    modal.classList.remove("flex");
  }

  window.openScheduleEditor = function (asset, onSave) {
    if (!modal) buildModal();
    const form = modal.querySelector("#schedForm");
    const err  = modal.querySelector("#schedError");
    modal.querySelector("#schedName").textContent = asset.name;
    err.classList.add("hidden");

    form.start_date.value     = asset.start_date || "";
    form.end_date.value       = asset.end_date || "";
    form.play_time_from.value = asset.play_time_from || "";
    form.play_time_to.value   = asset.play_time_to || "";
    setDays(asset.play_days || "1234567");

    form.onsubmit = async e => {
      e.preventDefault();
      const days = [...modal.querySelectorAll("#schedDays input:checked")].map(cb => cb.value).join("");
      if (!days) {
        err.textContent = "Pick at least one day.";
        err.classList.remove("hidden");
        return;
      }
      const fields = {
        start_date:     form.start_date.value || null,
        end_date:       form.end_date.value || null,
        play_days:      days === "1234567" ? null : days,
        play_time_from: form.play_time_from.value || null,
        play_time_to:   form.play_time_to.value || null,
      };
      try {
        await onSave(fields);
        closeModal();
      } catch (ex) {
        err.textContent = ex.message || "Could not save schedule.";
        err.classList.remove("hidden");
      }
    };

    modal.classList.remove("hidden");
    modal.classList.add("flex");
    form.start_date.focus();
  };
})();
