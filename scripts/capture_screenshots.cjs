#!/usr/bin/env node
/* Render the isolated fictional demo in a fresh Chromium profile. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {spawnSync} = require('node:child_process');

const root = path.resolve(__dirname, '..');
const output = path.join(root, 'docs/assets/screenshots');
const previews = path.join(output, 'previews');
const demo = path.join(root, '.local/demo/工具/求职进度面板/dashboard.html');
const python = process.env.PYTHON || 'python3';
let chromium;
try {
  ({chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright'));
} catch (error) {
  throw new Error('Screenshot capture requires an optional Playwright installation. Set PLAYWRIGHT_MODULE to its module path, or install Playwright in a development environment.', {cause: error});
}

async function main() {
  const built = spawnSync(python, ['-B', 'scripts/demo.py'], {cwd: root, stdio: 'inherit'});
  if (built.status !== 0) throw new Error('Synthetic demo generation failed.');
  fs.mkdirSync(output, {recursive: true});
  fs.mkdirSync(previews, {recursive: true});
  const browser = await chromium.launch({headless: true, ...(process.env.CHROMIUM_EXECUTABLE ? {executablePath: process.env.CHROMIUM_EXECUTABLE} : {})});
  const context = await browser.newContext({viewport: {width: 1440, height: 1060}, deviceScaleFactor: 1, colorScheme: 'light', locale: 'en-US', timezoneId: 'America/Chicago', reducedMotion: 'reduce', serviceWorkers: 'block'});
  const attempted = [], errors = [];
  await context.route('**/*', route => {
    const url = route.request().url();
    if (url.startsWith('file:') || url.startsWith('about:') || url.startsWith('data:')) return route.continue();
    attempted.push(url); return route.abort();
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(pathToFileURL(demo).href, {waitUntil: 'load'});
    await page.locator('#tabs [data-tab="today"]').waitFor();
    await page.evaluate(() => document.fonts.ready);
    async function tab(name) {
      await page.locator(`#tabs [data-tab="${name}"]`).click();
      await page.locator(`#tab-${name}`).waitFor({state: 'visible'});
      await page.evaluate(() => window.scrollTo({top: 0, behavior: 'instant'}));
      await page.waitForFunction(() => window.scrollY === 0);
    }
    async function shot(name, locator = null) {
      const options = {path: path.join(output, name + '.png'), animations: 'disabled'};
      if (locator) {
        await locator.screenshot(options);
        fs.copyFileSync(options.path, path.join(previews, name + '.png'));
      } else {
        await page.screenshot({...options, fullPage: true});
        const size = page.viewportSize();
        await page.setViewportSize(size.width < 600 ? {width: 390, height: 844} : {width: 1440, height: 1060});
        await page.evaluate(() => window.scrollTo({top: 0, behavior: 'instant'}));
        await page.waitForFunction(() => window.scrollY === 0);
        await page.screenshot({path: path.join(previews, name + '.png'), animations: 'disabled'});
        await page.setViewportSize(size);
      }
      console.log('Captured ' + name + '.png');
    }
    await tab('today'); await shot('01-today');
    await tab('discovered');
    assert.ok(await page.locator('#tab-discovered').innerText().then(text => text.includes('Example Imaging')), 'discovery page must include a fictional new role');
    await page.setViewportSize({width: 1440, height: 700});
    await shot('02-discovered');
    await page.setViewportSize({width: 1440, height: 1060});
    await tab('todo');
    const board = page.frameLocator('#todo-frame');
    await board.locator('.card').first().waitFor({state: 'visible'});
    // The production iframe scrolls independently. A tall browser viewport
    // captures the complete fictional board without changing its CSS or data.
    const boardHeight = await board.locator('body').evaluate(body => body.scrollHeight);
    await page.setViewportSize({width: 1440, height: boardHeight + 160});
    await shot('03-opportunities');
    await page.setViewportSize({width: 1440, height: 1060});
    await tab('internships'); await shot('04-internships');
    await tab('progress');
    for (const [index, name] of ['current', 'records', 'statistics'].entries()) {
      await page.locator('#progress-nav button').nth(index).click();
      await page.locator(`#tab-progress[data-view="${name}"]`).waitFor({state: 'visible'});
      await page.evaluate(() => window.scrollTo({top: 0, behavior: 'instant'}));
      await page.waitForFunction(() => window.scrollY === 0);
      await shot('0' + (index + 5) + '-progress-' + name);
    }
    await page.setViewportSize({width: 1440, height: 1600});
    await shot('08-application-flow', page.locator('#application-flow'));
    await page.setViewportSize({width: 1440, height: 1060});
    await page.locator('#global-q').fill('DEMO-01');
    await page.locator('#global-results button').first().click();
    await page.locator('#detail-drawer[open]').waitFor({state: 'visible'});
    await page.evaluate(() => window.scrollTo({top: 0, behavior: 'instant'}));
    await page.waitForFunction(() => window.scrollY === 0);
    await page.screenshot({path: path.join(output, '09-detail-drawer.png'), animations: 'disabled'});
    fs.copyFileSync(path.join(output, '09-detail-drawer.png'), path.join(previews, '09-detail-drawer.png'));
    console.log('Captured 09-detail-drawer.png');
    await page.locator('#drawer-close').click();
    await page.locator('#global-q').fill('');
    await tab('today');
    for (let count = 0; count < 3 && await page.locator('html').getAttribute('data-theme') !== 'dark'; count++) await page.locator('#theme').click();
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    await shot('10-dark-mode');
    await page.locator('#oa-shortcut').click();
    await page.locator('#oa-workspace').waitFor({state: 'visible'});
    await page.locator('#oa-workspace').evaluate(node => window.scrollTo({top: window.scrollY + node.getBoundingClientRect().top - 96, behavior: 'instant'}));
    await page.waitForFunction(() => document.querySelector('#oa-workspace').getBoundingClientRect().top >= 90);
    await shot('11-oa-workspace', page.locator('#oa-workspace'));
    for (let count = 0; count < 3 && await page.locator('html').getAttribute('data-theme') !== 'light'; count++) await page.locator('#theme').click();
    await page.setViewportSize({width: 390, height: 844});
    await tab('today');
    await shot('12-mobile-today');
    assert.deepEqual(errors, [], 'screenshots must render without uncaught page errors');
    assert.deepEqual(attempted, [], 'demo must not attempt external resource requests');
    console.log('Captured 12 real browser screenshots from fictional data; no external requests or page errors.');
  } finally {
    await context.close(); await browser.close();
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
