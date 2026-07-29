"use strict";
/* =====================================================================
   Route Coverage Map — renders the payload produced by the Python
   pipeline. No backend: the payload is either inlined at build time
   (standalone file) or fetched from ./data.json when served locally.

   The payload only ever contains technical identifiers, so nothing here
   can display a person: routes and regions are the labels.
   ===================================================================== */

const PALETTE = [
  "#e05252", "#e08c3a", "#c9a227", "#8fbf3f", "#2fae5f", "#2fbfa8", "#35b0d9",
  "#4d82e0", "#7a6bf0", "#a35df0", "#cc55d6", "#e0559b", "#d97777", "#b0803a",
  "#8a9a3f", "#3f8f7a", "#5aa0c9", "#7a94e0", "#b08ae0", "#c98ab0"
];
const UNASSIGNED_COLOR = "#8a97a8";
const UNASSIGNED_REGION = "__unassigned__";

const PRECISION_LABEL = {
  manual: "Manually pinned coordinate",
  block: "Approximate (block level, jittered)",
  address: "Address level",
  street: "Street level",
  district: "District outline (OpenStreetMap)",
  district_area: "Approximate (district area)",
  parent_district: "Approximate (parent district)",
  osm_variant: "District outline (name variant)",
  ibge_district: "Official outline (IBGE 2022 census)",
  voronoi: "Approximate area of influence (Voronoi)",
  city: "Municipal boundary"
};
const APPROXIMATE = new Set(["voronoi", "parent_district", "city", "district_area"]);

const state = {
  data: null,
  routes: {},          // route_id -> { route_id, region, specialist_id, color, areas }
  regions: {},         // region -> enabled
  layers: { cities: true, districts: true, origins: true },
  search: "",
  sort: "areas",
  map: null,
  groups: {},
  initialBounds: null
};

/* ---------- helpers ---------- */

const norm = (value) => (value || "")
  .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
  .toLowerCase().replace(/\s+/g, " ").trim();

const fmt = (value) => (value == null ? "—" : value.toLocaleString());

const el = (id) => document.getElementById(id);

function regionOf(record) {
  return record.specialist_id ? (record.region || "OTHER") : UNASSIGNED_REGION;
}

function regionLabel(region) {
  return region === UNASSIGNED_REGION ? "Unassigned routes" : region;
}

/* Colour per route: unique within a region, and chosen so that routes of the
   same region end up as far apart perceptually as the palette allows.
   Deterministic (routes sorted by id), so rebuilds keep their colours. */
function assignColors(routes) {
  const toRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const rgb = PALETTE.map(toRgb);
  const distance = (a, b) => { // redmean approximation
    const rm = (a[0] + b[0]) / 2, dr = a[0] - b[0], dg = a[1] - b[1], db = a[2] - b[2];
    return Math.sqrt((2 + rm / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rm) / 256) * db * db);
  };
  const byRegion = {};
  Object.values(routes).forEach((route) => {
    (byRegion[route.region] = byRegion[route.region] || []).push(route);
  });
  Object.entries(byRegion).forEach(([region, group]) => {
    if (region === UNASSIGNED_REGION) {
      group.forEach((route) => { route.color = UNASSIGNED_COLOR; });
      return;
    }
    group.sort((a, b) => a.route_id.localeCompare(b.route_id));
    const used = [];
    group.forEach((route, index) => {
      let best = index % PALETTE.length, bestScore = -1;
      for (let i = 0; i < PALETTE.length; i++) {
        if (used.includes(i)) continue;
        const score = used.length ? Math.min(...used.map((j) => distance(rgb[i], rgb[j]))) : 999;
        if (score > bestScore) { bestScore = score; best = i; }
      }
      used.push(best);
      route.color = PALETTE[best];
    });
  });
}

function buildModel(data) {
  const routes = {};
  const track = (record) => {
    const id = record.route_id || "—";
    if (!routes[id]) {
      routes[id] = { route_id: id, region: regionOf(record), specialist_id: record.specialist_id || null, areas: 0, color: UNASSIGNED_COLOR };
    }
    routes[id].areas += 1;
  };
  (data.cities || []).forEach(track);
  (data.districts || []).forEach(track);
  assignColors(routes);
  state.routes = routes;
  state.regions = {};
  Object.values(routes).forEach((route) => { state.regions[route.region] = true; });
}

function colorOf(record) {
  const route = state.routes[record.route_id || "—"];
  return route ? route.color : UNASSIGNED_COLOR;
}

function visible(record) {
  const route = state.routes[record.route_id || "—"];
  if (!route || !state.regions[route.region]) return false;
  if (!state.search) return true;
  const haystack = norm([record.route_id, record.region, record.specialist_id, record.city, record.district, record.state].filter(Boolean).join(" "));
  return haystack.includes(state.search);
}

/* ---------- rendering ---------- */

function popupHtml(record, kind, subtitle) {
  const precision = PRECISION_LABEL[record.location_precision] || record.location_precision || "—";
  const rows = [
    ["Route", record.route_id || "—"],
    ["Region", record.specialist_id ? (record.region || "—") : "unassigned"],
    ["Specialist", record.specialist_id || "—"],
    ["Precision", precision]
  ];
  const warning = APPROXIMATE.has(record.location_precision)
    ? '<div class="pp-warn">Approximated area — not an official boundary.</div>'
    : "";
  return `<div class="pp-name">${kind}</div><div class="pp-sub">${subtitle}</div>` +
    rows.map(([label, value]) => `<div class="pp-row"><span>${label}</span><span>${value}</span></div>`).join("") +
    warning;
}

function place(record) {
  return [record.city, record.state].filter(Boolean).join(" · ");
}

function drawLayers() {
  const { data, map } = state;
  Object.values(state.groups).forEach((group) => group.clearLayers());

  (data.cities || []).forEach((record) => {
    if (!state.layers.cities || !visible(record)) return;
    L.geoJSON(record.geometry, {
      style: { color: colorOf(record), weight: 1, opacity: .8, fillColor: colorOf(record), fillOpacity: .12, dashArray: "4 4" }
    }).bindPopup(popupHtml(record, record.city || "Whole city", `Whole municipality · ${record.state || ""}`)).addTo(state.groups.cities);
  });

  (data.districts || []).forEach((record) => {
    if (!state.layers.districts || !visible(record)) return;
    const approximate = APPROXIMATE.has(record.location_precision);
    L.geoJSON(record.geometry, {
      style: {
        color: colorOf(record),
        weight: approximate ? 1 : 1.6,
        opacity: approximate ? .55 : .9,
        fillColor: colorOf(record),
        fillOpacity: approximate ? .22 : .38,
        dashArray: approximate ? "3 5" : null
      }
    }).bindPopup(popupHtml(record, record.district || "District", place(record))).addTo(state.groups.districts);
  });

  (data.specialists || []).forEach((record) => {
    if (!state.layers.origins || !visible(record)) return;
    L.circleMarker([record.latitude, record.longitude], {
      radius: 6, weight: 2, color: "#fff", fillColor: colorOf(record), fillOpacity: 1
    }).bindPopup(popupHtml(record, record.specialist_id, `Home base · ${place(record)}`)).addTo(state.groups.origins);
  });

  const shown = Object.values(state.groups).reduce((total, group) => total + group.getLayers().length, 0);
  el("mapBanner").classList.toggle("on", shown === 0);
  el("mapBanner").textContent = "No coverage matches the current filters.";
  if (!state.initialBounds) {
    // LayerGroup has no getBounds(), so the extent is accumulated from the
    // individual layers currently drawn.
    const bounds = L.latLngBounds([]);
    Object.values(state.groups).forEach((group) => group.eachLayer((layer) => {
      if (layer.getBounds) bounds.extend(layer.getBounds());
      else if (layer.getLatLng) bounds.extend(layer.getLatLng());
    }));
    if (bounds.isValid()) { state.initialBounds = bounds; map.fitBounds(bounds, { padding: [30, 30] }); }
  }
}

function renderSidebar() {
  const data = state.data;
  const routes = Object.values(state.routes);
  const areas = (data.cities || []).length + (data.districts || []).length;
  const approximate = (data.districts || []).filter((record) => APPROXIMATE.has(record.location_precision)).length;

  el("kpiRoutes").textContent = fmt(routes.length);
  el("kpiAreas").textContent = fmt(areas);
  el("kpiReal").textContent = fmt(areas - approximate);
  el("kpiApprox").textContent = fmt(approximate);
  el("badgeRoutes").textContent = `${routes.length} routes`;
  el("badgeCities").textContent = `${(data.cities || []).length} cities`;
  el("badgeDistricts").textContent = `${(data.districts || []).length} districts`;
  if (data.generated_at) el("datasetTag").textContent = `generated ${data.generated_at.slice(0, 10)}`;

  const counts = {};
  routes.forEach((route) => { counts[route.region] = (counts[route.region] || 0) + 1; });
  el("regionFilters").innerHTML = Object.keys(counts).sort().map((region) => {
    const sample = routes.find((route) => route.region === region);
    const swatch = region === UNASSIGNED_REGION
      ? '<span class="swatch dash"></span>'
      : `<span class="swatch" style="background:${sample.color}"></span>`;
    return `
    <label class="filter-row${state.regions[region] ? "" : " off"}">
      <input type="checkbox" data-region="${region}" ${state.regions[region] ? "checked" : ""}>
      ${swatch}
      <span>${regionLabel(region)}</span>
      <span class="count">${counts[region]}</span>
    </label>`;
  }).join("");
  el("regionFilters").querySelectorAll("input").forEach((input) => {
    input.onchange = () => { state.regions[input.dataset.region] = input.checked; refresh(); };
  });

  const listed = routes
    .filter((route) => state.regions[route.region])
    .filter((route) => !state.search || norm(`${route.route_id} ${route.region} ${route.specialist_id || ""}`).includes(state.search))
    .sort((a, b) => {
      if (state.sort === "route") return a.route_id.localeCompare(b.route_id);
      if (state.sort === "region") return a.region.localeCompare(b.region) || a.route_id.localeCompare(b.route_id);
      return b.areas - a.areas || a.route_id.localeCompare(b.route_id);
    });
  el("routeCount").textContent = `(${listed.length})`;
  const widest = Math.max(1, ...listed.map((route) => route.areas));
  el("routeList").innerHTML = listed.map((route) => `
    <button class="route-item" data-route="${route.route_id}" title="Zoom to ${route.route_id}">
      <span class="route-top">
        <span class="swatch" style="background:${route.color}"></span>
        <span class="route-id">${route.route_id}</span>
        <span class="route-n">${route.areas}</span>
      </span>
      <span class="route-bar"><i style="width:${Math.round(100 * route.areas / widest)}%;background:${route.color}"></i></span>
      <span class="route-meta">${regionLabel(route.region)}${route.specialist_id ? ` · <span class="tag">${route.specialist_id}</span>` : ""}</span>
    </button>`).join("");
  el("routeList").querySelectorAll(".route-item").forEach((button) => {
    button.onclick = () => zoomToRoute(button.dataset.route);
  });

  el("legend").innerHTML = `
    <h4>How to read it</h4>
    <div class="lg-row"><span class="lg-sw" style="background:${PALETTE[4]}"></span> Real outline (OSM / IBGE)</div>
    <div class="lg-row"><span class="lg-sw dash"></span> Approximated (Voronoi or parent area)</div>
    <div class="lg-row"><span class="lg-sw" style="background:${UNASSIGNED_COLOR}"></span> Unassigned route</div>
    <div class="lg-row"><span class="lg-sw ring" style="background:${PALETTE[7]}"></span> Specialist origin</div>
    <div class="lg-note">Origins are displaced ~100 m: the published point is never an address.</div>`;
}

function zoomToRoute(routeId) {
  const records = [...(state.data.cities || []), ...(state.data.districts || [])].filter((record) => record.route_id === routeId);
  if (!records.length) return;
  const group = L.featureGroup(records.map((record) => L.geoJSON(record.geometry)));
  state.map.fitBounds(group.getBounds(), { padding: [40, 40] });
}

function refresh() {
  drawLayers();
  renderSidebar();
}

/* ---------- boot ---------- */

function showMessage(html) {
  el("loader").classList.remove("hidden");
  el("loaderBody").innerHTML = `<div class="ld-msg">${html}</div>`;
}

function start(data) {
  state.data = data;
  buildModel(data);

  state.map = L.map("map", { zoomControl: true, preferCanvas: true }).setView([-15.8, -47.9], 4);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager_nolabels/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    maxZoom: 19
  }).addTo(state.map);
  state.groups = {
    cities: L.layerGroup().addTo(state.map),
    districts: L.layerGroup().addTo(state.map),
    origins: L.layerGroup().addTo(state.map)
  };

  el("search").oninput = (event) => { state.search = norm(event.target.value); refresh(); };
  el("routeSort").onchange = (event) => { state.sort = event.target.value; renderSidebar(); };
  el("btnSidebar").onclick = () => el("layout").classList.toggle("collapsed");
  el("btnTheme").onclick = () => {
    const root = document.documentElement;
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
  };
  el("btnReset").onclick = () => { if (state.initialBounds) state.map.fitBounds(state.initialBounds, { padding: [30, 30] }); };
  el("btnSelectAll").onclick = () => { Object.keys(state.regions).forEach((r) => { state.regions[r] = true; }); refresh(); };
  el("btnSelectNone").onclick = () => { Object.keys(state.regions).forEach((r) => { state.regions[r] = false; }); refresh(); };
  document.querySelectorAll(".pill[data-layer]").forEach((pill) => {
    pill.onclick = () => {
      const layer = pill.dataset.layer;
      state.layers[layer] = !state.layers[layer];
      pill.classList.toggle("on", state.layers[layer]);
      refresh();
    };
  });

  refresh();
  el("loader").classList.add("hidden");
}

async function boot() {
  if (typeof L === "undefined") {
    return showMessage("The map library did not load. If you opened this file from disk, re-download it — the standalone build embeds everything it needs.");
  }
  let data = window.ROUTE_COVERAGE_DATA;
  if (!data) {
    try {
      const response = await fetch("./data.json", { cache: "no-store" });
      if (!response.ok) throw new Error(response.status);
      data = await response.json();
    } catch (error) {
      return showMessage("No coverage payload found. Build the standalone file with <b>python -m route_coverage build --demo</b>, or place a <b>data.json</b> next to this page.");
    }
  }
  if (!data.districts && !data.cities) {
    return showMessage("The payload has no coverage in it. Re-run the build.");
  }
  try {
    start(data);
  } catch (error) {
    showMessage(`Failed to render the map: <b>${error.message}</b>.`);
    console.error(error);
  }
}

boot();
