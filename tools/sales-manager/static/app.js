/* 売上・広告費 一元管理ツール — 画面側ロジック（依存ライブラリなし） */

const FIELD_LABELS = {
  date: "日付", orders: "注文数", gross: "売上", fees: "手数料",
  shipping: "送料", cogs: "原価", refunds: "返金",
  campaign: "キャンペーン名", cost: "広告費", ad_sales: "広告経由売上",
  clicks: "クリック数", impressions: "表示回数",
};
const REQUIRED = { sales: ["date", "gross"], ads: ["date", "cost"] };
// 手数料・原価・送料は列が無くても「売上の◯%」で入れられるようにする。
const RATE_FIELDS = ["fees", "cogs", "shipping"];

const state = {
  channels: [],
  selected: new Set(),
  presets: [],
  bounds: { min: null, max: null },
  fields: { sales: [], ads: [] },
  upload: null,
};

/* ---------- 共通ユーティリティ ---------- */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

const yen = (n) => "¥" + Math.round(n || 0).toLocaleString("ja-JP");
const pct = (n) => (n || 0).toFixed(1) + "%";
const num = (n) => Math.round(n || 0).toLocaleString("ja-JP");

function shortYen(n) {
  const a = Math.abs(n);
  if (a >= 1e8) return (n / 1e8).toFixed(1) + "億";
  if (a >= 1e4) return Math.round(n / 1e4).toLocaleString("ja-JP") + "万";
  return Math.round(n).toLocaleString("ja-JP");
}

function toast(message, isError) {
  const box = $("#toast");
  box.textContent = message;
  box.style.background = isError ? "#c0392b" : "#1b2733";
  box.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => box.classList.remove("show"), 3200);
}

function message(host, text, kind, list) {
  const node = el("div", { class: "msg " + kind }, text);
  if (list && list.length) {
    node.append(el("ul", {}, list.slice(0, 8).map((t) => el("li", {}, t))));
  }
  host.replaceChildren(node);
}

async function api(path, options = {}) {
  const opts = { headers: { "X-Requested-With": "sales-manager" }, ...options };
  if (options.json !== undefined) {
    opts.method = options.method || "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(options.json);
  }
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (_) { data = { error: text }; }
  if (!res.ok) throw new Error(data.error || `通信に失敗しました (${res.status})`);
  return data;
}

function isoDate(d) {
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString().slice(0, 10);
}

/* ---------- タブ ---------- */

$$("nav.tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$("nav.tabs button").forEach((b) => b.classList.toggle("active", b === btn));
    $$(".view").forEach((v) => {
      v.hidden = v.id !== "view-" + btn.dataset.tab;
    });
    if (btn.dataset.tab === "history") loadHistory();
  });
});

/* ---------- 起動 ---------- */

async function bootstrap() {
  const data = await api("/api/bootstrap");
  state.channels = data.channels;
  state.presets = data.presets;
  state.bounds = data.bounds;
  state.fields = data.fields;
  if (state.selected.size === 0) state.channels.forEach((c) => state.selected.add(c));
  renderChips();
  fillChannelSelects();
  fillPresetSelect();
  renderManualFields();
}

function renderChips() {
  const host = $("#channel-chips");
  host.replaceChildren(
    ...state.channels.map((name) =>
      el("button", {
        class: "chip" + (state.selected.has(name) ? " on" : ""),
        onclick: () => {
          state.selected.has(name) ? state.selected.delete(name)
            : state.selected.add(name);
          renderChips();
          refresh();
        },
      }, name)),
    el("button", {
      class: "chip", title: "すべて選択",
      onclick: () => {
        state.channels.forEach((c) => state.selected.add(c));
        renderChips();
        refresh();
      },
    }, "すべて")
  );
}

function fillChannelSelects() {
  for (const id of ["#imp-channel", "#man-channel"]) {
    const sel = $(id);
    const keep = sel.value;
    sel.replaceChildren(...state.channels.map((c) => el("option", { value: c }, c)));
    if (state.channels.includes(keep)) sel.value = keep;
  }
}

function fillPresetSelect() {
  const sel = $("#imp-preset");
  sel.replaceChildren(
    el("option", { value: "" }, "（使わない）"),
    ...state.presets.map((p) =>
      el("option", { value: String(p.id) }, `${p.name}（${p.channel}）`))
  );
}

/* ---------- ダッシュボード ---------- */

function filterQuery() {
  const params = new URLSearchParams();
  if ($("#date-from").value) params.set("from", $("#date-from").value);
  if ($("#date-to").value) params.set("to", $("#date-to").value);
  // 全チャネル選択時は絞り込み条件を送らない（新しいチャネルも自動で入る）。
  if (state.selected.size && state.selected.size < state.channels.length) {
    params.set("channels", [...state.selected].join(","));
  }
  return params;
}

async function refresh() {
  try {
    const params = filterQuery();
    const summary = await api("/api/summary?" + params);
    renderKpis(summary.total);
    renderChannelTable(summary);

    params.set("granularity", $("#granularity").value);
    const trend = await api("/api/timeseries?" + params);
    renderChart(trend.points);
  } catch (err) {
    toast(err.message, true);
  }
}

function renderKpis(t) {
  const cards = [
    { label: "純売上", value: yen(t.net), hint: `売上 ${shortYen(t.gross)} − 返金 ${shortYen(t.refunds)}` },
    { label: "広告費", value: yen(t.ad_cost), hint: `広告費率 ${pct(t.ad_ratio)}` },
    {
      label: "粗利", value: yen(t.profit), hint: `粗利率 ${pct(t.margin)}`,
      tone: t.profit > 0 ? "good" : t.profit < 0 ? "bad" : "",
    },
    { label: "ACoS", value: t.ad_sales ? pct(t.acos) : "—", hint: t.ad_sales ? `ROAS ${pct(t.roas)}` : "広告経由売上が未登録" },
    { label: "注文数", value: num(t.orders), hint: t.orders ? `客単価 ${yen(t.aov)}` : "" },
    { label: "経費計", value: yen(t.fees + t.shipping + t.cogs), hint: `手数料 ${shortYen(t.fees)} / 送料 ${shortYen(t.shipping)} / 原価 ${shortYen(t.cogs)}` },
  ];
  $("#kpis").replaceChildren(...cards.map((c) =>
    el("div", { class: "kpi" },
      el("div", { class: "label" }, c.label),
      el("div", { class: "value " + (c.tone || "") }, c.value),
      el("div", { class: "hint" }, c.hint || ""))));
}

const COLUMNS = [
  ["純売上", "net", yen], ["広告費", "ad_cost", yen], ["広告費率", "ad_ratio", pct],
  ["手数料", "fees", yen], ["送料", "shipping", yen], ["原価", "cogs", yen],
  ["粗利", "profit", yen], ["粗利率", "margin", pct],
  ["ACoS", "acos", (v) => (v ? pct(v) : "—")],
  ["ROAS", "roas", (v) => (v ? pct(v) : "—")],
  ["注文数", "orders", num], ["客単価", "aov", yen],
];

function renderChannelTable(summary) {
  const table = $("#channel-table");
  if (!summary.channels.length) {
    table.replaceChildren(el("tbody", {}, el("tr", {},
      el("td", { colspan: COLUMNS.length + 1, class: "empty" },
        "データがありません。「データ取り込み」タブから CSV を読み込んでください。"))));
    return;
  }
  const head = el("thead", {}, el("tr", {},
    el("th", {}, "チャネル"), ...COLUMNS.map(([label]) => el("th", {}, label))));
  const body = el("tbody", {}, summary.channels.map((row) => renderRow(row, false)));
  body.append(renderRow(summary.total, true));
  table.replaceChildren(head, body);
}

function renderRow(row, isTotal) {
  return el("tr", { class: isTotal ? "total" : "" },
    el("td", {}, row.channel),
    ...COLUMNS.map(([, key, fmt]) =>
      el("td", { class: row[key] < 0 ? "neg" : "" }, fmt(row[key]))));
}

/* ---------- 推移グラフ（SVG を直接組み立てる） ---------- */

function niceMax(value) {
  if (value <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(value)));
  const scaled = value / exp;
  const step = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
  return step * exp;
}

function svgEl(tag, attrs) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function renderChart(points) {
  const host = $("#chart-host");
  if (!points.length) {
    host.replaceChildren(el("p", { class: "empty" }, "この期間のデータがありません。"));
    return;
  }
  const W = 1000, H = 280, L = 66, R = 54, T = 14, B = 38;
  const plotW = W - L - R, plotH = H - T - B;

  const series = [
    { key: "net", color: "#1f6feb" },
    { key: "ad_cost", color: "#f0a202" },
    { key: "profit", color: "#17875b" },
  ];
  const values = points.flatMap((p) => series.map((s) => p[s.key]));
  const top = niceMax(Math.max(1, ...values));
  const bottom = Math.min(0, ...values);
  const lo = bottom < 0 ? -niceMax(-bottom) : 0;
  const yScale = (v) => T + plotH - ((v - lo) / (top - lo)) * plotH;

  const rateTop = niceMax(Math.max(1, ...points.map((p) => p.ad_ratio)));
  const rScale = (v) => T + plotH - (v / rateTop) * plotH;

  const svg = svgEl("svg", {
    class: "chart", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none",
    role: "img", "aria-label": "売上・広告費・粗利の推移",
  });

  // 目盛りと横罫線
  for (let i = 0; i <= 4; i++) {
    const v = lo + ((top - lo) * i) / 4;
    const y = yScale(v);
    svg.append(svgEl("line", {
      x1: L, x2: W - R, y1: y, y2: y,
      stroke: v === 0 ? "#b9c4d0" : "#eef2f6", "stroke-width": v === 0 ? 1.2 : 1,
    }));
    const label = svgEl("text", {
      x: L - 8, y: y + 4, "text-anchor": "end", "font-size": 11, fill: "#6b7a8c",
    });
    label.textContent = shortYen(v);
    svg.append(label);

    const rLabel = svgEl("text", {
      x: W - R + 8, y: rScale((rateTop * i) / 4) + 4, "font-size": 11, fill: "#c0392b",
    });
    rLabel.textContent = ((rateTop * i) / 4).toFixed(0) + "%";
    svg.append(rLabel);
  }

  const slot = plotW / points.length;
  const barW = Math.max(2, Math.min(22, (slot * 0.72) / series.length));
  const groupW = barW * series.length;

  points.forEach((p, i) => {
    const centre = L + slot * (i + 0.5);
    series.forEach((s, j) => {
      const v = p[s.key];
      const y0 = yScale(0), y1 = yScale(v);
      svg.append(svgEl("rect", {
        x: centre - groupW / 2 + j * barW,
        y: Math.min(y0, y1), width: Math.max(1, barW - 1),
        height: Math.max(1, Math.abs(y1 - y0)),
        fill: s.color, rx: 1.5,
      }));
    });
    // 目盛りが混み合うときは間引いて表示する。
    const every = Math.ceil(points.length / 16);
    if (i % every === 0) {
      const text = svgEl("text", {
        x: centre, y: H - 14, "text-anchor": "middle", "font-size": 11, fill: "#6b7a8c",
      });
      text.textContent = p.bucket.length === 10 ? p.bucket.slice(5) : p.bucket;
      svg.append(text);
    }
  });

  // 広告費率（右軸）
  const path = points
    .map((p, i) => `${i ? "L" : "M"}${L + slot * (i + 0.5)},${rScale(p.ad_ratio)}`)
    .join(" ");
  svg.append(svgEl("path", {
    d: path, fill: "none", stroke: "#c0392b", "stroke-width": 2,
    "stroke-linejoin": "round",
  }));
  points.forEach((p, i) => {
    svg.append(svgEl("circle", {
      cx: L + slot * (i + 0.5), cy: rScale(p.ad_ratio), r: 2.6, fill: "#c0392b",
    }));
  });

  host.replaceChildren(svg, renderTrendTable(points));
}

function renderTrendTable(points) {
  const wrap = el("div", { class: "table-wrap", style: "margin-top:14px" });
  const table = el("table", {},
    el("thead", {}, el("tr", {},
      el("th", {}, "期間"), el("th", {}, "純売上"), el("th", {}, "広告費"),
      el("th", {}, "広告費率"), el("th", {}, "粗利"), el("th", {}, "粗利率"),
      el("th", {}, "注文数"))),
    el("tbody", {}, points.map((p) => el("tr", {},
      el("td", {}, p.bucket),
      el("td", {}, yen(p.net)),
      el("td", {}, yen(p.ad_cost)),
      el("td", {}, pct(p.ad_ratio)),
      el("td", { class: p.profit < 0 ? "neg" : "" }, yen(p.profit)),
      el("td", { class: p.profit < 0 ? "neg" : "" }, pct(p.margin)),
      el("td", {}, num(p.orders)))))
  );
  wrap.append(table);
  return wrap;
}

/* ---------- 期間フィルタ ---------- */

$$("[data-range]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const today = new Date();
    let from, to = today;
    switch (btn.dataset.range) {
      case "this-month":
        from = new Date(today.getFullYear(), today.getMonth(), 1); break;
      case "last-month":
        from = new Date(today.getFullYear(), today.getMonth() - 1, 1);
        to = new Date(today.getFullYear(), today.getMonth(), 0); break;
      case "30d":
        from = new Date(today.getTime() - 29 * 86400000); break;
      case "this-year":
        from = new Date(today.getFullYear(), 0, 1); break;
      default:
        $("#date-from").value = state.bounds.min || "";
        $("#date-to").value = state.bounds.max || "";
        refresh();
        return;
    }
    $("#date-from").value = isoDate(from);
    $("#date-to").value = isoDate(to);
    refresh();
  });
});

["#date-from", "#date-to", "#granularity"].forEach((id) =>
  $(id).addEventListener("change", refresh));

$("#btn-export-channel").addEventListener("click", () => {
  window.location = "/api/export.csv?what=channel&" + filterQuery();
});
$("#btn-export-trend").addEventListener("click", () => {
  const params = filterQuery();
  params.set("granularity", $("#granularity").value);
  window.location = "/api/export.csv?what=trend&" + params;
});

/* ---------- 取り込み ---------- */

$("#btn-add-channel").addEventListener("click", async () => {
  const name = $("#imp-new-channel").value.trim();
  if (!name) return;
  try {
    const data = await api("/api/channel", { json: { name } });
    state.channels = data.channels;
    state.selected.add(name);
    $("#imp-new-channel").value = "";
    renderChips();
    fillChannelSelects();
    $("#imp-channel").value = name;
    toast(`チャネル「${name}」を追加しました`);
  } catch (err) {
    toast(err.message, true);
  }
});

const drop = $("#drop");
drop.addEventListener("click", () => $("#file-input").click());
drop.addEventListener("dragover", (e) => {
  e.preventDefault();
  drop.classList.add("hover");
});
drop.addEventListener("dragleave", () => drop.classList.remove("hover"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("hover");
  if (e.dataTransfer.files[0]) preview(e.dataTransfer.files[0]);
});
$("#file-input").addEventListener("change", (e) => {
  if (e.target.files[0]) preview(e.target.files[0]);
});
$("#imp-kind").addEventListener("change", () => {
  if (state.upload) preview(state.upload.file);
});
$("#imp-header-row").addEventListener("change", () => {
  if (state.upload) preview(state.upload.file);
});
$("#imp-preset").addEventListener("change", () => {
  const preset = state.presets.find((p) => String(p.id) === $("#imp-preset").value);
  if (!preset) return;
  $("#imp-kind").value = preset.kind;
  if (state.channels.includes(preset.channel)) $("#imp-channel").value = preset.channel;
  if (state.upload) {
    state.upload.guess = preset.mapping;
    renderMapping();
  }
});

async function preview(file) {
  const kind = $("#imp-kind").value;
  const headerRow = Math.max(1, Number($("#imp-header-row").value) || 1) - 1;
  message($("#file-info"), `「${file.name}」を読み込んでいます…`, "info");
  try {
    const data = await api(
      `/api/preview?kind=${kind}&header_row=${headerRow}`,
      { method: "POST", body: await file.arrayBuffer() }
    );
    state.upload = { file, headerRow, ...data };
    const presetId = $("#imp-preset").value;
    const preset = state.presets.find((p) => String(p.id) === presetId);
    if (preset && preset.kind === kind) state.upload.guess = preset.mapping;

    message($("#file-info"),
      `「${file.name}」を読み込みました： ${data.row_count.toLocaleString()} 行 ／ ` +
      `文字コード ${data.encoding} ／ 区切り「${data.delimiter}」`, "ok");
    $("#step-map").hidden = false;
    renderMapping();
    renderPreviewTable();
  } catch (err) {
    message($("#file-info"), err.message, "err");
    $("#step-map").hidden = true;
  }
}

function renderMapping() {
  const kind = $("#imp-kind").value;
  const guess = state.upload.guess || {};
  $("#map-grid").replaceChildren(...state.fields[kind].map((field) => {
    const required = REQUIRED[kind].includes(field);
    const current = guess[field];
    const select = el("select", { "data-field": field },
      el("option", { value: "" }, required ? "— 選択してください —" : "（使わない）"),
      ...state.upload.headers.map((h) =>
        el("option", { value: h, selected: current === h }, h)));

    const row = el("div", { class: "map-row" },
      el("label", {}, FIELD_LABELS[field] + (required ? " *" : "")),
      select);

    if (RATE_FIELDS.includes(field)) {
      const rateValue = current && current.rate_of ? current.percent : "";
      row.append(el("div", { class: "rate" },
        "または 売上の",
        el("input", {
          type: "number", step: "0.1", min: "0", max: "100",
          "data-rate": field, value: rateValue, placeholder: "―",
        }),
        "%"));
    }
    return row;
  }));
  $("#imp-aggregate").checked = kind === "sales";
}

function renderPreviewTable() {
  const headers = state.upload.headers;
  $("#preview-table").replaceChildren(
    el("thead", {}, el("tr", {}, headers.map((h) => el("th", {}, h)))),
    el("tbody", {}, state.upload.sample.map((row) =>
      el("tr", {}, headers.map((h) => el("td", { title: row[h] || "" }, row[h] || "")))))
  );
}

function collectMapping() {
  const mapping = {};
  $$("#map-grid select").forEach((sel) => {
    if (sel.value) mapping[sel.dataset.field] = sel.value;
  });
  $$("#map-grid input[data-rate]").forEach((input) => {
    const percent = parseFloat(input.value);
    if (!Number.isNaN(percent) && percent !== 0) {
      // 率を入れた項目は列指定より優先する（手数料 10% のような使い方）。
      mapping[input.dataset.rate] = { rate_of: "gross", percent };
    }
  });
  return mapping;
}

$("#btn-commit").addEventListener("click", async () => {
  if (!state.upload) return;
  const kind = $("#imp-kind").value;
  const mapping = collectMapping();
  const missing = REQUIRED[kind].filter((f) => !mapping[f]);
  if (missing.length) {
    message($("#import-msg"),
      "必須項目が未設定です: " + missing.map((f) => FIELD_LABELS[f]).join("、"), "err");
    return;
  }
  const button = $("#btn-commit");
  button.disabled = true;
  try {
    const result = await api("/api/commit", {
      json: {
        token: state.upload.token, kind, channel: $("#imp-channel").value,
        mapping, header_row: state.upload.headerRow,
        aggregate: $("#imp-aggregate").checked,
        replace: $("#imp-replace").checked,
        filename: state.upload.file.name,
        preset_name: $("#imp-preset-name").value.trim() || null,
      },
    });
    state.presets = result.presets;
    state.channels = result.channels;
    state.channels.forEach((c) => state.selected.add(c));
    fillPresetSelect();
    fillChannelSelects();
    renderChips();
    message($("#import-msg"),
      `${result.imported} 件を取り込みました。` +
      (result.replaced ? `（既存 ${result.replaced} 件を置き換え）` : ""),
      "ok", result.warnings);
    $("#imp-preset-name").value = "";
    await bootstrapBoundsAndRefresh();
    toast("取り込みが完了しました");
  } catch (err) {
    message($("#import-msg"), err.message, "err");
  } finally {
    button.disabled = false;
  }
});

async function bootstrapBoundsAndRefresh() {
  const data = await api("/api/bootstrap");
  state.bounds = data.bounds;
  if (!$("#date-from").value && state.bounds.min) $("#date-from").value = state.bounds.min;
  if (!$("#date-to").value && state.bounds.max) $("#date-to").value = state.bounds.max;
  await refresh();
}

/* ---------- 手入力 ---------- */

$("#man-kind").addEventListener("change", renderManualFields);

function renderManualFields() {
  const kind = $("#man-kind").value;
  const fields = state.fields[kind].filter((f) => f !== "date");
  $("#manual-fields").replaceChildren(...fields.map((field) => el("div", { class: "field" },
    el("label", { for: "man-" + field }, FIELD_LABELS[field]),
    el("input", {
      id: "man-" + field, "data-manual": field,
      type: field === "campaign" ? "text" : "number",
      step: field === "campaign" ? null : "1",
      placeholder: field === "campaign" ? "任意" : "0",
    }))));
  if (!$("#man-date").value) $("#man-date").value = isoDate(new Date());
}

$("#btn-manual-save").addEventListener("click", async () => {
  const payload = {
    kind: $("#man-kind").value,
    channel: $("#man-channel").value,
    date: $("#man-date").value,
  };
  $$("[data-manual]").forEach((input) => {
    payload[input.dataset.manual] = input.value || 0;
  });
  try {
    await api("/api/manual", { json: payload });
    $$("[data-manual]").forEach((input) => { input.value = ""; });
    message($("#manual-msg"), "登録しました。", "ok");
    await bootstrapBoundsAndRefresh();
    toast("登録しました");
  } catch (err) {
    message($("#manual-msg"), err.message, "err");
  }
});

/* ---------- 履歴 ---------- */

async function loadHistory() {
  try {
    const { batches } = await api("/api/batches");
    const table = $("#batch-table");
    if (!batches.length) {
      table.replaceChildren(el("tbody", {}, el("tr", {},
        el("td", { class: "empty" }, "取り込み履歴はまだありません。"))));
    } else {
      table.replaceChildren(
        el("thead", {}, el("tr", {},
          el("th", {}, "取り込み日時"), el("th", {}, "種類"), el("th", {}, "チャネル"),
          el("th", {}, "ファイル"), el("th", {}, "件数"), el("th", {}, ""))),
        el("tbody", {}, batches.map((b) => el("tr", {},
          el("td", {}, b.created_at.replace("T", " ")),
          el("td", {}, b.kind === "sales" ? "売上" : "広告費"),
          el("td", {}, b.channel),
          el("td", {}, b.filename),
          el("td", {}, num(b.row_count)),
          el("td", {}, el("button", {
            class: "danger",
            onclick: async () => {
              if (!confirm(`「${b.filename}」の取り込み ${b.row_count} 件を取り消します。よろしいですか？`)) return;
              await api("/api/batch?id=" + b.id, { method: "DELETE" });
              await loadHistory();
              await bootstrapBoundsAndRefresh();
              toast("取り込みを取り消しました");
            },
          }, "取り消す")))))
      );
    }

    const presetTable = $("#preset-table");
    if (!state.presets.length) {
      presetTable.replaceChildren(el("tbody", {}, el("tr", {},
        el("td", { class: "empty" }, "保存済みの列設定はありません。"))));
      return;
    }
    presetTable.replaceChildren(
      el("thead", {}, el("tr", {},
        el("th", {}, "名前"), el("th", {}, "種類"), el("th", {}, "チャネル"),
        el("th", {}, "対応付け"), el("th", {}, ""))),
      el("tbody", {}, state.presets.map((p) => el("tr", {},
        el("td", {}, p.name),
        el("td", {}, p.kind === "sales" ? "売上" : "広告費"),
        el("td", {}, p.channel),
        el("td", { style: "text-align:left;white-space:normal" },
          Object.entries(p.mapping)
            .map(([k, v]) => `${FIELD_LABELS[k] || k}←${
              typeof v === "object" ? `売上の${v.percent}%` : v}`)
            .join(" / ")),
        el("td", {}, el("button", {
          class: "danger",
          onclick: async () => {
            if (!confirm(`列設定「${p.name}」を削除します。よろしいですか？`)) return;
            const data = await api("/api/preset?id=" + p.id, { method: "DELETE" });
            state.presets = data.presets;
            fillPresetSelect();
            await loadHistory();
          },
        }, "削除")))))
    );
  } catch (err) {
    toast(err.message, true);
  }
}

/* ---------- 初期表示 ---------- */

(async function start() {
  try {
    await bootstrap();
    const today = new Date();
    $("#date-from").value = state.bounds.min
      || isoDate(new Date(today.getFullYear(), today.getMonth(), 1));
    $("#date-to").value = state.bounds.max || isoDate(today);
    await refresh();
  } catch (err) {
    toast("起動に失敗しました: " + err.message, true);
  }
})();
