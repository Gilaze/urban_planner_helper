// 'clash' (the strategy-game 3/4 view) is the default camera.
export const MODES = [
  { id: 'clash', label: 'Default', key: '1' },
  { id: 'isometric', label: 'Isometric', key: '2' },
  { id: 'topdown', label: 'Top-down', key: '3' },
  { id: 'street', label: 'Street', key: '4' },
  { id: 'drone', label: 'Drone', key: '5' },
];

export const VACANCY_CLASSES = [
  { code: 2, key: 'vacant', label: 'Vacant lot', color: '#E02424' },
  { code: 3, key: 'closed', label: 'Closed businesses', color: '#1C1D21' },
  { code: 1, key: 'not_vacant', label: 'Occupied', color: '#E4E6E9' },
  { code: 0, key: 'unknown', label: 'Unknown / no parcel', color: '#E9E4DB' },
];

const el = (tag, cls, html) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
};

/**
 * A panel docked to the left or right edge. A tab on its inner edge slides it away (leaving
 * only the tab) and back out again; the open state is remembered per drawer.
 */
function createDrawer(parent, { side, label, key, openByDefault = true }) {
  const storeKey = `sf-sim-drawer:${key}`;
  let open = openByDefault;
  try {
    const saved = localStorage.getItem(storeKey);
    if (saved != null) open = saved === '1';
  } catch { /* storage blocked: use the default */ }
  const drawer = el('section', `drawer ${side}`);
  drawer.setAttribute('aria-label', label);
  drawer.innerHTML = `
    <button class="drawer-tab" type="button" aria-expanded="${open}">
      <span class="drawer-chev" aria-hidden="true"></span><span class="drawer-label">${label}</span>
    </button>
    <div class="drawer-sheet"><div class="drawer-head">${label}</div><div class="drawer-body"></div></div>`;
  const tab = drawer.querySelector('.drawer-tab');
  const body = drawer.querySelector('.drawer-body');
  const set = (v) => {
    open = v;
    drawer.classList.toggle('collapsed', !open);
    tab.setAttribute('aria-expanded', String(open));
    tab.title = `${open ? 'Hide' : 'Show'} ${label}`;
    drawer.querySelector('.drawer-sheet').inert = !open;
    try { localStorage.setItem(storeKey, open ? '1' : '0'); } catch { /* ignore */ }
  };
  tab.addEventListener('click', () => set(!open));
  set(open);
  parent.appendChild(drawer);
  return { drawer, body, setOpen: set, isOpen: () => open };
}

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
        <div class="t1">SF 3D Model</div>`;
      hud.appendChild(title);

      // ---- legend: colour by building type or by parcel vacancy
      // ---- left drawer: map key (building type / vacancy legend) + controls
      const narrow = window.innerWidth < 1100;
      const left = createDrawer(hud, { side: 'left', label: 'Map key', key: 'map-key', openByDefault: !narrow });
      const legend = el('div', 'legend');
      const tabs = el('div', 'legend-tabs');
      tabs.innerHTML = '<button data-mode="vacancy">Vacancy</button><button data-mode="type" class="on">Building type</button>';
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
      left.body.appendChild(legend);

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
      setColorBy('vacancy'); // open in the vacancy view (legend tab, map colours and toolbar toggle)
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
      const help = el('details', 'drawer-help');
      help.innerHTML = `<summary>Controls</summary><ul>
        <li><b>Click</b> a building for its info (edit vacant ones)</li><li><b>Drag</b> pan · <b>Wheel</b> zoom</li>
        <li><b>Q / E</b> rotate · <b>WASD</b> move</li><li><b>1–5</b> camera views · <b>V</b> vacancy colours</li></ul>`;
      left.body.appendChild(help);

      // ---- right drawer: the Parcel Planner panel (created by planner.js) is docked into it
      const right = createDrawer(hud, { side: 'right', label: 'Parcel Planner', key: 'planner', openByDefault: true });
      const plannerPanel = root.parentElement?.querySelector('aside.planner') || document.querySelector('aside.planner');
      if (plannerPanel) right.body.appendChild(plannerPanel);
      // Data credits, collapsed behind a small button (OpenStreetMap's licence needs visible credit).
      const attrib = el('div', 'attrib');
      attrib.innerHTML = `<button class="attrib-btn" aria-expanded="false" title="Data sources">ⓘ Sources</button>
        <div class="attrib-list" hidden>${meta.sources.map((s) => `<a href="${s.url}" target="_blank" rel="noopener">${s.name}</a>`).join('')}
          <p class="attrib-warn">Building edits are a local sketch saved only in this browser; they don't change the data or what others see. Building cards never show addresses or exact locations.</p></div>`;
      const attribBtn = attrib.querySelector('.attrib-btn'), attribList = attrib.querySelector('.attrib-list');
      attribBtn.addEventListener('click', () => {
        attribList.hidden = !attribList.hidden;
        attribBtn.setAttribute('aria-expanded', String(!attribList.hidden));
      });
      hud.appendChild(attrib);
      // Sit in the bottom-right corner; lift above the toolbar only when the toolbar reaches that far right.
      const placeAttrib = () => {
        const barRight = bar.getBoundingClientRect().right;
        const btnLeft = window.innerWidth - 16 - attribBtn.offsetWidth;
        attrib.classList.toggle('lifted', barRight + 12 > btnLeft);
      };
      window.addEventListener('resize', placeAttrib);
      new ResizeObserver(placeAttrib).observe(bar);
      placeAttrib();

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

      // ---- edits panel (appears once the user has changed any building)
      const editsPanel = el('div', 'panel edits-panel hidden');
      editsPanel.innerHTML = '<span class="edits-count"></span><button class="link" data-reset>Reset all</button>';
      hud.appendChild(editsPanel);
      let onResetAll = () => {};
      editsPanel.querySelector('[data-reset]').addEventListener('click', () => onResetAll());

      return {
        /**
         * info:    { title, color, lot, edited, choices, choiceId, heightM, footprintM2 }
         *          choices = [{ id, label, group, color, height, minH, maxH, minArea }]
         * actions: { onClose, onApply(choiceId, heightM), onDelete(), onRestore(), validate(choiceId, heightM) }
         */
        showBuilding({ title, color, lot, edited = false, choices, choiceId, heightM, footprintM2, hasRules = true,
          editable = true, lockedReason = '' }, actions) {
          onCardClose = actions.onClose;
          const [cls, head, sub] = lot == null
            ? ['unknown', 'Unavailable', 'Rebuild data (npm run data) for vacancy']
            : LOT[lot] || LOT[0];
          const groups = [...new Set(choices.map((c) => c.group))];
          const options = groups.map((g) => `<optgroup label="${g}">${choices.filter((c) => c.group === g)
            .map((c) => `<option value="${c.id}"${c.id === choiceId ? ' selected' : ''}>${c.label}</option>`).join('')}</optgroup>`).join('');
          card.innerHTML = `
            <button class="bcard-x" aria-label="Close">×</button>
            <div class="bcard-type"><span class="sw" style="--c:${color}"></span>${title}${edited ? '<span class="pill edited">Edited</span>' : ''}</div>
            <div class="bcard-row"><span class="k">Status</span><span class="pill ${cls}">${head}</span></div>
            <div class="bcard-sub">${sub}</div>
            ${!editable ? `<div class="bcard-locked">${lockedReason}</div>
            ${edited ? '<div class="bcard-actions"><button class="btn" data-restore>Restore original</button></div>' : ''}` : ''}
            ${editable && !choices.length ? `<div class="bcard-locked">No building type fits this lot under SF zoning and height rules.</div>
            <div class="bcard-actions"><button class="btn danger" data-delete>Delete</button>${edited ? '<button class="btn" data-restore>Restore original</button>' : ''}</div>` : ''}
            <div class="bcard-edit"${editable && choices.length ? '' : ' hidden'}>
              <label class="bcard-field"><span class="k">Type</span><select data-type>${options}</select></label>
              <label class="bcard-field"><span class="k">Height</span>
                <input data-hrange type="range" step="1">
                <span class="hval"><input data-hnum type="number" step="1"> m</span>
              </label>
              <div class="bcard-warn" data-warn role="alert" hidden></div>
              <div class="bcard-actions">
                <button class="btn primary" data-apply>Apply</button>
                <button class="btn danger" data-delete>Delete</button>
                ${edited ? '<button class="btn" data-restore>Restore original</button>' : ''}
              </div>
            </div>`;
          card.querySelector('.bcard-x').addEventListener('click', () => onCardClose());
          card.querySelectorAll('[data-restore]').forEach((el) => el.addEventListener('click', () => actions.onRestore()));
          card.querySelectorAll('[data-delete]').forEach((el) => el.addEventListener('click', () => actions.onDelete()));
          card.classList.remove('hidden');
          if (!editable || !choices.length) return;
          const sel = card.querySelector('[data-type]');
          const range = card.querySelector('[data-hrange]'), num = card.querySelector('[data-hnum]');
          const warn = card.querySelector('[data-warn]');
          const apply = card.querySelector('[data-apply]');
          const choiceOf = () => choices.find((c) => c.id === sel.value);
          let touched = false;
          const setRange = (c) => {
            // the slider spans what SF allows on this lot, widened to show an out-of-range original height
            const r = actions.range(c.id);
            range.min = Math.min(r.min, Number(num.value) || r.min);
            range.max = Math.max(r.max, Number(num.value) || r.max);
            num.min = 1; num.max = 999;
          };
          const check = () => {
            const v = actions.validate(sel.value, num.value === '' ? NaN : Number(num.value));
            apply.disabled = !v.ok;
            // the original building's own values are only flagged once the user changes something
            warn.hidden = v.ok || !touched;
            warn.textContent = v.reasons.join(' ');
            apply.title = v.ok ? '' : v.reasons.join(' ');
          };
          const h0 = Math.round(heightM);
          num.value = range.value = h0;
          setRange(choiceOf());
          range.value = h0;
          check();
          sel.addEventListener('change', () => {
            const c = choiceOf();
            touched = true;
            const h = actions.defaultHeight(c.id); // picking a type fills in its default height (capped at the SF limit)
            num.value = h;
            setRange(c);
            range.value = h;
            check();
          });
          range.addEventListener('input', () => { num.value = range.value; touched = true; check(); });
          num.addEventListener('input', () => { if (num.value !== '') range.value = num.value; touched = true; check(); });
          apply.addEventListener('click', () => {
            touched = true;
            check();
            if (!apply.disabled) actions.onApply(sel.value, Number(num.value));
          });
        },
        setEditCount(n, onReset) {
          onResetAll = onReset;
          editsPanel.classList.toggle('hidden', n === 0);
          editsPanel.querySelector('.edits-count').textContent = `${n} building${n === 1 ? '' : 's'} edited`;
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
        setGpu(name) { title.title = `Rendering on ${name}`; },
      };
    },
  };
}
