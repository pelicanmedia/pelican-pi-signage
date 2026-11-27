/*
 * Shared device panels for admin pages (keep the client and controller
 * copies identical).
 *
 *   renderHealth(el, info)                    -> health grid + warnings
 *   renderScreenSchedule(el, settings, onSave) -> screen on/off schedule form;
 *       onSave({display_on, display_off, display_days, display_cec}) returns a
 *       Promise that rejects with an Error whose message is shown.
 *   healthWarningBadge(info)                  -> small "⚠ n" badge HTML or ""
 */
(function () {
  const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  function esc(str) {
    return String(str)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function fmtBytes(n) {
    if (n == null) return "—";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
  }

  function fmtUptime(s) {
    if (s == null) return "—";
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    return `${m}m`;
  }

  function bar(percent) {
    if (percent == null) return "";
    const col = percent >= 90 ? "bg-red-500" : percent >= 75 ? "bg-amber-500" : "bg-indigo-500";
    return `<div class="h-1.5 bg-gray-200 rounded-full mt-1 overflow-hidden"><div class="h-1.5 ${col}" style="width:${percent}%"></div></div>`;
  }

  window.healthWarningBadge = function (info) {
    const n = info && info.warnings ? info.warnings.length : 0;
    if (!n) return "";
    return `<span class="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-amber-100 text-amber-800"
      title="${esc(info.warnings.join("\n"))}">⚠ ${n}</span>`;
  };

  window.renderHealth = function (el, info) {
    if (!info) {
      el.innerHTML = `<p class="text-sm text-gray-500">No health information yet.</p>`;
      return;
    }
    const temp = info.cpu_temp == null ? "—" : `${Math.round(info.cpu_temp)}°C`;
    const tempCls = info.cpu_temp >= 80 ? "text-red-600" : info.cpu_temp >= 70 ? "text-amber-600" : "text-gray-800";
    const mem = info.memory, disk = info.disk;
    const kioskCls = info.kiosk === "active" ? "text-green-700" : "text-red-600";
    const kioskTxt = info.kiosk === "active" ? "Running" : info.kiosk ? `Not running (${esc(info.kiosk)})` : "—";
    const tile = (label, value, extra = "", wide = false) => `
      <div class="bg-gray-50 rounded-lg px-3 py-2 ${wide ? "col-span-2" : ""}">
        <div class="text-xs text-gray-500">${label}</div>
        <div class="text-sm font-medium text-gray-800 ${wide ? "break-words" : "truncate"}">${value}</div>${extra}
      </div>`;

    el.innerHTML = `
      ${(info.warnings || []).map(w => `
        <div class="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-2">⚠ ${esc(w)}</div>`).join("")}
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
        ${tile("Temperature", `<span class="${tempCls}">${temp}</span>`)}
        ${tile("Uptime", fmtUptime(info.uptime))}
        ${tile("Memory", mem ? `${fmtBytes(mem.used)} of ${fmtBytes(mem.total)}` : "—", bar(mem && mem.percent))}
        ${tile("Disk", disk ? `${fmtBytes(disk.free)} free` : "—", bar(disk && disk.percent))}
        ${tile("Display", `<span class="${kioskCls}">${kioskTxt}</span>`)}
        ${tile("Screen", info.screen_off ? "Off (schedule)" : "On")}
        ${tile("IP addresses", esc((info.ips || []).join(", ") || "—"), "", true)}
      </div>
      <p class="text-xs text-gray-400 mt-2">
        ${esc([info.model, info.os, info.hostname,
               info.load == null ? null : `load ${info.load}`].filter(Boolean).join(" · "))}
        ${info.now_showing ? ` · Showing: ${esc(info.now_showing)}` : ""}
      </p>`;
  };

  window.renderScreenSchedule = function (el, settings, onSave) {
    const s = settings || {};
    const days = s.display_days || "1234567";
    el.innerHTML = `
      <form class="space-y-3">
        <p class="text-sm text-gray-600">Turn the screen off outside opening hours. Leave the times blank to keep it on all day.</p>
        <div class="grid grid-cols-2 gap-3 max-w-xs">
          <label class="text-xs text-gray-500">On at
            <input type="time" name="on" value="${esc(s.display_on || "")}" class="mt-1 w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-400" />
          </label>
          <label class="text-xs text-gray-500">Off at
            <input type="time" name="off" value="${esc(s.display_off || "")}" class="mt-1 w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-400" />
          </label>
        </div>
        <div>
          <div class="text-xs text-gray-500 mb-1">On these days (off all day on the others)</div>
          <div class="flex flex-wrap gap-1.5" data-role="days">
            ${DAY_NAMES.map((n, i) => `
              <label class="cursor-pointer">
                <input type="checkbox" value="${i + 1}" class="peer sr-only" ${days.includes(String(i + 1)) ? "checked" : ""} />
                <span class="inline-block w-11 text-center text-xs font-medium py-1.5 rounded-lg border border-gray-300 text-gray-600
                             peer-checked:bg-indigo-600 peer-checked:border-indigo-600 peer-checked:text-white
                             peer-focus-visible:ring-2 peer-focus-visible:ring-indigo-400">${n}</span>
              </label>`).join("")}
          </div>
        </div>
        <label class="flex items-start gap-2 text-sm text-gray-700">
          <input type="checkbox" name="cec" class="mt-0.5" ${s.display_cec === "1" ? "checked" : ""} />
          <span>Also switch the TV to standby (HDMI-CEC)
            <span class="block text-xs text-gray-400">Many TVs then turn fully off rather than showing "no signal". Not all TVs support it.</span></span>
        </label>
        <p data-role="error" class="hidden text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2"></p>
        <div class="flex gap-2">
          <button type="submit" class="text-sm px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white font-medium">Save schedule</button>
          <button type="button" data-act="clear" class="text-sm px-4 py-2 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50">Always on</button>
        </div>
      </form>`;

    const form = el.querySelector("form");
    const err = el.querySelector("[data-role=error]");
    const save = async fields => {
      err.classList.add("hidden");
      try {
        await onSave(fields);
      } catch (ex) {
        err.textContent = ex.message || "Could not save.";
        err.classList.remove("hidden");
      }
    };

    form.onsubmit = e => {
      e.preventDefault();
      const picked = [...el.querySelectorAll("[data-role=days] input:checked")].map(c => c.value).join("");
      if (!picked) {
        err.textContent = "Pick at least one day.";
        err.classList.remove("hidden");
        return;
      }
      save({
        display_on:   form.on.value || "",
        display_off:  form.off.value || "",
        display_days: picked === "1234567" ? "" : picked,
        display_cec:  form.cec.checked ? "1" : "0",
      });
    };
    el.querySelector("[data-act=clear]").addEventListener("click", () => {
      form.on.value = "";
      form.off.value = "";
      el.querySelectorAll("[data-role=days] input").forEach(c => { c.checked = true; });
      save({ display_on: "", display_off: "", display_days: "", display_cec: form.cec.checked ? "1" : "0" });
    });
  };
})();
