import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const candidates = ['clinic', 'pharmacy', 'grocery', 'bank_credit_union', 'school'];
const comparisonFixture = JSON.parse(readFileSync(new URL('../../../contracts/examples/compare.json', import.meta.url), 'utf8'));
const parcelCollection = JSON.parse(readFileSync(new URL('../../../contracts/examples/parcels.json', import.meta.url), 'utf8'));
const firstParcel = parcelCollection.features[0];
const parcelFixture = [firstParcel, { ...firstParcel, id: 'sf:fixture-002', properties: { ...firstParcel.properties, parcel_id: 'sf:fixture-002' } }];

async function openParcel(page: Page) {
  await page.goto('/');
  const selector = page.getByTestId('parcel-selector');
  await expect(selector).toBeEnabled();
  const options = selector.locator('option').filter({ hasNotText: /^Select/ });
  const values = await options.evaluateAll(elements => elements.map(el => (el as HTMLOptionElement).value).filter(Boolean));
  expect(values.length, 'At least one selectable fixture/live parcel').toBeGreaterThan(0);
  await selector.selectOption(values[0]);
  await expect(page.getByTestId('comparison-results')).toHaveAttribute('data-parcel-id', values[0]);
  await expect(page.getByTestId('comparison-results')).toHaveAttribute('data-status', 'ready');
  return values[0];
}

async function stubParcelList(page: Page) {
  await page.route('**/api/v1/parcels?**', route => route.fulfill({ json: {
    ...parcelCollection, features: parcelFixture,
  } }));
}

async function setRange(page: Page, id: string, value: number) {
  const slider = page.getByTestId(id);
  await expect(slider).toHaveAttribute('type', 'range');
  const { min, step } = await slider.evaluate((element: HTMLInputElement) => ({ min: Number(element.min || 0), step: Number(element.step || 1) }));
  const presses = Math.round((value - min) / step);
  expect(Math.abs(min + presses * step - value)).toBeLessThan(1e-8);
  await slider.focus();
  await slider.press('Home');
  for (let i = 0; i < presses; i++) await slider.press('ArrowRight');
  await expect(slider).toHaveValue(String(value));
}

async function checkLayout(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'No horizontal overflow').toBeTruthy();
  await expect(page.getByTestId('parcel-selector')).toBeInViewport();
  await expect(page.getByTestId('comparison-results')).toBeVisible();
}

test('parcel selection, preview and reset', async ({ page }) => {
  const requests: any[] = [];
  page.on('request', request => {
    if (request.method() === 'POST' && request.url().includes('/scenarios/compare')) requests.push(request.postDataJSON());
  });
  const parcelId = await openParcel(page);
  for (const category of candidates) await expect(page.getByTestId(`alternative-${category}`)).toBeVisible();
  await page.getByTestId('candidate-clinic').click();
  await expect(page.getByTestId('massing-preview')).toHaveAttribute('data-kind', 'volume');
  await expect(page.getByTestId('massing-preview')).toContainText(/illustrative/i);
  await page.getByTestId('view-2d').click();
  await expect(page.getByTestId('view-2d')).toHaveAttribute('aria-pressed', 'true');
  await page.getByText('Ranking weights', { exact: true }).click();
  const before = requests.length;
  await setRange(page, 'objective-need', 2);
  const changedResponse = page.waitForResponse(response => response.url().includes('/scenarios/compare') && response.request().method() === 'POST'
    && response.request().postDataJSON().objective_weights?.need === 2 && response.request().postDataJSON().category_priorities?.clinic === 2);
  await setRange(page, 'priority-clinic', 2);
  const changed = await (await changedResponse).json();
  for (const row of changed.alternatives) {
    if (row.rank !== null) await expect(page.getByTestId(`alternative-${row.category_id}`)).toHaveAttribute('data-rank', String(row.rank));
  }
  await expect.poll(() => requests.slice(before).some(r => r.parcel_id === parcelId && r.objective_weights.need === 2 && r.category_priorities?.clinic === 2)).toBeTruthy();
  await page.getByRole('button', { name: /^reset$/i }).click();
  for (const dimension of ['need', 'economic', 'sustainability']) await expect(page.getByTestId(`objective-${dimension}`)).toHaveValue('1');
  for (const category of candidates) await expect(page.getByTestId(`priority-${category}`)).toHaveValue('1');
  await expect.poll(() => requests.at(-1)?.objective_weights).toEqual({ need: 1, economic: 1, sustainability: 1 });
  await expect.poll(() => requests.at(-1)?.category_priorities).toEqual(Object.fromEntries(candidates.map(c => [c, 1])));
  await expect(page.getByTestId('raw-metrics-clinic')).toContainText(/m|USD|FTE|population/i);
  await checkLayout(page);
});

test('clicking empty ground opens estimates and renders the proposed volume', async ({ page }) => {
  const png = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
    'base64',
  );
  await page.route('**/data/*', async route => {
    const name = new URL(route.request().url()).pathname.split('/').at(-1);
    if (name === 'meta.json') {
      await route.fulfill({ json: {
        origin: { lon: -122.435, lat: 37.77, metersPerDegLon: 88000, metersPerDegLat: 111000 },
        buildingCount: 0, towerThresholdM: 60, types: [], sources: [],
      } });
      return;
    }
    if (name?.endsWith('.png')) {
      await route.fulfill({ contentType: 'image/png', body: png });
      return;
    }
    const format = {
      'terrain.bin': 'SFH1', 'buildings.bin': 'SFB2', 'roads.bin': 'SFR1',
      'trees.bin': 'SFT1', 'lakes.bin': 'SFL1', 'vacancy.bin': 'SFV1',
    }[name ?? ''];
    if (!format) {
      await route.abort();
      return;
    }
    const buffer = Buffer.alloc(name === 'terrain.bin' ? 24 : name === 'vacancy.bin' ? 8 : 12);
    buffer.write(format);
    if (name === 'terrain.bin') {
      buffer.writeUInt32LE(2, 4);
      buffer.writeFloatLE(19200, 8);
      buffer.writeFloatLE(9600, 12);
    } else if (name !== 'vacancy.bin') {
      buffer.writeFloatLE(0.25, 8);
    }
    await route.fulfill({ contentType: 'application/octet-stream', body: buffer });
  });
  await page.goto('/');
  await page.waitForFunction(() => Boolean((window as any).__sf));
  const [lon, lat] = firstParcel.properties.representative_point.coordinates;
  const viewport = page.viewportSize()!;
  await page.evaluate(([lon, lat]) => {
    const viewer = (window as any).__sf;
    viewer.jump(lon, lat, 300, 'topdown');
    viewer.cam.update(10);
    viewer.cam.getActiveCamera().updateMatrixWorld();
  }, [lon, lat]);
  await page.mouse.click(viewport.width / 2, viewport.height / 2);
  const results = page.getByTestId('comparison-results');
  await expect(results).toHaveAttribute('data-parcel-id', firstParcel.id);
  await expect(results).toHaveAttribute('data-status', 'ready');

  const metrics = page.getByTestId('raw-metrics-clinic');
  await expect(metrics).toContainText(/Local sales tax \/ year.*\$.*P10–P90/i);
  await expect(metrics).toContainText(/Operating jobs \(FTE\).*P10–P90/i);
  await expect(metrics).toContainText(/Predicted Google reviews.*Demo estimate/i);
  await page.getByTestId('candidate-clinic').click();
  await expect(page.getByTestId('massing-preview')).toContainText(/Illustrative 7 m building/i);
  expect(await page.evaluate(() =>
    (window as any).__sf.scene.getObjectByName('parcel-scenario').children.some((child: any) => child.isMesh)
  )).toBe(true);
});

test('keyboard parcel selection', async ({ page }) => {
  await page.goto('/');
  const selector = page.getByTestId('parcel-selector');
  await expect(selector).toBeEnabled();
  await selector.focus();
  await selector.press('Home');
  if (!(await selector.inputValue())) await selector.press('ArrowDown');
  await selector.press('Enter');
  await expect(page.getByTestId('comparison-results')).toHaveAttribute('data-parcel-id', await selector.inputValue());
  await expect(page.getByTestId('comparison-results')).toHaveAttribute('data-status', 'ready');
});

test('stale snapshot label', async ({ page }) => {
  await page.route('**/api/v1/parcels**', async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.source.freshness = 'stale';
    await route.fulfill({ response, json: data });
  });
  await page.route('**/api/v1/scenarios/compare', async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.parcel_source.freshness = 'stale';
    await route.fulfill({ response, json: data });
  });
  await openParcel(page);
  await expect(page.getByTestId('data-freshness')).toContainText(/stale|last verified|cached snapshot/i);
});

test('unavailable API preserves usable controls', async ({ page }) => {
  await page.route('**/api/v1/scenarios/compare', route => route.fulfill({ status: 503, json: {
    error: { code: 'artifacts_unavailable', message: 'Required scoring artifacts unavailable' },
  } }));
  await page.goto('/');
  const selector = page.getByTestId('parcel-selector');
  await expect(selector).toBeEnabled();
  const value = await selector.locator('option').evaluateAll(elements => elements.map(el => (el as HTMLOptionElement).value).find(Boolean));
  expect(value).toBeTruthy();
  await selector.selectOption(value!);
  await expect(page.getByRole('alert')).toContainText(/unavailable|failed|try again/i);
  await expect(selector).toBeEnabled();
  await expect(page.getByTestId('comparison-results')).not.toHaveAttribute('data-status', 'ready');
  await page.getByLabel('Proposed use').selectOption('grocery');
  const foodReview = page.getByRole('link', { name: 'Public Health plan review' });
  await expect(foodReview).toHaveAttribute('href', 'https://www.sf.gov/check-if-your-project-requires-health-plan-review');
  await expect(page.getByLabel('Gross floor area (sq ft)')).toBeVisible();
});

test('late comparison cannot replace current selection', async ({ page }) => {
  await stubParcelList(page);
  const ids = parcelFixture.map((p: any) => p.id);
  await page.route('**/api/v1/parcels/*', route => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-1)!);
    return route.fulfill({ json: { ...parcelFixture.find((p: any) => p.id === id), source: parcelCollection.source, scoring_available: true, scoring_reason: null } });
  });
  let releaseFirst!: () => void;
  const firstGate = new Promise<void>(resolve => { releaseFirst = resolve; });
  let firstArrived!: () => void;
  const firstRequest = new Promise<void>(resolve => { firstArrived = resolve; });
  let firstHandled!: () => void;
  const firstFinished = new Promise<void>(resolve => { firstHandled = resolve; });
  await page.route('**/api/v1/scenarios/compare', async route => {
    const body = route.request().postDataJSON();
    if (body.parcel_id === ids[0]) { firstArrived(); await firstGate; }
    try { await route.fulfill({ json: { ...comparisonFixture, parcel_id: body.parcel_id } }); }
    catch (error) { if (!page.isClosed() && !/closed|cancel|abort/i.test(String(error))) throw error; }
    finally { if (body.parcel_id === ids[0]) firstHandled(); }
  });
  await page.goto('/');
  const selector = page.getByTestId('parcel-selector');
  await selector.selectOption(ids[0]);
  await firstRequest;
  await selector.selectOption(ids[1]);
  await expect(page.getByTestId('comparison-results')).toHaveAttribute('data-parcel-id', ids[1]);
  releaseFirst();
  await firstFinished;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(page.getByTestId('comparison-results')).toHaveAttribute('data-status', 'ready');
  await page.getByTestId('candidate-clinic').click();
  await expect(page.getByTestId('comparison-results')).toHaveAttribute('data-parcel-id', ids[1]);
});

test('missing basemap preserves list and comparison', async ({ page }) => {
  await page.route('**/*', route => {
    const url = route.request().url();
    if (/\.pbf(?:\?|$)|\.mvt(?:\?|$)|tiles\.|tile\.|basemap|style\.json/.test(url) && !url.includes('/api/v1/')) return route.abort('failed');
    return route.continue();
  });
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(this: HTMLCanvasElement, type: string, ...args: any[]) {
      if (/webgl/.test(type)) return null;
      return (original as any).call(this, type, ...args);
    } as typeof original;
  });
  await openParcel(page);
  await checkLayout(page);
});

test('invalid geometry uses outline fallback', async ({ page }) => {
  await page.route('**/api/v1/scenarios/compare', async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.massing.geometry = null;
    data.massing.reason = 'invalid_geometry';
    await route.fulfill({ response, json: data });
  });
  await openParcel(page);
  await page.getByTestId('candidate-clinic').click();
  await expect(page.getByTestId('massing-preview')).toHaveAttribute('data-kind', 'outline');
  await expect(page.getByTestId('comparison-results')).toHaveAttribute('data-status', 'ready');
});

test('unavailable metric shows reason and raw values', async ({ page }) => {
  await page.route('**/api/v1/scenarios/compare', async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.active_dimensions = ['need', 'sustainability'];
    data.unavailable_dimensions = ['economic'];
    data.effective_weights = { need: .5, economic: 0, sustainability: .5 };
    for (const row of data.alternatives) {
      row.normalized.economic = null;
      row.normalized.operating_fte = null;
      row.economic.operating_fte = null;
      row.economic.reason = 'missing_economic_input';
      row.total = (row.normalized.need + row.normalized.sustainability) / 2;
    }
    data.alternatives.sort((a: any, b: any) => b.total - a.total || a.category_id.localeCompare(b.category_id));
    data.alternatives.forEach((a: any, index: number) => { a.rank = index + 1; });
    for (const row of data.alternatives) row.pareto = !data.alternatives.some((other: any) =>
      other.normalized.need >= row.normalized.need && other.normalized.sustainability >= row.normalized.sustainability &&
      (other.normalized.need > row.normalized.need || other.normalized.sustainability > row.normalized.sustainability));
    await route.fulfill({ response, json: data });
  });
  await openParcel(page);
  await expect(page.getByTestId('unavailable-reason')).toContainText(/economic|missing|unavailable/i);
  await expect(page.getByTestId('raw-metrics-clinic')).toBeVisible();
  for (const category of candidates) await expect(page.getByTestId(`alternative-${category}`)).toBeVisible();
});
