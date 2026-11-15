import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

// The primary UI is now tract-first; manual parcel-picker flows were retired.
// Run against a real release with the supplied tract index built (docs/tract-planning.md).
const categories = ['bank_credit_union', 'clinic', 'grocery', 'pharmacy', 'school'];
const defaultTract = '06075061501';
const permitFixture = JSON.parse(readFileSync(new URL('../../../contracts/examples/permit-checklist.json', import.meta.url), 'utf8'));

test.beforeEach(async ({ page }) => {
  // Permit-rule tests belong to backend/tests. Keep UI runs independent of live map latency.
  await page.route('**/api/v1/permits/check', route => {
    const body = route.request().postDataJSON();
    return route.fulfill({ json: { ...permitFixture, parcel_id: body.parcel_id, category_id: body.category_id } });
  });
});

async function openTract(page: Page, category = 'grocery') {
  // These flows exercise planning for a returning visitor; first-visit onboarding is checked separately.
  await page.addInitScript(() => localStorage.setItem('sf-sim-getting-started-seen', '1'));
  await page.goto('/');
  await expect(page).toHaveTitle('Tract Planner');
  const area = page.getByTestId('area-selector');
  await expect(area).toBeVisible({ timeout:15000 });
  await expect(area).toBeEnabled({ timeout:15000 });
  if (await area.inputValue() !== defaultTract) await area.selectOption(defaultTract);
  await expect(page.getByTestId('site-selector')).toBeEnabled({ timeout: 15000 });
  await page.getByTestId(`area-use-${category}`).click();
  const results = page.getByTestId('comparison-results');
  await expect(results).toHaveAttribute('data-status', 'ready');
  await expect(page.locator('[data-permit-use]')).toHaveValue(category);
  const site = await page.getByTestId('site-selector').inputValue();
  await expect(results).toHaveAttribute('data-parcel-id', site);
  return site;
}

async function details(page: Page) {
  const panel = page.getByTestId('tract-details');
  if (!await panel.evaluate((el: HTMLDetailsElement) => el.open)) await panel.locator('summary').click();
  return panel;
}

async function meshHeight(page: Page) {
  return page.evaluate(() => {
    const group = (window as any).__sf.scene.getObjectByName('parcel-scenario');
    const mesh = group.children.find((child: any) => child.isMesh);
    const positions = mesh.geometry.attributes.position;
    let low = Infinity, high = -Infinity;
    for (let i=0; i<positions.count; i++) { low = Math.min(low, positions.getY(i)); high = Math.max(high, positions.getY(i)); }
    return high-low;
  });
}

test('tract-first screen uses plain language, one score and one details disclosure', async ({ page }) => {
  await openTract(page, 'school');
  await expect(page.getByTestId('tract-recommendation')).toHaveText('Schools are the biggest need in this area.');
  await expect(page.getByTestId('site-score')).toHaveText(/^\d+\/100$/);
  await expect(page.getByTestId('selected-site-fit')).toContainText('Local access data');
  await expect(page.getByText('Ranking weights', { exact:true })).toHaveCount(0);
  await expect(page.getByTestId('parcel-selector')).toBeHidden();
  await expect(page.getByTestId('tract-details')).not.toHaveAttribute('open');
  await expect(page.locator('.planner details')).toHaveCount(1);
  await expect(page.getByRole('complementary', { name:'Tract Planner', exact:true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('best-three dropdown matches the real ranking and changing it moves the selected pin', async ({ page }) => {
  await openTract(page);
  const response = await page.request.get(`/api/v1/planning/areas/${defaultTract}/recommendations`);
  expect(response.ok()).toBe(true);
  const recommendations = await response.json();
  const expected = recommendations.rankings.grocery.sites.filter((site: any) => site.rank != null);
  expect(expected.length).toBe(3);
  const selector = page.getByTestId('site-selector');
  const values = await selector.locator('option:not([value="__pick_own__"])').evaluateAll(elements => elements.map(el => (el as HTMLOptionElement).value));
  expect(values).toEqual(expected.map((site: any) => site.parcel_id));
  await expect(selector.locator('option')).toHaveText(['Best Match','Second Best Match','Third Best Match','Choose Your Own Vacant Lot…']);
  await selector.selectOption(values[1]);
  await expect(page.getByTestId('comparison-results')).toHaveAttribute('data-parcel-id', values[1]);
  await page.waitForFunction(() => Boolean((window as any).__sf));
  await expect.poll(() => page.evaluate(() => (window as any).__sf.scene.getObjectByName('tract-site-planning').children
    .find((child: any) => child.userData.categoryId === 'grocery')?.userData.parcelId)).toBe(values[1]);
  await expect(page.getByTestId('site-score')).toHaveText(`${Math.round(expected[1].score)}/100`);
});

test('Tract 612 uses consistent need labels and orders building types by their displayed scores', async ({ page }) => {
  await openTract(page);
  const response = page.waitForResponse(res => res.url().includes('/planning/areas/06075061200/recommendations') && res.status() === 200);
  await page.getByTestId('area-selector').selectOption('06075061200');
  const data = await (await response).json();
  await expect(page.getByTestId('site-selector')).toBeEnabled({timeout:15000});
  const clinic = data.rankings.clinic.sites[0];
  const bank = data.rankings.bank_credit_union.sites[0];
  expect(clinic.score).toBeGreaterThan(bank.score);
  expect(clinic.need_level).toBe('High need');
  expect(bank.score).toBeLessThanOrEqual(15);
  expect(bank.need_level).toBe('No clear need');
  const order = await page.getByTestId('area-needs').locator('button').evaluateAll(elements =>
    elements.map(el => el.getAttribute('data-testid')!.replace('area-use-','')));
  const scores = order.map(category => data.rankings[category].sites.find((s:any)=>s.rank != null)?.score ?? -1);
  expect(scores).toEqual([...scores].sort((a,b)=>b-a));
  await page.getByTestId('area-use-clinic').click();
  await expect(page.getByTestId('site-score')).toHaveText(`${clinic.score}/100`);
  await expect(page.getByTestId('area-use-clinic')).toContainText('High need');
});

test('map has pins one through five and a bold selected tract boundary', async ({ page }) => {
  await openTract(page);
  await page.waitForFunction(() => Boolean((window as any).__sf));
  const evidence = await page.evaluate(() => {
    const children = (window as any).__sf.scene.getObjectByName('tract-site-planning').children;
    return { pins:children.filter((child: any) => child.userData.rankMarker).map((child: any) => child.userData),
      arrows:children.filter((child: any) => child.userData.siteArrow).map((child: any) => child.userData),
      boundary:children.some((child: any) => child.isLine2 && child.material.linewidth >= 4) };
  });
  expect(evidence.pins.map((pin: any) => pin.rank).sort()).toEqual([1,2,3,4,5]);
  expect(evidence.pins.map((pin: any) => pin.categoryId).sort()).toEqual([...categories].sort());
  expect(evidence.pins.filter((pin: any) => pin.selected)).toHaveLength(1);
  expect(evidence.boundary).toBe(true);
  expect(evidence.arrows.map((arrow: any) => arrow.rank).sort()).toEqual([1,2,3,4,5]);
});

test('height slider starts at the suggestion and changes the actual 3D volume', async ({ page }) => {
  const id = await openTract(page);
  await page.waitForFunction(() => Boolean((window as any).__sf));
  const plan = await (await page.request.get(`/api/v1/planning/sites/${encodeURIComponent(id)}`)).json();
  const slider = page.getByTestId('proposal-height');
  await slider.scrollIntoViewIfNeeded();
  await expect(slider).toHaveValue(String(plan.designs.grocery.height_m));
  const original = await meshHeight(page);
  const suggestion = page.getByTestId('suggested-height-mark');
  const originalMark = await suggestion.getAttribute('style');
  await slider.focus();
  for (let i=0;i<4;i++) await slider.press('ArrowRight');
  await expect(slider).toHaveValue(String(plan.designs.grocery.height_m + 2));
  await expect.poll(async () => (await meshHeight(page)) - original).toBeGreaterThan(1.98);
  await expect(page.getByTestId('suggested-height')).toContainText(`Suggested: ${plan.designs.grocery.height_m} m`);
  await expect(suggestion).toBeVisible();
  await expect(suggestion).toHaveAttribute('style', originalMark!);
  await expect(suggestion).toHaveAttribute('data-suggested-height', String(plan.designs.grocery.height_m));
});

test('only-vacant view filters buildings and keeps suggestions and pins available', async ({ page }) => {
  await openTract(page);
  await page.waitForFunction(() => Boolean((window as any).__sf));
  const toggle = page.getByTestId('only-vacant-view');
  await toggle.check();
  const filtered = await page.evaluate(() => {
    const viewer = (window as any).__sf;
    const index = viewer.buildingIndex;
    return { only:viewer.shared.uOnlyVacant.value,
      pins:viewer.scene.getObjectByName('tract-site-planning').children.filter((c:any)=>c.userData.rankMarker).length,
      concept:viewer.scene.getObjectByName('parcel-scenario').children.some((c:any)=>c.isMesh),
      hasFilter:typeof index.skip === 'function' };
  });
  expect(filtered).toEqual({only:1,pins:5,concept:true,hasFilter:true});
  await toggle.uncheck();
  expect(await page.evaluate(() => (window as any).__sf.shared.uOnlyVacant.value)).toBe(0);
  await expect(page.getByTestId('site-selector')).toBeEnabled();
});

test('Tract Planner drawer opens and closes without changing the selected tract', async ({ page }) => {
  await openTract(page);
  await page.waitForFunction(() => Boolean((window as any).__sf));
  const selected = await page.getByTestId('area-selector').inputValue();
  const tab = page.locator('.drawer.right .drawer-tab');
  await tab.click();
  await expect(tab).toHaveAttribute('aria-expanded','false');
  await expect(page.getByTestId('area-selector')).not.toBeInViewport();
  await tab.click();
  await expect(tab).toHaveAttribute('aria-expanded','true');
  await expect(page.getByTestId('area-selector')).toBeInViewport();
  await expect(page.getByTestId('area-selector')).toHaveValue(selected);
  await page.reload();
  await page.waitForFunction(() => Boolean((window as any).__sf));
  await expect(page.getByTestId('area-selector')).toBeVisible({ timeout:15000 });
  await expect(tab).toHaveAttribute('aria-expanded','true');
});

test('keyboard site selection works and changing use restores the new suggested size', async ({ page }) => {
  await openTract(page);
  const selector = page.getByTestId('site-selector');
  await selector.focus();
  await selector.press('Home');
  await selector.press('ArrowDown');
  await selector.press('Enter');
  await expect(page.getByTestId('comparison-results')).toHaveAttribute('data-parcel-id', await selector.inputValue());
  await page.getByTestId('area-use-school').click();
  await expect(page.locator('[data-permit-use]')).toHaveValue('school');
  const id = await selector.inputValue();
  const plan = await (await page.request.get(`/api/v1/planning/sites/${encodeURIComponent(id)}`)).json();
  await expect(page.getByTestId('proposal-height')).toHaveValue(String(plan.designs.school.height_m));
});

test('details retain financial estimates and pass suggested grocery floor area to the existing permit check', async ({ page }) => {
  const requests: any[] = [];
  page.on('request', req => { if (req.url().includes('/permits/check')) requests.push(req.postDataJSON()); });
  await openTract(page);
  await details(page);
  await expect(page.getByTestId('raw-metrics-grocery')).toContainText(/Local sales tax \/ year.*P10–P90/);
  await expect(page.getByTestId('raw-metrics-grocery')).toContainText(/Operating jobs \(FTE\)/);
  expect(requests.some(body => body.category_id === 'grocery' && body.floor_area_sqft > 0)).toBe(true);
  await expect(page.getByTestId('data-freshness')).toContainText(/snapshot|cached|Live|Demo/i);
});

test('missing tract data gives a readable retry state', async ({ page }) => {
  await page.route('**/api/v1/planning/areas', route => route.fulfill({ status:503, json:{error:{message:'Area data is temporarily unavailable.'}} }));
  await page.goto('/');
  await expect(page.getByTestId('site-ranking-status')).toContainText('temporarily unavailable');
  await expect(page.getByTestId('area-selector')).toBeDisabled();
  await expect(page.getByRole('button', { name:'Try again', exact:true })).toBeVisible();
});

test('tracts with no suitable sites keep the area and type choices usable', async ({ page }) => {
  await openTract(page);
  await page.getByTestId('area-selector').selectOption('06075980200');
  await expect(page.getByTestId('site-selector')).toBeEnabled();
  await expect(page.getByTestId('site-ranking-status')).toContainText(/No vacant lot/);
  await expect(page.getByTestId('area-selector')).toBeEnabled();
  await expect(page.getByTestId('area-use-grocery')).toBeEnabled();
  await expect(page.getByTestId('proposal-height')).toBeHidden();
});

test('choosing a custom lot pulses only compatible vacancies and clears them on cancel or a type change', async ({ page }) => {
  await openTract(page);
  await page.waitForFunction(() => Boolean((window as any).__sf));
  const response = await page.request.get(`/api/v1/planning/areas/${defaultTract}/available-sites?category=grocery`);
  const data = await response.json();
  // Zoom into the tract before choosing a lot; entering selection must retain that framing.
  await expect(page.locator('.loader')).toHaveCount(0, { timeout: 30000 });
  await page.mouse.move(600, 400);
  await page.mouse.wheel(0, -400);
  await page.waitForTimeout(1200);
  const framing = () => page.evaluate(() => {
    const camera = (window as any).__sf.cam.getActiveCamera();
    return { zoom: camera.zoom, position: camera.position.toArray() };
  });
  const beforePick = await framing();
  await page.getByTestId('site-selector').selectOption('__pick_own__');
  await expect(page.getByTestId('site-pick-notice')).toContainText('blinking');
  await expect.poll(() => page.evaluate(() => (window as any).__sf.scene.getObjectByName('available-vacant-sites').userData.count)).toBe(data.features.length);
  const highlighted = await page.evaluate(() => (window as any).__sf.scene.getObjectByName('available-vacant-sites').children.filter((x: any) => x.isMesh).map((x: any) => x.userData.parcelId));
  expect([...new Set(highlighted)].sort()).toEqual(data.features.map((x: any) => x.id).sort());
  const firstOpacity = await page.evaluate(() => (window as any).__sf.scene.getObjectByName('available-vacant-sites').children.find((x: any) => x.isMesh).material.opacity);
  await expect.poll(() => page.evaluate(() => (window as any).__sf.scene.getObjectByName('available-vacant-sites').children.find((x: any) => x.isMesh).material.opacity)).not.toBe(firstOpacity);
  const duringPick = await framing();
  expect(duringPick.zoom).toBeCloseTo(beforePick.zoom, 5);
  duringPick.position.forEach((value: number, index: number) => expect(value).toBeCloseTo(beforePick.position[index], 2));
  await page.getByTestId('cancel-site-pick').click();
  await expect(page.getByTestId('site-pick-notice')).toBeHidden();
  expect(await page.evaluate(() => (window as any).__sf.scene.getObjectByName('available-vacant-sites').children.length)).toBe(0);
  await page.getByTestId('site-selector').selectOption('__pick_own__');
  await expect(page.getByTestId('site-pick-notice')).toContainText('blinking');
  await page.getByTestId('area-use-clinic').click();
  await expect(page.getByTestId('site-pick-notice')).toBeHidden();
  expect(await page.evaluate(() => (window as any).__sf.scene.getObjectByName('available-vacant-sites').children.length)).toBe(0);
});

test('empty vacant land supports building sketches and a tract shortcut with the planner closed', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('sf-sim-getting-started-seen', '1');
    localStorage.setItem('sf-sim-drawer:planner', '0');
    localStorage.setItem('sf-sim-drawer:map-key', '0');
  });
  await page.goto('/');
  await page.waitForFunction(() => Boolean((window as any).__sf));
  await expect(page.locator('.loader')).toHaveCount(0, { timeout: 30000 });
  const response = await page.request.get('/api/v1/parcels/sf%3A5338053');
  expect(response.ok()).toBe(true);
  const feature = await response.json();
  const [lon, lat] = feature.properties.representative_point.coordinates;
  await page.evaluate(({lon, lat}) => (window as any).__sf.jump(lon, lat, 180, 'topdown'), {lon, lat});
  await page.waitForTimeout(1200);
  const viewport = page.viewportSize()!;
  await page.mouse.click(viewport.width / 2, viewport.height / 2);
  const card = page.getByRole('dialog', { name: 'Building details' });
  await expect(card).toContainText('Vacant land', { timeout: 15000 });
  await expect(page.locator('.drawer.right .drawer-tab')).toHaveAttribute('aria-expanded', 'false');
  await expect(card.locator('[data-type]')).toBeVisible();
  await card.locator('[data-hnum]').fill('15');
  await card.locator('[data-apply]').click();
  const sketchCount = () => page.evaluate(() => (window as any).__sf.scene.getObjectByName('vacant-lot-sketches').children.length);
  await expect.poll(sketchCount).toBe(1);
  await expect(card).toContainText('Edited');
  await card.getByTestId('lot-effects').locator('summary').click();
  await expect(card.getByTestId('lot-effects')).toContainText('Operating jobs (FTE)', { timeout: 15000 });
  await expect(card.getByTestId('lot-effects')).toContainText('Local sales tax / year');
  await expect(card.getByTestId('lot-effects')).toContainText('Local access data');
  await expect(page.locator('.drawer.right .drawer-tab')).toHaveAttribute('aria-expanded', 'false');
  await card.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(card).toBeHidden();
  await page.mouse.click(viewport.width / 2, viewport.height / 2);
  await expect(card).toBeVisible();
  await expect(card).toContainText('Edited');
  await card.locator('[data-delete]').click();
  await expect.poll(sketchCount).toBe(0);
  await card.getByTestId('jump-to-tract').click();
  await expect(page.locator('.drawer.right .drawer-tab')).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByTestId('area-selector')).toHaveValue('06075061200');
});

test('Getting Started opens on the first visit, remembers it, and reopens from the top-left title', async ({ page }) => {
  await page.goto('/');
  const guide = page.getByTestId('getting-started-dialog');
  await expect(guide).toBeVisible({ timeout: 30000 });
  await expect(guide).toHaveAttribute('data-step', '1');
  await guide.getByRole('button', { name: 'Close Getting Started' }).click();
  await expect(guide).not.toBeVisible();
  await page.reload();
  const trigger = page.locator('.title').getByTestId('getting-started');
  await expect(trigger).toBeVisible({ timeout: 30000 });
  await expect(guide).not.toBeVisible();
  await trigger.click();
  await expect(guide).toBeVisible();
  await expect(guide).toHaveAttribute('data-step', '1');
});

test('Getting Started is a three-step modal with control-free looping clips and keyboard navigation', async ({ page }) => {
  await openTract(page);
  await page.waitForFunction(() => Boolean((window as any).__sf));
  await page.getByTestId('getting-started').click();
  const guide = page.getByTestId('getting-started-dialog');
  await expect(guide).toBeVisible();
  for (let step=1; step<=3; step++) {
    await expect(guide).toHaveAttribute('data-step',String(step));
    await expect(guide.locator('h2')).not.toBeEmpty();
    await expect.poll(() => guide.locator('video').evaluate((video: HTMLVideoElement) => video.readyState)).toBeGreaterThanOrEqual(2);
    expect(await guide.locator('video').evaluate((v: HTMLVideoElement) => ({loop:v.loop,muted:v.muted,playing:!v.paused,controls:v.controls}))).toEqual({loop:true,muted:true,playing:true,controls:false});
    if (step<3) await guide.getByRole('button',{name:'Next step',exact:true}).click();
  }
  await guide.getByRole('button',{name:'Previous step',exact:true}).click();
  await expect(guide).toHaveAttribute('data-step','2');
  await page.keyboard.press('ArrowRight');
  await expect(guide).toHaveAttribute('data-step','3');
  await guide.getByRole('button',{name:'Start planning',exact:true}).click();
  await expect(guide).not.toBeVisible();
  await expect(page.getByTestId('getting-started')).toBeFocused();
});

test('WebGL failure preserves tract choices and the 2D footprint', async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(this: HTMLCanvasElement, type: string, ...args: any[]) {
      return /webgl/.test(type) ? null : (original as any).call(this,type,...args);
    } as typeof original;
  });
  await openTract(page);
  await details(page);
  await expect(page.getByTestId('view-2d')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('[data-footprint] svg')).toBeVisible();
  await expect(page.getByTestId('area-selector')).toBeEnabled();
});

test('missing comparison estimates preserve the ranked-site controls and size preview', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('sf-sim-getting-started-seen', '1'));
  await page.route('**/api/v1/scenarios/compare', route => route.fulfill({status:503,json:{error:{message:'Site estimates unavailable.'}}}));
  await page.goto('/');
  await expect(page.getByTestId('site-selector')).toBeEnabled({timeout:15000});
  await expect(page.getByTestId('proposal-height')).toBeVisible();
  await details(page);
  await expect(page.getByRole('alert')).toContainText('Site estimates unavailable');
  await expect(page.getByTestId('area-selector')).toBeEnabled();
});
