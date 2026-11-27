/*
 * Shared content helpers for playlist admin pages (keep the client and
 * controller copies identical).
 *
 *   contentSummary(asset)           -> short text, e.g. "Weather slide · Leeds"
 *   hasAutoDuration(asset)          -> true when the video's own length is used
 *   openSlideEditor(kind, onSave)   -> modal for a built-in slide ("clock",
 *                                      "weather" or "text"); onSave({name, src,
 *                                      duration}) returns a Promise
 *   openRefreshEditor(asset, onSave)-> modal for a web page's auto-refresh;
 *                                      onSave({options: {refresh_seconds}})
 *
 * Built-in slides are ordinary web-page items whose address is
 * /widgets/<kind>?..., served by each Pi.
 * Set window.SLIDE_PREVIEW = true on pages that can open /widgets/ (the Pi).
 */
(function () {
  const VIDEO_URL_RE  = /\.(mp4|webm|mov|m4v)(\?.*)?$/i;
  const STREAM_URL_RE = /\.m3u8(\?.*)?$/i;
  const INPUT = "mt-1 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-400";

  function esc(str) {
    return String(str)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function slideInfo(src) {
    const m = /^\/widgets\/(clock|weather|text)(\?.*)?$/.exec(src || "");
    if (!m) return null;
    return { kind: m[1], params: new URLSearchParams(m[2] || "") };
  }

  function fmtSeconds(s) {
    if (s % 3600 === 0) return `${s / 3600} hour${s === 3600 ? "" : "s"}`;
    if (s % 60 === 0)   return `${s / 60} min`;
    return `${s}s`;
  }

  window.contentSummary = function (a) {
    const opts = a.options || {};
    const slide = slideInfo(a.src);
    if (slide) {
      if (slide.kind === "clock") {
        const label = slide.params.get("label") || slide.params.get("tz");
        return "Clock slide" + (label ? ` · ${label}` : "");
      }
      if (slide.kind === "weather") {
        const place = slide.params.get("place");
        return "Weather slide" + (place ? ` · ${place}` : "");
      }
      return "Text slide";
    }
    if (a.type === "url" && STREAM_URL_RE.test(a.src)) return "Live stream (plays for its set duration)";
    const parts = [];
    if (opts.source_url) parts.push("Downloaded from the web");
    if (a.type === "url" && opts.refresh_seconds) parts.push(`Refreshes every ${fmtSeconds(opts.refresh_seconds)}`);
    return parts.join(" · ");
  };

  window.hasAutoDuration = function (a) {
    return a.type === "video" || (a.type === "url" && VIDEO_URL_RE.test(a.src || ""));
  };

  window.isRefreshable = function (a) {
    return a.type === "url" && !slideInfo(a.src) &&
      !VIDEO_URL_RE.test(a.src || "") && !STREAM_URL_RE.test(a.src || "");
  };

  // --- Modal plumbing ------------------------------------------------------
  let modal = null;

  function ensureModal() {
    if (modal) return modal;
    modal = document.createElement("div");
    modal.className = "fixed inset-0 z-40 hidden items-center justify-center bg-black/40 px-4";
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.innerHTML = `
      <div class="bg-white rounded-xl shadow-xl w-full max-w-md p-6 max-h-[92vh] overflow-y-auto">
        <div class="flex items-start justify-between mb-4">
          <h3 data-role="title" class="text-lg font-semibold text-gray-800"></h3>
          <button type="button" data-act="close" class="text-gray-400 hover:text-gray-600 text-xl leading-none" aria-label="Close">&times;</button>
        </div>
        <form data-role="form" class="space-y-4"></form>
      </div>`;
    document.body.appendChild(modal);
    modal.addEventListener("click", e => {
      if (e.target === modal || e.target.closest("[data-act=close]")) closeModal();
    });
    document.addEventListener("keydown", e => {
      if (e.key === "Escape" && !modal.classList.contains("hidden")) closeModal();
    });
    return modal;
  }

  function closeModal() {
    modal.classList.add("hidden");
    modal.classList.remove("flex");
  }

  function openModal(title, bodyHtml, submitLabel) {
    ensureModal();
    modal.querySelector("[data-role=title]").textContent = title;
    const form = modal.querySelector("[data-role=form]");
    form.innerHTML = `
      ${bodyHtml}
      <p data-role="error" class="hidden text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2"></p>
      <div class="flex items-center justify-between pt-1">
        <div data-role="left"></div>
        <div class="flex gap-2">
          <button type="button" data-act="close" class="text-sm px-4 py-2 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50">Cancel</button>
          <button type="submit" class="text-sm px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white font-medium">${esc(submitLabel)}</button>
        </div>
      </div>`;
    modal.classList.remove("hidden");
    modal.classList.add("flex");
    return form;
  }

  function showError(form, msg) {
    const err = form.querySelector("[data-role=error]");
    err.textContent = msg;
    err.classList.remove("hidden");
  }

  function field(label, inner, hint) {
    return `<label class="block text-sm font-medium text-gray-700">${label}${inner}
      ${hint ? `<span class="block text-xs font-normal text-gray-400 mt-1">${hint}</span>` : ""}</label>`;
  }

  function colourFields(bg, fg) {
    return `<div class="grid grid-cols-2 gap-3">
      ${field("Background", `<input type="color" name="bg" value="${bg}" class="mt-1 block w-full h-9 rounded border border-gray-300 cursor-pointer" />`)}
      ${field("Text colour", `<input type="color" name="fg" value="${fg}" class="mt-1 block w-full h-9 rounded border border-gray-300 cursor-pointer" />`)}
    </div>`;
  }

  function commonFields(name, duration) {
    return `<div class="grid grid-cols-3 gap-3">
      <div class="col-span-2">${field("Name <span class='text-gray-400 font-normal'>(optional)</span>",
        `<input name="name" type="text" placeholder="${esc(name)}" class="${INPUT}" />`)}</div>
      ${field("Seconds", `<input name="duration" type="number" min="1" max="3600" value="${duration}" class="${INPUT}" />`)}
    </div>`;
  }

  // --- Built-in slides -----------------------------------------------------
  const SLIDES = {
    clock: {
      title: "Add clock slide",
      body: () => {
        let zones = [];
        try { zones = Intl.supportedValuesOf("timeZone"); } catch (_) {}
        return `
          ${field("Label <span class='text-gray-400 font-normal'>(optional)</span>",
            `<input name="label" type="text" placeholder="e.g. New York" class="${INPUT}" />`)}
          ${field("Time zone", `<input name="tz" list="cuiZones" placeholder="The Pi's own time zone" class="${INPUT}" />
            <datalist id="cuiZones">${zones.map(z => `<option value="${esc(z)}">`).join("")}</datalist>`,
            "Leave blank to use the Pi's time zone, or pick one such as Europe/London.")}
          <div class="flex flex-wrap gap-x-5 gap-y-2 text-sm text-gray-700">
            <label class="flex items-center gap-2"><input type="checkbox" name="h24" checked /> 24-hour</label>
            <label class="flex items-center gap-2"><input type="checkbox" name="sec" /> Seconds</label>
            <label class="flex items-center gap-2"><input type="checkbox" name="date" checked /> Date</label>
          </div>
          ${colourFields("#000000", "#ffffff")}
          ${commonFields("Clock", 15)}`;
      },
      build: f => {
        const p = new URLSearchParams();
        if (f.label.value.trim()) p.set("label", f.label.value.trim());
        if (f.tz.value.trim()) {
          try { new Intl.DateTimeFormat(undefined, { timeZone: f.tz.value.trim() }); }
          catch (_) { throw new Error("Unknown time zone — pick one from the list."); }
          p.set("tz", f.tz.value.trim());
        }
        p.set("h24", f.h24.checked ? "1" : "0");
        if (f.sec.checked) p.set("sec", "1");
        if (!f.date.checked) p.set("date", "0");
        return { params: p, name: f.label.value.trim() ? `Clock · ${f.label.value.trim()}` : "Clock" };
      },
    },

    weather: {
      title: "Add weather slide",
      body: () => `
        ${field("Location", `<div class="flex gap-2 mt-1">
            <input name="q" type="text" placeholder="Town or city" class="${INPUT.replace("mt-1 ", "")}" />
            <button type="button" data-act="find" class="text-sm px-3 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50">Find</button>
          </div>`)}
        <div data-role="places" class="space-y-1"></div>
        <input type="hidden" name="lat" /><input type="hidden" name="lon" /><input type="hidden" name="place" />
        <p data-role="chosen" class="hidden text-sm text-green-700"></p>
        ${field("Units", `<select name="units" class="${INPUT}">
            <option value="c">°C and km/h</option><option value="f">°F and mph</option></select>`)}
        ${colourFields("#0f172a", "#ffffff")}
        ${commonFields("Weather", 20)}
        <p class="text-xs text-gray-400">Forecasts from Open-Meteo. The Pi needs internet access; it keeps showing the last forecast if the connection drops.</p>`,
      setup: form => {
        const list = form.querySelector("[data-role=places]");
        const chosen = form.querySelector("[data-role=chosen]");
        const search = async () => {
          const q = form.q.value.trim();
          if (!q) return;
          list.innerHTML = `<p class="text-sm text-gray-400">Searching…</p>`;
          try {
            const res = await fetch(`https://geocoding-api.open-meteo.com/v1/search?count=6&name=${encodeURIComponent(q)}`);
            const data = await res.json();
            const results = data.results || [];
            if (!results.length) { list.innerHTML = `<p class="text-sm text-gray-500">No places found.</p>`; return; }
            list.innerHTML = results.map((r, i) => {
              const where = [r.admin1, r.country].filter(Boolean).join(", ");
              return `<button type="button" data-i="${i}" class="block w-full text-left text-sm px-3 py-1.5 rounded-lg hover:bg-indigo-50">
                <span class="font-medium text-gray-800">${esc(r.name)}</span> <span class="text-gray-500">${esc(where)}</span></button>`;
            }).join("");
            list.querySelectorAll("[data-i]").forEach(btn => btn.addEventListener("click", () => {
              const r = results[+btn.dataset.i];
              form.lat.value = r.latitude.toFixed(4);
              form.lon.value = r.longitude.toFixed(4);
              form.place.value = r.name;
              chosen.textContent = `✓ ${r.name}${r.country ? ", " + r.country : ""}`;
              chosen.classList.remove("hidden");
              list.innerHTML = "";
            }));
          } catch (_) {
            list.innerHTML = `<p class="text-sm text-red-600">Couldn't search — is this computer online?</p>`;
          }
        };
        form.querySelector("[data-act=find]").addEventListener("click", search);
        form.q.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); search(); } });
      },
      build: f => {
        if (!f.lat.value) throw new Error("Search for a location and pick one from the list.");
        const p = new URLSearchParams({ lat: f.lat.value, lon: f.lon.value, place: f.place.value });
        if (f.units.value === "f") p.set("units", "f");
        return { params: p, name: `Weather · ${f.place.value}` };
      },
    },

    text: {
      title: "Add text slide",
      body: () => `
        ${field("Heading", `<input name="title" type="text" class="${INPUT}" />`)}
        ${field("Message", `<textarea name="body" rows="4" class="${INPUT}"></textarea>`, "Text is sized automatically to fit the screen.")}
        ${field("Alignment", `<select name="align" class="${INPUT}"><option value="center">Centred</option><option value="left">Left</option></select>`)}
        ${colourFields("#1e3a8a", "#ffffff")}
        ${commonFields("Message", 15)}`,
      build: f => {
        const title = f.title.value.trim(), body = f.body.value.trim();
        if (!title && !body) throw new Error("Enter a heading or a message.");
        const p = new URLSearchParams();
        if (title) p.set("title", title);
        if (body) p.set("body", body);
        if (f.align.value === "left") p.set("align", "left");
        return { params: p, name: title || body.split("\n")[0].slice(0, 40) };
      },
    },
  };

  function slideSrc(kind, form) {
    const spec = SLIDES[kind];
    const built = spec.build(form);
    built.params.set("bg", form.bg.value.replace("#", ""));
    built.params.set("fg", form.fg.value.replace("#", ""));
    return { src: `/widgets/${kind}?${built.params.toString()}`, name: built.name };
  }

  window.openSlideEditor = function (kind, onSave) {
    const spec = SLIDES[kind];
    if (!spec) return;
    const form = openModal(spec.title, spec.body(), "Add to playlist");
    if (spec.setup) spec.setup(form);

    if (window.SLIDE_PREVIEW) {
      const left = form.querySelector("[data-role=left]");
      left.innerHTML = `<button type="button" class="text-sm text-indigo-600 hover:underline">Preview</button>`;
      left.firstChild.addEventListener("click", () => {
        try { window.open(slideSrc(kind, form).src, "_blank"); }
        catch (ex) { showError(form, ex.message); }
      });
    }

    form.onsubmit = async e => {
      e.preventDefault();
      try {
        const { src, name } = slideSrc(kind, form);
        const duration = Math.max(1, parseInt(form.duration.value, 10) || 15);
        await onSave({ src, name: form.name.value.trim() || name, duration });
        closeModal();
      } catch (ex) {
        showError(form, ex.message || "Could not add slide.");
      }
    };
    const first = form.querySelector("input:not([type=hidden]), textarea");
    if (first) first.focus();
  };

  // --- Auto-refresh for web pages -----------------------------------------
  const REFRESH_CHOICES = [[0, "Off"], [60, "Every minute"], [300, "Every 5 minutes"],
    [900, "Every 15 minutes"], [1800, "Every 30 minutes"], [3600, "Every hour"]];

  window.openRefreshEditor = function (asset, onSave) {
    const current = (asset.options && asset.options.refresh_seconds) || 0;
    const preset = REFRESH_CHOICES.some(([s]) => s === current);
    const form = openModal("Auto-refresh", `
      <p class="text-sm text-gray-500 -mt-2 truncate">${esc(asset.name)}</p>
      <p class="text-sm text-gray-600">Reload this page while it's on screen, for dashboards and live pages. The new copy loads in the background, so the screen doesn't flash.</p>
      ${field("Reload", `<select name="choice" class="${INPUT}">
          ${REFRESH_CHOICES.map(([s, l]) => `<option value="${s}" ${s === current ? "selected" : ""}>${l}</option>`).join("")}
          <option value="custom" ${preset ? "" : "selected"}>Custom…</option></select>`)}
      <div data-role="custom" class="${preset ? "hidden" : ""}">
        ${field("Every (seconds)", `<input name="seconds" type="number" min="10" max="86400" value="${current || 120}" class="${INPUT}" />`)}
      </div>`, "Save");
    form.choice.addEventListener("change", () => {
      form.querySelector("[data-role=custom]").classList.toggle("hidden", form.choice.value !== "custom");
    });
    form.onsubmit = async e => {
      e.preventDefault();
      const seconds = form.choice.value === "custom" ? parseInt(form.seconds.value, 10) || 0 : +form.choice.value;
      try {
        await onSave({ options: { refresh_seconds: seconds || null } });
        closeModal();
      } catch (ex) {
        showError(form, ex.message || "Could not save.");
      }
    };
  };
})();
