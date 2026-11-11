"use strict";
/* =====================================================================
   Route Coverage Map — renders the payload produced by the Python
   pipeline. No backend: the payload is either inlined at build time
   (standalone file) or fetched from ./data.json when served locally.

   The payload only ever contains technical identifiers, so nothing here
   can display a person: routes and regions are the labels. Every value
   that reaches innerHTML still goes through esc(): names come from
   spreadsheets, and the file gets forwarded around.
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
/* For a district, these precisions mean the outline is not the district's
   own. For a whole-city record, "city" is the real municipal boundary. */
const APPROXIMATE = new Set(["voronoi", "parent_district", "city", "district_area"]);

const WHY_APPROXIMATE = {
  voronoi: "No outline exists for this district in OpenStreetMap or the IBGE census. This is the part of the city closer to its point than to any other district's, clipped to the municipal boundary.",
  parent_district: "Drawn from a larger area that contains the district, not from its own outline.",
  district_area: "Drawn from the area around the district's point, not from an official outline.",
  city: "Only the municipal boundary was found, so it stands in for the district."
};

/* Where each outline came from, in the order the cascade tries them. The
   style of the bar mirrors the map: solid = real, hatched = approximation. */
const SOURCES = [
  { label: "OpenStreetMap", fill: "real", match: (a) => a.kind === "district" && a.rec.location_precision === "district" },
  { label: "OSM, name variant", fill: "real", match: (a) => a.kind === "district" && a.rec.location_precision === "osm_variant" },
  { label: "IBGE 2022 census", fill: "real", match: (a) => a.kind === "district" && a.rec.location_precision === "ibge_district" },
  { label: "Municipal boundary", fill: "city", match: (a) => a.kind === "city" },
  { label: "Voronoi (approx.)", fill: "approx", match: (a) => a.approx && a.rec.location_precision === "voronoi" },
  { label: "Parent area (approx.)", fill: "approx", match: (a) => a.approx },
  { label: "Other", fill: "real", match: () => true }
];

/* Basemap. CARTO's free basemaps started answering with tiles stamped
   "API KEY REQUIRED", so the map uses OpenStreetMap, which needs no key:
   the official server when the page has an http(s) address, and OSM France
   when the file is opened from disk, because the official server's usage
   policy asks for a Referer, which a file:// page cannot send. */
const ON_THE_WEB = /^https?:$/.test(location.protocol);
const OSM_CREDIT = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const TILES = ON_THE_WEB
  ? { url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", subdomains: "abc", attribution: OSM_CREDIT }
  : { url: "https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png", subdomains: "abc", attribution: `${OSM_CREDIT} · tiles OSM France` };
const BASEMAPS = {
  muted: { label: "Muted", title: "Quiet basemap: the coverage colours carry the picture" },
  streets: { label: "Streets", title: "The OpenStreetMap style, unfiltered" }
};

const THEME_KEY = "route-coverage-theme";
const REDUCED_MOTION = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const SVG_NS = "http://www.w3.org/2000/svg";

const state = {
  data: null,
  areas: [],          // { index, rec, kind, approx, route, name, place, haystack, layer }
  origins: [],        // { rec, route, haystack, layer }
  routes: {},         // route_id -> { route_id, region, specialist_id, color, areas, approx, cities }
  regions: {},        // region -> enabled
  layers: { districts: true, cities: true, origins: true },
  search: "",         // normalized
  searchRaw: "",
  sort: "areas",
  base: "muted",
  selected: null,     // route_id
  preview: null,      // route_id under the pointer in the route list
  shown: [],          // areas currently drawn
  map: null,
  groups: {},
  pulse: null,
  allBounds: null,
  t0: 0
};

/* ---------- helpers ---------- */

const el = (id) => document.getElementById(id);

const norm = (value) => (value || "")
  .normalize("NFD").replace(/[̀-ͯ]/g, "")
  .toLowerCase().replace(/\s+/g, " ").trim();

const fmt = (value) => (value == null ? "—" : Number(value).toLocaleString("en-US"));

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (value) => String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ESCAPES[c]);

const isNarrow = () => window.matchMedia("(max-width: 780px)").matches;
const plural = (n, one, many) => `${fmt(n)} ${n === 1 ? one : many}`;

function regionOf(record) {
  return record.specialist_id ? (record.region || "OTHER") : UNASSIGNED_REGION;
}

function regionLabel(region) {
  return region === UNASSIGNED_REGION ? "Unassigned routes" : region;
}

function colorOf(routeId) {
  const route = state.routes[routeId];
  return route ? route.color : UNASSIGNED_COLOR;
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

/* Hatch fill for approximated areas: one SVG pattern per route colour,
   created the first time it is needed. */
function hatchFor(color) {
  const id = `hatch-${color.replace(/[^0-9a-z]/gi, "")}`;
  if (!document.getElementById(id)) {
    const pattern = document.createElementNS(SVG_NS, "pattern");
    pattern.setAttribute("id", id);
    pattern.setAttribute("patternUnits", "userSpaceOnUse");
    pattern.setAttribute("width", "7");
    pattern.setAttribute("height", "7");
    pattern.setAttribute("patternTransform", "rotate(45)");
    const tint = document.createElementNS(SVG_NS, "rect");
    tint.setAttribute("width", "7");
    tint.setAttribute("height", "7");
    tint.setAttribute("fill", color);
    tint.setAttribute("fill-opacity", ".14");
    const line = document.createElementNS(SVG_NS, "rect");
    line.setAttribute("width", "2.2");
    line.setAttribute("height", "7");
    line.setAttribute("fill", color);
    line.setAttribute("fill-opacity", ".62");
    pattern.append(tint, line);
    el("patterns").querySelector("defs").appendChild(pattern);
  }
  return `url(#${id})`;
}

/* ---------- model ---------- */

function buildModel(data) {
  const routes = {};
  const areas = [];
  const add = (rec, kind) => {
    const id = rec.route_id || "—";
    if (!routes[id]) {
      routes[id] = {
        route_id: id, region: regionOf(rec), specialist_id: rec.specialist_id || null,
        color: UNASSIGNED_COLOR, areas: 0, approx: 0, cities: 0
      };
    }
    const approx = kind === "district" && APPROXIMATE.has(rec.location_precision);
    const route = routes[id];
    route.areas += 1;
    if (approx) route.approx += 1;
    if (kind === "city") route.cities += 1;
    areas.push({
      index: areas.length, rec, kind, approx, route: id, layer: null,
      name: kind === "city" ? (rec.city || "Whole city") : (rec.district || "District"),
      place: kind === "city" ? `Whole municipality · ${rec.state || ""}` : [rec.city, rec.state].filter(Boolean).join(" · "),
      haystack: norm([id, rec.region, rec.specialist_id, rec.city, rec.district, rec.state].filter(Boolean).join(" "))
    });
  };
  (data.cities || []).forEach((rec) => add(rec, "city"));
  (data.districts || []).forEach((rec) => add(rec, "district"));
  assignColors(routes);

  state.routes = routes;
  state.areas = areas;
  state.origins = (data.specialists || []).map((rec) => ({
    rec, route: rec.route_id || "—", layer: null,
    haystack: norm([rec.route_id, rec.region, rec.specialist_id, rec.city, rec.state].filter(Boolean).join(" "))
  }));
  state.regions = {};
  Object.values(routes).forEach((route) => { state.regions[route.region] = true; });
}

function routeOn(routeId) {
  const route = state.routes[routeId];
  return !!route && state.regions[route.region];
}

function areaVisible(area) {
  if (!state.layers[area.kind === "city" ? "cities" : "districts"]) return false;
  if (!routeOn(area.route)) return false;
  return !state.search || area.haystack.includes(state.search);
}

function originVisible(origin) {
  if (!state.layers.origins || !routeOn(origin.route)) return false;
  return !state.search || origin.haystack.includes(state.search);
}

/* ---------- map styles ---------- */

/* The route in focus: the one previewed from the list (hover), else the
   selected one. Everything else is dimmed while there is one. */
function focusRoute() {
  return state.preview || state.selected;
}

function styleFor(area, hovered = false) {
  const color = colorOf(area.route);
  const focus = focusRoute();
  const isSelected = focus === area.route;
  const dim = !!focus && !isSelected;
  const lift = hovered ? 1 : 0;
  if (area.kind === "city") {
    return {
      color, weight: (isSelected ? 2.4 : 1.4) + lift, opacity: dim ? .3 : .9, dashArray: "6 5",
      fillColor: color, fillOpacity: dim ? .03 : (isSelected ? .2 : .1) + lift * .08
    };
  }
  if (area.approx) {
    return {
      color, weight: (isSelected ? 2.2 : 1.2) + lift, opacity: dim ? .25 : .85, dashArray: "3 5",
      fillColor: hatchFor(color), fillOpacity: dim ? .22 : 1
    };
  }
  return {
    color, weight: (isSelected ? 2.6 : 1.6) + lift, opacity: dim ? .3 : .95, dashArray: null,
    fillColor: color, fillOpacity: dim ? .07 : (isSelected ? .5 : .36) + lift * .12
  };
}

function originStyle(origin) {
  const focus = focusRoute();
  const dim = !!focus && focus !== origin.route;
  return { radius: 6.5, weight: 2.5, color: "#fff", opacity: dim ? .45 : 1, fillColor: colorOf(origin.route), fillOpacity: dim ? .35 : 1 };
}

/* ---------- drawing ---------- */

function drawLayers() {
  Object.values(state.groups).forEach((group) => group.clearLayers());
  state.shown = [];

  state.areas.forEach((area) => {
    area.layer = null;
    if (!areaVisible(area)) return;
    const layer = L.geoJSON(area.rec.geometry, {
      style: () => styleFor(area),
      className: `area area-${area.kind}${area.approx ? " area-approx" : ""}`
    });
    layer.on("mouseover", (event) => {
      layer.setStyle(styleFor(area, true));
      showTip(areaTip(area), event.containerPoint);
    });
    layer.on("mouseout", () => {
      layer.setStyle(styleFor(area));
      hideTip();
    });
    layer.on("click", (event) => {
      L.DomEvent.stopPropagation(event);
      selectRoute(area.route, { area });
    });
    layer.addTo(state.groups[area.kind === "city" ? "cities" : "districts"]);
    area.layer = layer;
    state.shown.push(area);
  });

  state.origins.forEach((origin) => {
    origin.layer = null;
    if (!originVisible(origin)) return;
    const { rec } = origin;
    const layer = L.circleMarker([rec.latitude, rec.longitude], { ...originStyle(origin), className: "origin" });
    layer.on("mouseover", (event) => {
      layer.setStyle({ weight: 3.5 });
      showTip(originTip(origin), event.containerPoint);
    });
    layer.on("mouseout", () => { layer.setStyle(originStyle(origin)); hideTip(); });
    layer.on("click", (event) => {
      L.DomEvent.stopPropagation(event);
      selectRoute(origin.route, { origin });
    });
    layer.addTo(state.groups.origins);
    origin.layer = layer;
  });

  applySelectionStyles();
  el("hudCount").textContent = fmt(state.shown.length);
  const nothing = state.shown.length === 0 && !state.origins.some((origin) => origin.layer);
  el("mapBanner").hidden = !nothing;
}

/* Restyles what is on the map for the current selection, without
   rebuilding any layer. */
function applySelectionStyles() {
  state.shown.forEach((area) => {
    const isSelected = state.selected === area.route;
    area.layer.eachLayer((path) => {
      const node = path.getElement && path.getElement();
      if (node) node.classList.toggle("is-selected", isSelected);
    });
  });
  restyle();

  if (state.pulse) { state.map.removeLayer(state.pulse); state.pulse = null; }
  const home = state.selected && state.origins.find((origin) => origin.route === state.selected && origin.layer);
  if (home) {
    state.pulse = L.marker([home.rec.latitude, home.rec.longitude], {
      interactive: false, keyboard: false, zIndexOffset: 1000,
      icon: L.divIcon({ className: "", iconSize: [0, 0], html: `<div class="pulse" style="--c:${colorOf(home.route)}"></div>` })
    }).addTo(state.map);
  }
}

/* Colours and order only (no layer rebuilt): cheap enough to run on every
   hover of the route list. */
function restyle() {
  const focus = focusRoute();
  state.shown.forEach((area) => {
    area.layer.setStyle(styleFor(area));
    if (area.route === focus) area.layer.bringToFront();
  });
  state.origins.forEach((origin) => {
    if (!origin.layer) return;
    origin.layer.setStyle(originStyle(origin));
    if (origin.route === focus) origin.layer.bringToFront();
  });
}

/* Hovering (or tabbing through) the route list lights that route up on the
   map, without moving the camera. Pointer devices only: on touch the "hover"
   would stick after the tap. */
const CAN_HOVER = window.matchMedia && window.matchMedia("(hover: hover)").matches;

function previewRoute(routeId) {
  if (state.preview === routeId) return;
  state.preview = routeId;
  restyle();
}

/* ---------- hover tip ---------- */

function areaTip(area) {
  const route = state.routes[area.route];
  const tag = area.kind === "city"
    ? '<span class="tag">Whole municipality</span>'
    : area.approx ? '<span class="tag warn">Approximated area</span>' : '<span class="tag">Real outline</span>';
  return `<div class="tip-title"><span class="dot" style="background:${colorOf(area.route)}"></span>${esc(area.name)}</div>` +
    `<div class="tip-sub">${esc(area.place)}</div>` +
    `<div class="tip-sub"><b>${esc(area.route)}</b> · ${esc(regionLabel(route.region))}</div>${tag}`;
}

function originTip(origin) {
  const { rec } = origin;
  return `<div class="tip-title"><span class="dot" style="background:${colorOf(origin.route)}"></span>${esc(rec.specialist_id)}</div>` +
    `<div class="tip-sub">Home base · ${esc([rec.city, rec.state].filter(Boolean).join(" · "))}</div>` +
    `<div class="tip-sub">Serves <b>${esc(origin.route)}</b></div><span class="tag">Displaced ~100 m</span>`;
}

function placeTip(point) {
  const tip = el("tip");
  const size = state.map.getSize();
  let x = point.x + 16, y = point.y + 14;
  if (x + tip.offsetWidth + 8 > size.x) x = point.x - 16 - tip.offsetWidth;
  if (y + tip.offsetHeight + 8 > size.y) y = point.y - 14 - tip.offsetHeight;
  tip.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
}

function showTip(html, point) {
  const tip = el("tip");
  tip.innerHTML = html;
  if (point) placeTip(point);
  tip.classList.add("on");
}

function hideTip() { el("tip").classList.remove("on"); }

/* One update per frame at most: mousemove fires far more often than the
   screen redraws. */
function wirePointer() {
  let last = null, queued = false;
  const frame = () => {
    queued = false;
    if (!last) return;
    el("hudCoord").textContent = `${last.latlng.lat.toFixed(5)}, ${last.latlng.lng.toFixed(5)}`;
    if (el("tip").classList.contains("on")) placeTip(last.containerPoint);
  };
  state.map.on("mousemove", (event) => {
    last = event;
    if (!queued) { queued = true; L.Util.requestAnimFrame(frame); }
  });
  state.map.on("mouseout movestart zoomstart", hideTip);
  const zoom = () => { el("hudZoom").textContent = `z ${Math.round(state.map.getZoom() * 10) / 10}`; };
  state.map.on("zoomend", zoom);
  zoom();
}

/* ---------- selection and detail card ---------- */

/* Keyboard: a card opened from the keyboard takes the focus (on its title),
   and closing it gives the focus back to whatever opened it, instead of
   dropping it on <body>. */
let focusReturn = null;

function selectRoute(routeId, { area = null, origin = null, zoom = false, focus = false } = {}) {
  const active = document.activeElement;
  if (active && active !== document.body && !el("detail").contains(active)) focusReturn = active;
  state.preview = null;
  state.selected = routeId;
  applySelectionStyles();
  markActiveRoute();
  if (area) showAreaDetail(area);
  else if (origin) showOriginDetail(origin);
  else showRouteDetail(routeId);
  if (focus) el("detailTitle").focus();
  if (zoom) zoomToRoute(routeId);
  scheduleLink();
}

function restoreFocus(routeId) {
  let target = focusReturn && focusReturn.isConnected ? focusReturn : null;
  if (!target && routeId) {
    target = [...el("routeList").querySelectorAll(".route-item")].find((item) => item.dataset.route === routeId) || null;
  }
  // A closed drawer on a phone hides the list: the map takes the focus then.
  if (target && el("panel").contains(target) && !panelIsOpen()) target = null;
  (target || el("map")).focus();
  focusReturn = null;
}

function clearSelection() {
  if (!state.selected && el("detail").hidden) return;
  const routeId = state.selected;
  const hadFocus = el("detail").contains(document.activeElement);
  state.preview = null;
  state.selected = null;
  applySelectionStyles();
  markActiveRoute();
  closeDetail();
  if (hadFocus) restoreFocus(routeId);
  scheduleLink();
}

/* The card leaves with a short fade (CSS .leaving), then is hidden. A new
   selection during the fade cancels it (openDetail). */
let detailTimer = null;

function closeDetail() {
  const detail = el("detail");
  if (detail.hidden) return;
  clearTimeout(detailTimer);
  if (REDUCED_MOTION) { detail.hidden = true; return; }
  detail.classList.add("leaving");
  detailTimer = setTimeout(() => {
    detail.hidden = true;
    detail.classList.remove("leaving");
  }, 200);
}

function row(label, value) {
  return `<div class="detail-row"><span>${label}</span><strong>${value}</strong></div>`;
}

function areaSwatch(area) {
  return `<span class="sw ${area.kind === "city" ? "sw-city" : area.approx ? "sw-approx" : "sw-real"}"></span>`;
}

function areaList(routeId, except) {
  const items = state.areas
    .filter((area) => area.route === routeId && area !== except)
    .sort((a, b) => (a.rec.city || "").localeCompare(b.rec.city || "") || a.name.localeCompare(b.name));
  if (!items.length) return '<div class="empty">No other area on this route.</div>';
  return `<div class="list animate">${items.map((area) => `
    <button type="button" class="area-item" data-area="${area.index}" title="Zoom to ${esc(area.name)}">
      ${areaSwatch(area)}<span>${esc(area.name)}</span>
      <span class="sub">${esc(area.kind === "city" ? area.rec.state : area.rec.city)}</span>
    </button>`).join("")}</div>`;
}

function openDetail(color, html) {
  const detail = el("detail");
  clearTimeout(detailTimer);
  detail.classList.remove("leaving");
  detail.style.setProperty("--c", color);
  detail.innerHTML = html;
  detail.hidden = false;
  detail.scrollTop = 0;
}

function detailHeader(title, subtitle) {
  return `<div class="detail-top"><span class="dot"></span>
    <div class="detail-heading"><h2 class="detail-title" id="detailTitle" tabindex="-1">${esc(title)}</h2><div class="detail-sub">${esc(subtitle)}</div></div>
    <button type="button" class="detail-close" data-act="close" aria-label="Close and clear the selection" title="Close (Esc)">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>
    </button></div>`;
}

const ACTIONS = '<div class="detail-actions"><button type="button" class="btn primary" data-act="zoom-route">Zoom to route</button>' +
  '<button type="button" class="btn" data-act="close">Clear selection</button></div>';

function specialistValue(route) {
  return route.specialist_id ? `<span class="tag">${esc(route.specialist_id)}</span>` : "unassigned";
}

function showAreaDetail(area) {
  const route = state.routes[area.route];
  const precision = PRECISION_LABEL[area.rec.location_precision] || area.rec.location_precision || "—";
  const alert = area.approx
    ? `<div class="detail-alert"><strong>Approximated area</strong><span>${esc(WHY_APPROXIMATE[area.rec.location_precision] || "Not an official boundary.")}</span></div>`
    : "";
  openDetail(route.color,
    detailHeader(area.name, area.place) + alert +
    row("Route", esc(route.route_id)) +
    row("Region", esc(regionLabel(route.region))) +
    row("Specialist", specialistValue(route)) +
    row("Outline", esc(precision)) +
    `<div class="detail-section"><h4>Same route · ${plural(route.areas - 1, "other area", "other areas")}</h4>${areaList(area.route, area)}</div>` +
    ACTIONS);
}

function showRouteDetail(routeId) {
  const route = state.routes[routeId];
  openDetail(route.color,
    detailHeader(route.route_id, regionLabel(route.region)) +
    row("Specialist", specialistValue(route)) +
    row("Covered areas", fmt(route.areas)) +
    row("Real outlines", fmt(route.areas - route.approx)) +
    row("Approximated", route.approx ? `<span class="tag warn">${fmt(route.approx)}</span>` : "0") +
    (route.cities ? row("Whole cities", fmt(route.cities)) : "") +
    `<div class="detail-section"><h4>Areas on this route</h4>${areaList(routeId, null)}</div>` +
    ACTIONS);
}

function showOriginDetail(origin) {
  const { rec } = origin;
  const route = state.routes[origin.route];
  openDetail(colorOf(origin.route),
    detailHeader(rec.specialist_id, `Home base · ${[rec.city, rec.state].filter(Boolean).join(" · ")}`) +
    row("Route", esc(origin.route)) +
    row("Region", esc(route ? regionLabel(route.region) : rec.region || "—")) +
    row("Position", esc(PRECISION_LABEL[rec.location_precision] || rec.location_precision || "—")) +
    '<div class="note">The point is displaced about 100 m on purpose: a published home base is never an address.</div>' +
    (route ? `<div class="detail-section"><h4>Areas served</h4>${areaList(origin.route, null)}</div>` : "") +
    ACTIONS);
}

/* ---------- camera ---------- */

function boundsOf(areas) {
  const bounds = L.latLngBounds([]);
  areas.forEach((area) => bounds.extend(L.geoJSON(area.rec.geometry).getBounds()));
  return bounds;
}

/* Leaves room for the detail card on the right, so the target is not hidden
   behind it. */
function flyTo(bounds) {
  if (!bounds.isValid()) return;
  const detail = el("detail");
  const open = !detail.hidden;
  /* Desktop: the card is on the right. Phone: it is a sheet at the bottom. */
  const options = isNarrow()
    ? { paddingTopLeft: [24, 24], paddingBottomRight: [24, open ? detail.offsetHeight + 24 : 120], maxZoom: 14 }
    : { paddingTopLeft: [40, 40], paddingBottomRight: [open ? 370 : 40, 70], maxZoom: 14 };
  if (REDUCED_MOTION) state.map.fitBounds(bounds, { ...options, animate: false });
  else state.map.flyToBounds(bounds, { ...options, duration: .8 });
}

function zoomToRoute(routeId) {
  const areas = state.areas.filter((area) => area.route === routeId);
  const bounds = boundsOf(areas);
  state.origins.filter((origin) => origin.route === routeId).forEach((origin) => bounds.extend([origin.rec.latitude, origin.rec.longitude]));
  flyTo(bounds);
}

function fitAll(animate = !REDUCED_MOTION) {
  if (state.allBounds && state.allBounds.isValid()) {
    state.map.fitBounds(state.allBounds, { padding: [30, 30], animate });
  }
}

/* ---------- side panel ---------- */

function renderStats() {
  const shown = state.shown;
  const routes = new Set(shown.map((area) => area.route));
  const origins = state.origins.filter((origin) => origin.layer).length;
  el("kpiAreas").textContent = fmt(shown.length);
  el("kpiRoutes").textContent = fmt(routes.size);
  el("kpiCities").textContent = fmt(shown.filter((area) => area.kind === "city").length);
  el("kpiDistricts").textContent = fmt(shown.filter((area) => area.kind === "district").length);
  el("kpiOrigins").textContent = fmt(origins);

  const counts = SOURCES.map(() => 0);
  shown.forEach((area) => { counts[SOURCES.findIndex((source) => source.match(area))] += 1; });
  const widest = Math.max(1, ...counts);
  const rows = SOURCES.map((source, i) => counts[i] ? `
    <div class="bar-row${source.fill === "approx" ? " approx" : ""}">
      <span class="bar-label"><span class="sw sw-${source.fill}"></span>${source.label}</span>
      <span class="bar-track"><i class="bar-fill fill-${source.fill}" style="width:${Math.round(100 * counts[i] / widest)}%"></i></span>
      <span class="bar-num">${fmt(counts[i])}</span>
    </div>` : "").join("");
  const stats = state.data.stats || {};
  const clipped = stats.overlaps_clipped
    ? `<div class="note"><strong>${fmt(stats.overlaps_clipped)}</strong> overlapping outlines were clipped at build time, so no two specialists share an area.</div>`
    : "";
  el("sourceBars").innerHTML = (rows || '<div class="empty">Nothing in view.</div>') + clipped;
}

function renderRegions() {
  const routes = Object.values(state.routes);
  const byRegion = {};
  routes.forEach((route) => { (byRegion[route.region] = byRegion[route.region] || []).push(route); });
  el("regionFilters").innerHTML = Object.keys(byRegion).sort((a, b) => {
    if (a === UNASSIGNED_REGION) return 1;
    if (b === UNASSIGNED_REGION) return -1;
    return a.localeCompare(b);
  }).map((region) => {
    const group = byRegion[region];
    const swatch = region === UNASSIGNED_REGION
      ? '<span class="dot dot-unassigned"></span>'
      : `<span class="dots">${group.slice(0, 4).map((route) => `<i style="background:${route.color}"></i>`).join("")}</span>`;
    return `<label class="chip" title="${esc(plural(group.length, "route", "routes"))} in ${esc(regionLabel(region))}">
      <input type="checkbox" data-region="${esc(region)}" ${state.regions[region] ? "checked" : ""}>
      ${swatch}${esc(regionLabel(region))}<span class="n">${group.length}</span></label>`;
  }).join("");
}

function listedRoutes() {
  const counts = {};
  state.shown.forEach((area) => { counts[area.route] = (counts[area.route] || 0) + 1; });
  return Object.values(state.routes)
    .filter((route) => counts[route.route_id])
    .sort((a, b) => {
      if (state.sort === "route") return a.route_id.localeCompare(b.route_id);
      if (state.sort === "region") return a.region.localeCompare(b.region) || a.route_id.localeCompare(b.route_id);
      if (state.sort === "approx") return b.approx / b.areas - a.approx / a.areas || b.areas - a.areas;
      return b.areas - a.areas || a.route_id.localeCompare(b.route_id);
    });
}

function renderRouteList(animate) {
  const listed = listedRoutes();
  el("routeCount").textContent = `(${listed.length})`;
  const widest = Math.max(1, ...listed.map((route) => route.areas));
  const list = el("routeList");
  list.className = `list list-scroll${animate ? " animate" : ""}`;
  list.innerHTML = listed.length ? listed.map((route) => {
    const real = route.areas - route.approx;
    const approx = route.approx ? ` · <span class="tag warn">${route.approx} approx.</span>` : "";
    const who = route.specialist_id ? ` · <span class="tag">${esc(route.specialist_id)}</span>` : "";
    return `
    <button type="button" class="route-item" data-route="${esc(route.route_id)}" style="--c:${route.color}" title="Show ${esc(route.route_id)} on the map">
      <span class="ri-top"><span class="dot"></span><span class="ri-id">${esc(route.route_id)}</span><span class="ri-n">${route.areas}</span></span>
      <span class="ri-bar"><i style="width:${Math.round(100 * route.areas / widest)}%"></i></span>
      <span class="ri-meta">${esc(regionLabel(route.region))}${who} · ${real} real${approx}</span>
    </button>`;
  }).join("") : '<div class="empty">No route matches the filters.</div>';
  markActiveRoute();
}

function markActiveRoute() {
  el("routeList").querySelectorAll(".route-item").forEach((item) => {
    const active = item.dataset.route === state.selected;
    item.classList.toggle("active", active);
    item.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function renderSearchMessage() {
  const msg = el("searchMsg");
  if (!state.search) { msg.className = "msg"; msg.innerHTML = ""; return; }
  const routes = new Set(state.shown.map((area) => area.route)).size;
  if (state.shown.length) {
    msg.className = "msg ok";
    msg.innerHTML = `<b>${plural(state.shown.length, "area", "areas")}</b> on ${plural(routes, "route", "routes")} · <kbd>Enter</kbd> zooms to them`;
  } else {
    msg.className = "msg error";
    msg.innerHTML = `Nothing matches “${esc(state.searchRaw)}”.`;
  }
}

function renderLegend() {
  el("legend").innerHTML = `
    <div class="legend-row"><span class="sw sw-real"></span>Real outline (OpenStreetMap or IBGE)</div>
    <div class="legend-row"><span class="sw sw-approx"></span>Approximated (Voronoi or parent area)</div>
    <div class="legend-row"><span class="sw sw-city"></span>Whole municipality</div>
    <div class="legend-row"><span class="sw sw-unassigned"></span>Unassigned route</div>
    <div class="legend-row"><span class="sw sw-origin"></span>Specialist home base</div>
    <div class="note">Colour is the route: routes of the same region get the most distinct colours
      the palette allows. Hover an area for its name, click it for the details.</div>`;
}

function refresh({ animate = true, regions = false } = {}) {
  drawLayers();
  if (regions) renderRegions();
  renderStats();
  renderRouteList(animate);
  renderSearchMessage();
  if (state.selected && !routeOn(state.selected)) clearSelection();
  scheduleLink();
}

/* ---------- link to the view ----------
   The address keeps the view: #z=9&c=-27.1,-49.3&route=ROUTE-SOUTH-01
   &off=COAST&hide=origins&q=centro&base=streets. Opening that link on any
   copy of the map brings back the same area, route and filters. The theme
   is not part of it: it is the preference of whoever opens it. */

let linkTimer = null;

function scheduleLink() {
  if (!state.map) return;
  clearTimeout(linkTimer);
  linkTimer = setTimeout(writeLink, 350);
}

function linkHash() {
  const center = state.map.getCenter();
  const parts = [`z=${Math.round(state.map.getZoom() * 2) / 2}`, `c=${center.lat.toFixed(5)},${center.lng.toFixed(5)}`];
  if (state.selected) parts.push(`route=${encodeURIComponent(state.selected)}`);
  const off = Object.keys(state.regions).filter((region) => !state.regions[region]);
  if (off.length) parts.push(`off=${off.map(encodeURIComponent).join(",")}`);
  const hidden = Object.keys(state.layers).filter((layer) => !state.layers[layer]);
  if (hidden.length) parts.push(`hide=${hidden.join(",")}`);
  if (state.searchRaw) parts.push(`q=${encodeURIComponent(state.searchRaw)}`);
  if (state.base !== "muted") parts.push(`base=${state.base}`);
  return `#${parts.join("&")}`;
}

function writeLink() {
  const hash = linkHash();
  if (location.hash === hash) return;
  try { history.replaceState(null, "", hash); } catch (error) {
    try { location.replace(hash); } catch (ignored) { /* no writable address: no link */ }
  }
}

function readLink() {
  const raw = (location.hash || "").replace(/^#/, "");
  if (!raw) return null;
  const values = {};
  raw.split("&").forEach((pair) => {
    const i = pair.indexOf("=");
    if (i > 0) values[pair.slice(0, i)] = pair.slice(i + 1);
  });
  const decode = (text) => { try { return decodeURIComponent(text); } catch (error) { return text; } };
  const center = /^(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/.exec(values.c || "");
  const zoom = Number(values.z);
  return {
    center: center && Math.abs(+center[1]) <= 90 && Math.abs(+center[2]) <= 180 ? [+center[1], +center[2]] : null,
    zoom: values.z && Number.isFinite(zoom) ? Math.min(19, Math.max(2, zoom)) : null,
    route: values.route ? decode(values.route) : null,
    off: values.off ? values.off.split(",").map(decode) : [],
    hide: values.hide ? values.hide.split(",") : [],
    q: values.q ? decode(values.q) : "",
    base: BASEMAPS[values.base] ? values.base : "muted"
  };
}

/* Puts the link into the controls, not only into the state, so the screen
   shows what is in force. */
function applyLink(link) {
  link.off.forEach((region) => { if (region in state.regions) state.regions[region] = false; });
  link.hide.forEach((layer) => { if (layer in state.layers) state.layers[layer] = false; });
  document.querySelectorAll("input[data-layer]").forEach((input) => { input.checked = state.layers[input.dataset.layer]; });
  if (link.q) {
    el("search").value = link.q;
    state.searchRaw = link.q;
    state.search = norm(link.q);
  }
  state.base = link.base;
  if (link.route && state.routes[link.route]) state.selected = link.route;
}

function copyLink() {
  writeLink();
  const url = location.href, button = el("btnCopyLink");
  const done = (text) => {
    button.textContent = text;
    setTimeout(() => { button.textContent = "Copy link"; }, 1800);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(() => done("Link copied"), () => window.prompt("Copy the link to this view:", url));
  } else {
    window.prompt("Copy the link to this view:", url);
  }
}

/* ---------- basemap and theme ---------- */

function applyBase() {
  state.map.getContainer().classList.toggle("base-muted", state.base === "muted");
  document.querySelectorAll("#basePicker [data-base]").forEach((button) => {
    const on = button.dataset.base === state.base;
    button.classList.toggle("on", on);
    button.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

function buildBasePicker() {
  el("basePicker").innerHTML = Object.entries(BASEMAPS).map(([id, base]) =>
    `<button type="button" class="seg" data-base="${id}" title="${base.title}" aria-pressed="false">${base.label}</button>`).join("");
  el("basePicker").addEventListener("click", (event) => {
    const button = event.target.closest("[data-base]");
    if (!button || button.dataset.base === state.base) return;
    state.base = button.dataset.base;
    applyBase();
    scheduleLink();
  });
}

/* Theme. index.html already applied the saved choice, or the system's, before
   the first paint. Until the viewer picks one, the map keeps following the
   system; a pick made with the button is remembered (when storage exists). */
function savedTheme() {
  try {
    const theme = localStorage.getItem(THEME_KEY);
    return theme === "light" || theme === "dark" ? theme : null;
  } catch (error) {
    return null;
  }
}

/* The switch cross-fades the whole page with a View Transition where the
   browser has one and motion is not reduced; otherwise it is instant. */
function setTheme(theme, { save = false } = {}) {
  const root = document.documentElement;
  if (save) {
    try { localStorage.setItem(THEME_KEY, theme); } catch (error) { /* no storage: it just is not remembered */ }
  }
  if (root.dataset.theme === theme) return;
  const apply = () => { root.dataset.theme = theme; };
  if (document.startViewTransition && !REDUCED_MOTION) document.startViewTransition(apply);
  else apply();
}

function toggleTheme() {
  setTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light", { save: true });
}

function followSystemTheme() {
  const query = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)");
  if (!query || !query.addEventListener) return;
  query.addEventListener("change", (event) => {
    if (!savedTheme()) setTheme(event.matches ? "light" : "dark");
  });
}

/* ---------- wiring ---------- */

function setPanelOpen(open) {
  if (isNarrow()) document.body.classList.toggle("panel-open", open);
  else slidePanel(open);
  el("btnPanel").setAttribute("aria-expanded", open ? "true" : "false");
}

/* Desktop. Animating the panel's width (or margin) would re-lay the page and
   resize the map on every frame. Instead the panel floats over the map while
   it slides (transform only), and the map is resized once: when the panel
   leaves the layout, or when it rejoins it. The view shifts by the same width,
   so the coverage stays where it was on screen. */
let panelTimer = null;

function slidePanel(open) {
  const app = el("app");
  const width = el("panel").offsetWidth;
  clearTimeout(panelTimer);
  if (!open) {
    if (app.classList.contains("collapsed")) return;
    const floating = app.classList.contains("panel-floating");
    app.classList.add("panel-floating", "collapsed");
    if (!floating) shiftView(-width);
    return;
  }
  if (!app.classList.contains("collapsed")) return;
  app.classList.remove("collapsed");
  panelTimer = setTimeout(() => {
    app.classList.remove("panel-floating");
    shiftView(width);
  }, REDUCED_MOTION ? 0 : 260);
}

function shiftView(dx) {
  if (!state.map) return;
  state.map.invalidateSize({ pan: false });
  state.map.panBy([dx, 0], { animate: false });
}

function panelIsOpen() {
  return isNarrow() ? document.body.classList.contains("panel-open") : !el("app").classList.contains("collapsed");
}

function showEverything() {
  Object.keys(state.regions).forEach((region) => { state.regions[region] = true; });
  Object.keys(state.layers).forEach((layer) => { state.layers[layer] = true; });
  document.querySelectorAll("input[data-layer]").forEach((input) => { input.checked = true; });
  el("search").value = "";
  state.search = state.searchRaw = "";
  refresh({ regions: true });
}

function wireControls() {
  let searchTimer = null;
  el("search").addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.searchRaw = event.target.value.trim();
      state.search = norm(state.searchRaw);
      refresh({ animate: false });
    }, 120);
  });
  el("search").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && state.search && state.shown.length) {
      flyTo(boundsOf(state.shown));
      if (isNarrow()) setPanelOpen(false);
    }
  });

  el("regionFilters").addEventListener("change", (event) => {
    const input = event.target.closest("input[data-region]");
    if (!input) return;
    state.regions[input.dataset.region] = input.checked;
    refresh();
  });
  el("btnAll").addEventListener("click", () => { Object.keys(state.regions).forEach((r) => { state.regions[r] = true; }); refresh({ regions: true }); });
  el("btnNone").addEventListener("click", () => { Object.keys(state.regions).forEach((r) => { state.regions[r] = false; }); refresh({ regions: true }); });

  document.querySelectorAll("input[data-layer]").forEach((input) => {
    input.addEventListener("change", () => { state.layers[input.dataset.layer] = input.checked; refresh(); });
  });

  el("routeSort").addEventListener("change", (event) => { state.sort = event.target.value; renderRouteList(true); });
  el("routeList").addEventListener("click", (event) => {
    const item = event.target.closest(".route-item");
    if (!item) return;
    if (isNarrow()) setPanelOpen(false);
    // detail === 0: the click came from Enter/Space, not from a pointer.
    selectRoute(item.dataset.route, { zoom: true, focus: event.detail === 0 });
  });
  if (CAN_HOVER) {
    el("routeList").addEventListener("mouseover", (event) => {
      const item = event.target.closest(".route-item");
      if (item) previewRoute(item.dataset.route);
    });
    el("routeList").addEventListener("mouseleave", () => previewRoute(null));
  }
  el("routeList").addEventListener("focusin", (event) => {
    const item = event.target.closest(".route-item");
    if (item && item.matches(":focus-visible")) previewRoute(item.dataset.route);
  });
  el("routeList").addEventListener("focusout", (event) => {
    if (!el("routeList").contains(event.relatedTarget)) previewRoute(null);
  });
  el("scrim").addEventListener("click", () => setPanelOpen(false));

  el("detail").addEventListener("click", (event) => {
    const target = event.target.closest("[data-act], [data-area]");
    if (!target) return;
    if (target.dataset.act === "close") return clearSelection();
    if (target.dataset.act === "zoom-route") return zoomToRoute(state.selected);
    const area = state.areas[Number(target.dataset.area)];
    if (area) {
      // The clicked item is replaced by the new card: keep the keyboard in it.
      selectRoute(area.route, { area, focus: event.detail === 0 });
      flyTo(boundsOf([area]));
    }
  });

  el("btnPanel").addEventListener("click", () => setPanelOpen(!panelIsOpen()));
  el("btnTheme").addEventListener("click", toggleTheme);
  el("btnCopyLink").addEventListener("click", copyLink);
  el("btnShowAll").addEventListener("click", showEverything);

  state.map.on("click", () => {
    if (isNarrow() && panelIsOpen()) setPanelOpen(false);
    clearSelection();
  });
  state.map.on("moveend", scheduleLink);

  document.addEventListener("keydown", (event) => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName);
    if (event.key === "Escape") {
      if (event.target === el("search") && el("search").value) {
        el("search").value = "";
        el("search").dispatchEvent(new Event("input"));
      } else if (!el("detail").hidden) {
        clearSelection();
      } else if (isNarrow() && panelIsOpen()) {
        setPanelOpen(false);
      }
    } else if (event.key === "/" && !typing) {
      event.preventDefault();
      if (!panelIsOpen()) setPanelOpen(true);
      el("search").focus();
    }
  });

  /* The panel slides and the window resizes: Leaflet has to re-measure. */
  if (window.ResizeObserver) new ResizeObserver(() => state.map.invalidateSize({ pan: false })).observe(el("map"));
}

const FitControl = L.Control.extend({
  options: { position: "topleft" },
  onAdd() {
    const bar = L.DomUtil.create("div", "leaflet-bar leaflet-control");
    const link = L.DomUtil.create("a", "", bar);
    link.href = "#";
    link.title = "Back to the whole coverage";
    link.setAttribute("role", "button");
    link.setAttribute("aria-label", "Back to the whole coverage");
    link.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>';
    L.DomEvent.disableClickPropagation(bar);
    L.DomEvent.on(link, "click", (event) => { L.DomEvent.preventDefault(event); fitAll(); });
    return bar;
  }
});

/* ---------- boot ---------- */

function fail(html) {
  const boot = el("boot");
  boot.classList.remove("leaving");
  boot.classList.add("failed");
  el("bootError").innerHTML = html;
  el("bootError").hidden = false;
}

function leaveBoot() {
  const boot = el("boot");
  document.body.classList.add("ready");
  boot.classList.add("leaving");
  setTimeout(() => { if (boot.parentNode && !boot.classList.contains("failed")) boot.remove(); }, 700);
}

function start(data) {
  state.data = data;
  buildModel(data);
  const link = readLink();
  if (link) applyLink(link);

  state.map = L.map("map", { zoomControl: true, minZoom: 2 }).setView([-15.8, -47.9], 4);
  L.tileLayer(TILES.url, { subdomains: TILES.subdomains, maxZoom: 19, attribution: TILES.attribution }).addTo(state.map);
  new FitControl().addTo(state.map);
  state.groups = {
    cities: L.layerGroup().addTo(state.map),
    districts: L.layerGroup().addTo(state.map),
    origins: L.layerGroup().addTo(state.map)
  };
  state.allBounds = boundsOf(state.areas);
  state.origins.forEach((origin) => state.allBounds.extend([origin.rec.latitude, origin.rec.longitude]));

  buildBasePicker();
  applyBase();
  renderLegend();
  wirePointer();
  wireControls();
  followSystemTheme();
  setPanelOpen(!isNarrow());

  refresh({ regions: true });
  /* The first view is set, not flown to: it appears as the boot screen fades. */
  if (link && link.center && link.zoom != null) state.map.setView(link.center, link.zoom, { animate: false });
  else fitAll(false);
  if (state.selected) selectRoute(state.selected);

  const generated = data.generated_at || "";
  el("builtBadge").textContent = generated ? `Built ${generated.slice(0, 10)}` : "Built —";
  el("footBuilt").textContent = generated ? `${generated.slice(0, 10)} ${generated.slice(11, 16)} UTC` : "—";
  el("footAreas").textContent = fmt(state.areas.length);
  el("footTime").textContent = `${Math.max(1, Math.round(performance.now() - state.t0))} ms`;
  leaveBoot();
}

async function boot() {
  state.t0 = performance.now();
  if (typeof L === "undefined") {
    return fail("The map library did not load. If you opened this file from disk, download it again: the standalone build embeds everything it needs.");
  }
  let data = window.ROUTE_COVERAGE_DATA;
  if (!data) {
    try {
      const response = await fetch("./data.json", { cache: "no-store" });
      if (!response.ok) throw new Error(response.status);
      data = await response.json();
    } catch (error) {
      return fail("No coverage payload found. Build the standalone file with <b>python -m route_coverage build --demo</b>, or place a <b>data.json</b> next to this page.");
    }
  }
  if (!data.districts && !data.cities) {
    return fail("The payload has no coverage in it. Run the build again.");
  }
  /* Draw after the boot screen has painted: parsing a large payload blocks
     the page for a moment. */
  requestAnimationFrame(() => setTimeout(() => {
    try {
      start(data);
    } catch (error) {
      fail(`Failed to render the map: <b>${esc(error.message)}</b>.`);
      console.error(error);
    }
  }, 0));
}

boot();
