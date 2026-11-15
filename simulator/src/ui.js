// 'clash' (the strategy-game 3/4 view) is the default camera.
export const MODES = [
  { id: 'clash', label: 'Default', key: '1' },
  { id: 'isometric', label: 'Isometric', key: '2' },
  { id: 'topdown', label: 'Top-down', key: '3' },
  { id: 'street', label: 'Street', key: '4' },
  { id: 'drone', label: 'Drone', key: '5' },
];

export const VACANCY_CLASSES = [
  { code: 2, key: 'vacant', label: 'Vacant lot', color: '#FF5A4F' },
  { code: 3, key: 'closed', label: 'Closed businesses', color: '#FFA43A' },
  { code: 1, key: 'not_vacant', label: 'Occupied', color: '#CFE2F3' },
  { code: 0, key: 'unknown', label: 'Unknown / no parcel', color: '#E9E4DB' },
];

const el = (tag, cls, html) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
};

export function createUI(root) {
  const loader = el('div', 'loader');
  loader.innerHTML = `
    <div class="loader-card">
      <div class="loader-title">San Francisco</div>
      <div class="loader-sub">Building the city…</div>
      <div class="bar"><div class="bar-fill"></div></div>
      <div class="loader-stage">Starting</div>
    </div>`;
  root.appendChild(loader);
  const fill = loader.querySelector('.bar-fill');
  const stage = loader.querySelector('.loader-stage');

  const hud = el('div', 'hud hidden');
  root.appendChild(hud);

  return {
    setProgress(label, p) {
      stage.textContent = label;
      fill.style.width = `${Math.round(Math.min(1, Math.max(0, p)) * 100)}%`;
    },
    fail(msg) {
      stage.textContent = msg;
      loader.classList.add('error');
    },
    ready({ meta, stats, neighborhoods, onMode, onRotate, onJump, onToggleType, onFocusType, onShowAll, onTerrain, onTrees, onClear, onRadius, onOutlines, radius,
      onColorBy, onToggleVac, onFocusVac }) {
      loader.classList.add('done');
      setTimeout(() => loader.remove(), 700);
      hud.classList.remove('hidden');

      // ---- title
      const title = el('div', 'panel title');
      title.innerHTML = `
        <div class="t1">San Francisco <span>3D city model</span></div>
        <div class="t2">${stats.buildings.toLocaleString()} buildings · ${(stats.tris / 1e6).toFixed(1)}M triangles · ${stats.trees.toLocaleString()} trees</div>`;
      hud.appendChild(title);

      // ---- legend: colour by building type or by parcel vacancy
      const legend = el('div', 'panel legend');
      const tabs = el('div', 'legend-tabs');
      tabs.innerHTML = '<button data-mode="type" class="on">Building type</button><button data-mode="vacancy">Vacancy</button>';
      legend.appendChild(tabs);

      const typeSec = el('div', 'legend-sec');
      typeSec.appendChild(el('div', 'legend-head', 'Building type <button class="link" data-all>show all</button>'));
      const rows = [];
      const addRow = (sec, color, label, count, onClick, onHover) => {
        const row = el('button', 'legend-row');
        row.innerHTML = `<span class="sw" style="--c:${color}"></span><span class="lbl">${label}</span><span class="cnt">${count}</span>`;
        row.title = 'Click to hide / show · hover to highlight';
        row.addEventListener('click', () => onClick(row.classList.toggle('off')));
        row.addEventListener('mouseenter', () => onHover(true));
        row.addEventListener('mouseleave', () => onHover(false));
        sec.appendChild(row);
        return row;
      };
      meta.types.forEach((t, i) => {
        if (!t.count) return;
        rows.push(addRow(typeSec, t.color, t.label, t.count.toLocaleString(),
          (off) => onToggleType(i, off), (on) => onFocusType(on ? i : -1)));
      });
      typeSec.querySelector('[data-all]').addEventListener('click', () => {
        rows.forEach((r) => r.classList.remove('off'));
        onShowAll();
      });
      typeSec.appendChild(el('div', 'legend-note', `Tower = ${meta.towerThresholdM} m or taller, any use.`));
      legend.appendChild(typeSec);

      const vac = meta.vacancy || { buildings: {}, lots: {} };
      const vacSec = el('div', 'legend-sec hidden');
      vacSec.appendChild(el('div', 'legend-head', 'Parcel vacancy <span class="sub">buildings · lots</span>'));
      for (const c of VACANCY_CLASSES) {
        const b = (vac.buildings[c.key] ?? 0).toLocaleString();
        const l = c.key in vac.lots ? vac.lots[c.key].toLocaleString() : '–';
        addRow(vacSec, c.color, c.label, `${b} · ${l}`, (off) => onToggleVac(c.code, off), (on) => onFocusVac(on ? c.code : -1));
      }
      vacSec.appendChild(el('div', 'legend-note',
        '<b>Vacant lot</b>: the team Parcel Planner release marks the parcel vacant (empty ones are hatched red on the ground). <b>Closed businesses</b>: every Google Maps business in the building is closed (Drive Places data).'));
      legend.appendChild(vacSec);
      hud.appendChild(legend);

      let colorBy = 'type';
      const setColorBy = (mode) => {
        colorBy = mode;
        for (const b of tabs.querySelectorAll('button')) b.classList.toggle('on', b.dataset.mode === mode);
        typeSec.classList.toggle('hidden', mode !== 'type');
        vacSec.classList.toggle('hidden', mode !== 'vacancy');
        vacBtn?.classList.toggle('on', mode === 'vacancy');
        onColorBy(mode);
      };
      tabs.addEventListener('click', (e) => { if (e.target.dataset.mode) setColorBy(e.target.dataset.mode); });
      let vacBtn = null;

      // ---- jump-to
      const jump = el('div', 'panel jump');
      const sel = el('select');
      sel.innerHTML = `<option value="">Jump to…</option>` + neighborhoods.map((n, i) => `<option value="${i}">${n.name}</option>`).join('');
      sel.addEventListener('change', () => {
        if (sel.value !== '') onJump(neighborhoods[Number(sel.value)]);
        sel.value = '';
        sel.blur();
      });
      jump.appendChild(sel);
      hud.appendChild(jump);

      // ---- toolbar
      const bar = el('div', 'panel toolbar');
      const modeBtns = {};
      const rotL = el('button', 'tb icon', '⟲');
      rotL.title = 'Rotate 90° (Q)';
      rotL.addEventListener('click', () => onRotate(-1));
      bar.appendChild(rotL);
      for (const m of MODES) {
        const b = el('button', 'tb', `${m.label}<kbd>${m.key}</kbd>`);
        b.addEventListener('click', () => onMode(m.id));
        modeBtns[m.id] = b;
        bar.appendChild(b);
      }
      const rotR = el('button', 'tb icon', '⟳');
      rotR.title = 'Rotate 90° (E)';
      rotR.addEventListener('click', () => onRotate(1));
      bar.appendChild(rotR);
      bar.appendChild(el('span', 'sep'));
      const terr = el('button', 'tb toggle on', 'Hills<kbd>T</kbd>');
      terr.title = 'Real terrain elevation vs. flat game board (T)';
      terr.addEventListener('click', () => onTerrain(terr.classList.toggle('on')));
      bar.appendChild(terr);
      const trees = el('button', 'tb toggle on', 'Trees<kbd>G</kbd>');
      trees.addEventListener('click', () => onTrees(trees.classList.toggle('on')));
      bar.appendChild(trees);
      const lines = el('button', 'tb toggle on', 'Outlines<kbd>O</kbd>');
      lines.addEventListener('click', () => onOutlines(lines.classList.toggle('on')));
      bar.appendChild(lines);
      vacBtn = el('button', 'tb toggle', 'Vacancy<kbd>V</kbd>');
      vacBtn.title = 'Colour buildings and lots by parcel vacancy (V)';
      vacBtn.addEventListener('click', () => setColorBy(colorBy === 'vacancy' ? 'type' : 'vacancy'));
      bar.appendChild(vacBtn);
      bar.appendChild(el('span', 'sep'));
      const focus = el('button', 'tb toggle on', 'Focus<kbd>F</kbd>');
      focus.title = 'Full detail inside the clear radius, blurred low-detail city outside (F)';
      bar.appendChild(focus);
      const slider = el('label', 'radius');
      slider.title = 'Clear radius around the view centre';
      slider.innerHTML = `<input type="range" min="300" max="4000" step="50" value="${radius}"><span>${(radius / 1000).toFixed(2)} km</span>`;
      const input = slider.querySelector('input');
      const out = slider.querySelector('span');
      input.addEventListener('input', () => {
        out.textContent = `${(Number(input.value) / 1000).toFixed(2)} km`;
        onRadius(Number(input.value));
      });
      focus.addEventListener('click', () => {
        const on = focus.classList.toggle('on');
        slider.classList.toggle('disabled', !on);
        onClear(on);
      });
      bar.appendChild(slider);
      hud.appendChild(bar);

      // ---- help + attribution
      hud.appendChild(el('div', 'panel help',
        '<b>Click</b> building info · <b>Drag</b> pan · <b>Wheel</b> zoom · <b>Q/E</b> rotate · <b>WASD</b> move · <b>1–5</b> camera · <b>V</b> vacancy'));
      hud.appendChild(el('div', 'attrib',
        meta.sources.map((s) => `<a href="${s.url}" target="_blank" rel="noopener">${s.name}</a>`).join(' · ')));

      // ---- building info card (type and lot status only; no address or coordinates)
      const card = el('div', 'panel bcard hidden');
      card.setAttribute('role', 'dialog');
      card.setAttribute('aria-label', 'Building details');
      hud.appendChild(card);
      let onCardClose = () => {};
      // codes from vacancy.bin (see VACANCY_CLASSES)
      const LOT = {
        2: ['vacant', 'Vacant lot', 'No recorded use on this lot'],
        3: ['closed', 'Closed businesses', 'Every listed business here is closed'],
        1: ['occupied', 'Not vacant', 'Lot has a recorded use'],
        0: ['unknown', 'Unknown', 'No land-use record for this lot'],
      };

      return {
        showBuilding({ type, lot }, onClose) {
          onCardClose = onClose;
          const t = meta.types[type] || { label: 'Building', color: '#ccc' };
          const [cls, head, sub] = lot == null
            ? ['unknown', 'Unavailable', 'Rebuild data (npm run data) for vacancy']
            : LOT[lot] || LOT[0];
          card.innerHTML = `
            <button class="bcard-x" aria-label="Close">×</button>
            <div class="bcard-type"><span class="sw" style="--c:${t.color}"></span>${t.label}</div>
            <div class="bcard-row"><span class="k">Status</span><span class="pill ${cls}">${head}</span></div>
            <div class="bcard-sub">${sub}</div>
            <div class="bcard-note">Source: Parcel Planner release (SF land use, Google Maps closures). Exact location is not shown.</div>`;
          card.querySelector('.bcard-x').addEventListener('click', () => onCardClose());
          card.classList.remove('hidden');
        },
        hideBuilding() { card.classList.add('hidden'); },
        /** Anchor the card above the building's screen position (or hide it when off-screen). */
        placeBuilding(x, y, visible) {
          card.style.visibility = visible ? 'visible' : 'hidden';
          if (!visible) return;
          const w = card.offsetWidth, hgt = card.offsetHeight;
          const left = Math.min(window.innerWidth - w - 12, Math.max(12, x - w / 2));
          const top = Math.min(window.innerHeight - hgt - 12, Math.max(12, y - hgt - 30));
          card.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
        },
        setMode(id) {
          for (const k in modeBtns) modeBtns[k].classList.toggle('active', k === id);
        },
        toggleTerrain() { terr.click(); },
        toggleTrees() { trees.click(); },
        toggleFocus() { focus.click(); },
        toggleOutlines() { lines.click(); },
        toggleVacancy() { vacBtn.click(); },
        setGpu(name) { title.querySelector('.t2').insertAdjacentHTML('beforeend', ` · <span class="gpu">${name}</span>`); },
      };
    },
  };
}
