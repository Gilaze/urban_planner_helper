const SEEN_KEY = 'sf-sim-getting-started-seen';

const STEPS = [
  {
    section: '1 of 3 · What it is', title: 'Find the right service for the right place',
    text: 'See what an area needs, compare vacant lots, and preview a building that fits. Open Details for financial estimates and permit guidance.',
    media: 'overview', caption: 'The city map, local service needs, and a suggested building preview.',
  },
  {
    section: '2 of 3 · Navigation', title: 'Explore the map',
    text: 'Drag to pan, scroll to zoom, and use the bottom buttons to change your view. Select a red vacant building or lot to add a building or jump to its tract.',
    media: 'navigation', caption: 'Moving the map and switching between camera views.',
  },
  {
    section: '3 of 3 · Tract Planner', title: 'Choose your tract and vacant lot',
    text: 'Choose a tract and building type. Compare the best matches, or pick a yellow-flashing vacant lot inside the blue boundary. Then adjust the building height.',
    media: 'selection', caption: 'Comparing matches and choosing your own vacant lot in the selected tract.',
  },
];

export function createGettingStarted(parent, trigger) {
  const dialog = document.createElement('dialog');
  dialog.className = 'getting-started';
  dialog.setAttribute('aria-labelledby', 'guide-title');
  dialog.dataset.testid = 'getting-started-dialog';
  dialog.innerHTML = `
    <div class="guide-top"><span data-guide-section></span><button type="button" class="guide-close" aria-label="Close Getting Started">×</button></div>
    <h2 id="guide-title"></h2>
    <p class="guide-copy" data-guide-copy></p>
    <figure class="guide-demo">
      <video muted autoplay loop playsinline disablepictureinpicture disableremoteplayback tabindex="-1" preload="metadata" aria-label="Looping demonstration"></video>
      <figcaption data-guide-caption></figcaption>
    </figure>
    <div class="guide-bottom">
      <button type="button" data-guide-back aria-label="Previous step">← <span>Back</span></button>
      <div class="guide-dots" aria-label="Guide steps"></div>
      <button type="button" data-guide-next aria-label="Next step"><span>Next</span> →</button>
    </div>`;
  parent.append(dialog);
  const video = dialog.querySelector('video');
  const back = dialog.querySelector('[data-guide-back]');
  const next = dialog.querySelector('[data-guide-next]');
  const dots = dialog.querySelector('.guide-dots');
  let index = 0;
  STEPS.forEach((step, i) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('aria-label', `Step ${i + 1}: ${step.title}`);
    button.addEventListener('click', () => { index = i; render(); });
    dots.append(button);
  });
  function render() {
    const step = STEPS[index];
    dialog.dataset.step = String(index + 1);
    dialog.querySelector('[data-guide-section]').textContent = step.section;
    dialog.querySelector('h2').textContent = step.title;
    dialog.querySelector('[data-guide-copy]').textContent = step.text;
    dialog.querySelector('[data-guide-caption]').textContent = step.caption;
    video.pause();
    video.poster = `/guide/${step.media}.jpg`;
    video.src = `/guide/${step.media}.mp4`;
    video.muted = true;
    video.play().catch(() => { /* Keep the poster visible if autoplay is blocked. */ });
    back.disabled = index === 0;
    next.querySelector('span').textContent = index === STEPS.length - 1 ? 'Start Planning' : 'Next';
    next.setAttribute('aria-label', index === STEPS.length - 1 ? 'Start planning' : 'Next step');
    [...dots.children].forEach((dot, i) => dot.setAttribute('aria-current', i === index ? 'step' : 'false'));
  }
  function open() {
    index = 0;
    dialog.showModal();
    render();
    try { localStorage.setItem(SEEN_KEY, '1'); } catch { /* Storage may be disabled. */ }
  }
  trigger.addEventListener('click', open);
  back.addEventListener('click', () => { if (index > 0) { index--; render(); } });
  next.addEventListener('click', () => {
    if (index === STEPS.length - 1) dialog.close();
    else { index++; render(); }
  });
  dialog.querySelector('.guide-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { video.pause(); trigger.focus(); });
  dialog.addEventListener('click', (event) => { if (event.target === dialog) {
    const r = dialog.getBoundingClientRect();
    if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
  } });
  dialog.addEventListener('keydown', (event) => {
    if (event.target === video) return;
    if (event.key === 'ArrowRight') { event.preventDefault(); next.click(); }
    if (event.key === 'ArrowLeft') { event.preventDefault(); back.click(); }
    event.stopPropagation();
  });
  let seen = false;
  try { seen = localStorage.getItem(SEEN_KEY) === '1'; } catch { /* Still offer the guide without storage. */ }
  if (!seen) open();
}
