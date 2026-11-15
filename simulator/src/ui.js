const MODES = [
  { id: 'isometric', label: 'Isometric', key: '1' },
  { id: 'topdown', label: 'Top-down', key: '2' },
  { id: 'clash', label: 'Clash 3/4', key: '3' },
  { id: 'street', label: 'Street', key: '4' },
  { id: 'drone', label: 'Drone', key: '5' },
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
    ready({ meta, stats, neighborhoods, onMode, onRotate, onJump, onToggleType, onFocusType, onShowAll, onTerrain, onTrees, onClear, onRadius, onOutlines, radius }) {
      loader.classList.add('done');
      setTimeout(() => loader.remove(), 700);
      hud.classList.remove('hidden');

      // ---- title
      const title = el('div', 'panel title');
      title.innerHTML = `
        <div class="t1">San Francisco <span>3D city model</span></div>
        <div class="t2">${stats.buildings.toLocaleString()} buildings · ${(stats.tris / 1e6).toFixed(1)}M triangles · ${stats.trees.toLocaleString()} trees</div>`;
      hud.appendChild(title);

      // ---- legend
      const legend = el('div', 'panel legend');
      legend.appendChild(el('div', 'legend-head', 'Building type <button class="link" data-all>show all</button>'));
      const rows = [];
      meta.types.forEach((t, i) => {
        if (!t.count) return;
        const row = el('button', 'legend-row');
        row.innerHTML = `<span class="sw" style="--c:${t.color}"></span><span class="lbl">${t.label}</span><span class="cnt">${t.count.toLocaleString()}</span>`;
        row.title = 'Click to hide / show · hover to highlight';
        row.addEventListener('click', () => {
          const off = row.classList.toggle('off');
          onToggleType(i, off);
        });
        row.addEventListener('mouseenter', () => onFocusType(i));
        row.addEventListener('mouseleave', () => onFocusType(-1));
        legend.appendChild(row);
        rows.push(row);
      });
      legend.querySelector('[data-all]').addEventListener('click', () => {
        rows.forEach((r) => r.classList.remove('off'));
        onShowAll();
      });
      const note = el('div', 'legend-note', `Tower = ${meta.towerThresholdM} m or taller, any use.`);
      legend.appendChild(note);
      hud.appendChild(legend);

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
        '<b>Drag</b> pan · <b>Wheel</b> zoom · <b>Q/E</b> rotate · <b>WASD</b> move · <b>1–5</b> camera'));
      hud.appendChild(el('div', 'attrib',
        meta.sources.map((s) => `<a href="${s.url}" target="_blank" rel="noopener">${s.name}</a>`).join(' · ')));

      return {
        setMode(id) {
          for (const k in modeBtns) modeBtns[k].classList.toggle('active', k === id);
        },
        toggleTerrain() { terr.click(); },
        toggleTrees() { trees.click(); },
        toggleFocus() { focus.click(); },
        toggleOutlines() { lines.click(); },
        setGpu(name) { title.querySelector('.t2').insertAdjacentHTML('beforeend', ` · <span class="gpu">${name}</span>`); },
      };
    },
  };
}
