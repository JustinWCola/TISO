import assert from 'node:assert/strict'
import { chromium } from 'playwright-core'

const base = process.env.TISO_TEST_URL ?? 'http://127.0.0.1:8080'
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH ?? '/usr/bin/chromium',
})
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  const pageErrors = []
  const externalRequests = []
  page.on('pageerror', error => pageErrors.push(error.message))
  await page.route('**/*', route => {
    if (new URL(route.request().url()).origin === new URL(base).origin) return route.continue()
    externalRequests.push(route.request().url())
    return route.abort()
  })
  await page.route('**/api/v1/settings/amap', route => route.fulfill({
    json: { js_key: '', routing_configured: false, web_service_saved: false },
  }))
  await page.goto(base)
  assert.equal(await page.title(), 'TISO · 交通可达性')
  assert.equal(await page.locator('html').evaluate(root => getComputedStyle(root).colorScheme), 'light')
  const generate = page.getByRole('button', { name: '生成等时圈' })
  assert.equal(await page.locator('canvas').count(), 0)
  await page.getByRole('button', { name: '打开高德设置', exact: true }).click()
  await page.getByLabel('Web 端 JS Key', { exact: true }).waitFor()
  assert.equal(await page.getByLabel('JS API 安全密钥', { exact: true }).getAttribute('type'), 'password')
  assert.equal(await page.getByLabel('Web 服务 Key', { exact: true }).getAttribute('type'), 'password')
  await page.keyboard.press('Escape')
  await page.getByLabel('Web 端 JS Key', { exact: true }).waitFor({ state: 'hidden' })
  const analysis = page.locator('#analysis-toggle')
  assert.equal(await analysis.getAttribute('aria-expanded'), 'false')
  assert.equal(await generate.isVisible(), false)
  const collapsed = await analysis.boundingBox()
  const radiusBefore = await analysis.evaluate(button => getComputedStyle(button.parentElement).borderRadius)
  await analysis.click()
  const expanded = await analysis.boundingBox()
  assert.equal(expanded.x, collapsed.x)
  assert.equal(expanded.y, collapsed.y)
  assert.equal(await analysis.evaluate(button => getComputedStyle(button.parentElement).borderRadius), radiusBefore)
  assert.equal(await page.locator('#analysis-controls').evaluate(panel => getComputedStyle(panel).animationName), 'menu-unfold')
  assert.equal(await analysis.getAttribute('aria-expanded'), 'true')
  await analysis.click()
  await page.locator('#analysis-controls').waitFor({ state: 'hidden' })
  assert.equal(await generate.isVisible(), false)
  await analysis.click()
  await generate.waitFor()
  assert.ok(await generate.isDisabled())
  const generateBounds = await generate.boundingBox()
  for (const [name, initial, changed] of [
    ['最长耗时', '60 分钟', '65 分钟'], ['等时线间隔', '15 分钟', '20 分钟'],
    ['最小间距', '250 米', '500 米'],
  ]) {
    const slider = page.getByRole('slider', { name })
    assert.ok((await slider.boundingBox()).y < generateBounds.y)
    assert.equal(await slider.getAttribute('aria-valuetext'), initial)
    await slider.press('ArrowRight')
    assert.equal(await slider.getAttribute('aria-valuetext'), changed)
    await slider.press('ArrowLeft')
  }
  assert.equal(await page.getByRole('button', { name: '分析设置', exact: true }).count(), 0)
  assert.equal(await page.getByRole('slider').count(), 3)
  await page.setViewportSize({ width: 390, height: 720 })
  assert.equal(await page.locator('#analysis-controls .dock-content').evaluate(menu => {
    return getComputedStyle(menu).scrollbarWidth
  }), 'none')
  const panel = await page.locator('.analysis-card').boundingBox()
  assert.ok(panel.width < 390 && panel.height < 720)
  assert.deepEqual(externalRequests, [])
  assert.deepEqual(pageErrors, [])

  const configured = await browser.newPage({ viewport: { width: 632, height: 690 } })
  await configured.route('**/*', route => {
    if (new URL(route.request().url()).origin === new URL(base).origin) return route.continue()
    return route.abort()
  })
  const secrets = { js_security_code: 'security-test-fixture', web_service_key: 'web-key-test-fixture' }
  let reveals = 0
  await configured.route('**/api/v1/settings/amap/reveal', route => {
    reveals++
    return route.fulfill({ json: { value: secrets[route.request().postDataJSON().field] } })
  })
  await configured.route('**/api/v1/settings/amap', route => route.fulfill({
    json: { js_key: 'browser-test-fixture', routing_configured: true, web_service_saved: true },
  }))
  await configured.route('https://webapi.amap.com/maps?*', route => route.fulfill({
    contentType: 'application/javascript', body: `
      window.AMap = {
        Map: class {
          constructor(element, options) {
            element.dataset.mapStyle = options.mapStyle;
            element.dataset.sdkInitialized = 'yes';
            this.scene = document.createElement('div');
            const basemap = document.createElement('div');
            basemap.dataset.basemap = 'true';
            basemap.style.height = '1px';
            this.scene.append(basemap);
            element.append(this.scene);
            this.offset = 0;
            this.events = new Map();
            let startX;
            element.addEventListener('pointerdown', event => { startX = event.clientX });
            element.addEventListener('pointermove', event => {
              if (event.buttons === 1 && startX !== undefined) {
                this.offset = event.clientX - startX;
                this.scene.style.transform = 'translateX(' + this.offset + 'px)';
                this.layer?.render();
                this.events.get('mapmove')?.();
              }
            });
          }
          on(name, handler) { this.events.set(name, handler); }
          off(name) { this.events.delete(name); }
          setZoomAndCenter() {} destroy() {}
          addLayer(layer) {
            if (location.search.includes('fixture=raster')) return;
            this.layer = layer;
            this.scene.parentElement.append(layer.canvas);
            layer.render();
          }
          removeLayer(layer) { layer.canvas.remove(); }
          lngLatToContainer([lng, lat]) {
            return { getX: () => 220 + (lng - 121.47) * 6000 + this.offset,
              getY: () => 250 + (31.24 - lat) * 6000 };
          }
        },
        Marker: class { on() {} off() {} setMap() {} setPosition() {} },
        CustomLayer: class {
          constructor(canvas, options) {
            this.canvas = canvas;
            canvas.dataset.followMap = String(options.alwaysRender);
          }
        },
      };
    `,
  }))
  const jobId = 'amap-browser-fixture'
  const done = {
    job_id: jobId, status: 'completed', stage: 'completed', sequence: 1,
    total_points: 21, completed_points: 21, requests_sent: 1, error: null,
  }
  const ring = [[121.47, 31.23], [121.48, 31.23], [121.48, 31.24], [121.47, 31.24], [121.47, 31.23]]
  let keepRunning = false
  const active = { ...done, job_id: jobId + '-running', status: 'running', stage: 'refining', completed_points: 8, preview_ready: true }
  await configured.route('**/api/v1/isochrones/jobs', route => {
    const { destination, ...options } = route.request().postDataJSON()
    assert.ok(destination.lng && destination.lat)
    assert.deepEqual(options, { max_duration_min: 60, contour_interval_min: 15, min_spacing_m: keepRunning ? 500 : 250 })
    return route.fulfill({ status: 202, json: keepRunning ? active : { ...done, status: 'queued' } })
  })
  await configured.route('**/api/v1/isochrones/jobs/' + active.job_id + '/events', route => route.fulfill({
    contentType: 'text/event-stream', body: 'retry: 60000\nevent: progress\ndata: ' + JSON.stringify(active) + '\n\n',
  }))
  const jobPath = '**/api/v1/isochrones/jobs/' + jobId
  await configured.route(jobPath + '/events', route => route.fulfill({
    contentType: 'text/event-stream', body: 'event: completed\ndata: ' + JSON.stringify(done) + '\n\n',
  }))
  const completedResult = {
    job_id: jobId,
    bands: { features: [15, 30, 45, 60].map((maximum, index) => ({
      properties: { minimum_min: index * 15, maximum_min: maximum }, geometry: { coordinates: [[ring]] },
    })) },
    contours: { features: [] },
    samples: [],
    observed_from: '2026-11-25T03:14:27Z',
    observed_to: '2026-11-25T03:14:32Z',
  }
  await configured.route(jobPath + '/result', route => route.fulfill({ json: completedResult }))
  let showPreview
  const previewGate = new Promise(resolve => { showPreview = resolve })
  await configured.route('**/api/v1/isochrones/jobs/' + active.job_id + '/preview', async route => {
    await previewGate
    await route.fulfill({ json: { ...completedResult, job_id: active.job_id, preview: true } })
  })
  const samplesPath = '/isochrones/jobs/' + active.job_id + '/samples'
  await configured.route(url => url.pathname.endsWith(samplesPath), route => {
    const after = Number(new URL(route.request().url()).searchParams.get('after'))
    const point = { coordinates: [121.44, 31.225], duration_s: 720, status: 'ok' }
    const next = after === 0 ? 1 : 8
    return route.fulfill({ json: { samples: Array(next - after).fill(point), next } })
  })
  await configured.goto(base)
  await configured.locator('.amap[data-sdk-initialized="yes"]').waitFor()
  assert.equal(await configured.getByRole('button', { name: '高德底图', exact: true }).count(), 0)
  const amapToggle = configured.locator('#amap-toggle')
  const closedSettings = await amapToggle.boundingBox()
  await amapToggle.click()
  const openSettings = await amapToggle.boundingBox()
  assert.equal(openSettings.x + openSettings.width, closedSettings.x + closedSettings.width)
  assert.equal(openSettings.y, closedSettings.y)
  assert.equal(reveals, 0)
  const webKey = configured.getByLabel('Web 服务 Key', { exact: true })
  assert.equal(await webKey.getAttribute('type'), 'password')
  assert.match(await webKey.getAttribute('placeholder'), /^•+$/)
  assert.equal(await configured.locator('.amap-settings-content fieldset').first().evaluate(fieldset =>
    getComputedStyle(fieldset).borderTopWidth), '0px')
  const settingsActions = await configured.locator('.settings-actions').evaluate(row => {
    const bounds = row.getBoundingClientRect()
    const [save, clear] = [...row.querySelectorAll('button')].map(button => button.getBoundingClientRect())
    return { widthDifference: save.width - clear.width, startGap: save.left - bounds.left, endGap: bounds.right - clear.right }
  })
  assert.ok(Object.values(settingsActions).every(difference => Math.abs(difference) < 1))
  const settingsGuide = await configured.getByRole('link', { name: /高德 Key 与安全密钥说明/ }).boundingBox()
  const saveSettings = await configured.getByRole('button', { name: '保存设置', exact: true }).boundingBox()
  assert.ok(settingsGuide.y + settingsGuide.height < saveSettings.y)
  assert.equal(await configured.getByText('生成等时圈时消耗高德算路配额。', { exact: true }).count(), 0)
  await configured.getByRole('button', { name: '显示 Web 服务 Key', exact: true }).click()
  await configured.getByRole('button', { name: '隐藏 Web 服务 Key', exact: true }).waitFor()
  assert.equal(await webKey.inputValue(), secrets.web_service_key)
  assert.equal(await webKey.getAttribute('type'), 'text')
  await configured.getByRole('button', { name: '隐藏 Web 服务 Key', exact: true }).click()
  assert.equal(await webKey.getAttribute('type'), 'password')
  assert.equal(await webKey.inputValue(), '')
  assert.equal(reveals, 1)
  await amapToggle.click()
  await configured.locator('#amap-settings').waitFor({ state: 'hidden' })
  assert.equal(await webKey.isVisible(), false)
  await configured.locator('#analysis-toggle').click()
  assert.equal(await configured.getByLabel('数据源', { exact: true }).count(), 0)
  assert.equal(await configured.locator('.amap').getAttribute('data-map-style'), 'amap://styles/normal')
  assert.equal(await configured.evaluate(() => window._AMapSecurityConfig.serviceHost), new URL('/_AMapService', base).href)
  await configured.getByRole('button', { name: '生成等时圈', exact: true }).click()
  assert.equal(await configured.locator('#analysis-toggle').getAttribute('aria-expanded'), 'false')
  const canvas = configured.locator('.amap canvas[data-follow-map="true"]')
  const alphaAt = (x, y) => canvas.evaluate((node, [x, y]) =>
    node.getContext('2d').getImageData(x, y, 1, 1).data[3], [x, y])
  const legend = configured.getByRole('complementary', { name: '等时圈图例' })
  await legend.waitFor()
  assert.deepEqual(await legend.locator('.legend-tick').allTextContents(), ['15 min', '30 min', '45 min', '60 min'])
  assert.match(await legend.innerText(), /11:14:27–11:14:32（北京时间）/)
  assert.equal(await legend.locator('.legend-time').evaluate(time => getComputedStyle(time).textAlign), 'center')
  assert.equal(await legend.locator('.legend-tick').first().evaluate(tick => getComputedStyle(tick).textAlign), 'center')
  assert.ok((await legend.locator('.legend-time').boundingBox()).y < (await legend.locator('.legend-scale').boundingBox()).y)
  assert.equal(await legend.locator('.legend-segment').first().locator('span').first().evaluate(segment =>
    getComputedStyle(segment).borderTopLeftRadius), '4px')
  assert.ok(await alphaAt(250, 280) > 0)
  assert.equal(await alphaAt(325, 280), 0)
  assert.equal(await configured.getByText('高德驾车数据', { exact: true }).count(), 0)
  assert.ok((await legend.boundingBox()).x < 50 && (await legend.boundingBox()).y > 500)
  const basemap = configured.locator('.amap [data-basemap]')
  const compass = await configured.locator('.compass').boundingBox()
  const layerButton = await configured.locator('#layers-toggle').boundingBox()
  const zoomGroup = await configured.locator('.zoom-controls').boundingBox()
  assert.deepEqual([compass.width, compass.height], [layerButton.width, layerButton.height])
  assert.ok(layerButton.y > zoomGroup.y)
  await amapToggle.click()
  await configured.mouse.move(160, 520)
  await configured.mouse.down()
  await configured.mouse.move(235, 520, { steps: 5 })
  await configured.mouse.up()
  assert.equal(await amapToggle.getAttribute('aria-expanded'), 'false')
  assert.equal((await basemap.boundingBox()).x, 75)
  assert.equal(await alphaAt(250, 280), 0)
  assert.ok(await alphaAt(325, 280) > 0)
  await amapToggle.click()
  await configured.mouse.click(160, 520)
  assert.equal(await amapToggle.getAttribute('aria-expanded'), 'false')

  keepRunning = true
  await configured.locator('#analysis-toggle').click()
  await configured.getByRole('slider', { name: '最小间距' }).press('ArrowRight')
  await configured.getByRole('button', { name: '生成等时圈', exact: true }).click()
  const progress = configured.locator('.map-job')
  await progress.getByRole('button', { name: '取消', exact: true }).waitFor()
  assert.equal(await legend.count(), 0)
  assert.equal(await alphaAt(115, 340), 0)
  await configured.locator('#layers-toggle').click()
  const liveBatch = configured.waitForResponse(response => response.url().endsWith(samplesPath + '?after=1'))
  const sampleToggle = configured.getByRole('checkbox', { name: '采样点' })
  await sampleToggle.check()
  await liveBatch
  await configured.waitForFunction(() => document.querySelector('.amap canvas')
    .getContext('2d').getImageData(115, 340, 1, 1).data[3] > 0)
  assert.equal(await legend.count(), 0)
  await sampleToggle.uncheck()
  assert.equal(await alphaAt(115, 340), 0)
  await configured.locator('#layers-toggle').click()
  showPreview()
  await legend.getByText('初步等时圈 · 正在加密', { exact: true }).waitFor()
  for (const width of [632, 390]) {
    await configured.setViewportSize({ width, height: 690 })
    const bounds = await configured.locator('.map-bottom').evaluate(row =>
      ['.legend', '.map-job', '#layers-toggle'].map(selector => {
        const { bottom, left, right } = row.querySelector(selector).getBoundingClientRect()
        return { bottom, left, right }
      }))
    assert.ok(bounds.every(item => Math.abs(item.bottom - bounds[0].bottom) < 1))
    assert.ok(bounds[0].right < bounds[1].left && bounds[1].right < bounds[2].left)
  }
  keepRunning = false
  await configured.setViewportSize({ width: 632, height: 690 })
  await configured.goto(new URL('?fixture=raster', base).href)
  await configured.locator('#analysis-toggle').click()
  await configured.getByRole('button', { name: '生成等时圈', exact: true }).click()
  await legend.waitFor()
  await canvas.waitFor()
  assert.match(await canvas.getAttribute('class'), /amap-travel-fallback/)
  assert.ok(await alphaAt(250, 280) > 0)
  await configured.mouse.move(160, 520)
  await configured.mouse.down()
  await configured.mouse.move(235, 520, { steps: 5 })
  await configured.mouse.up()
  await configured.waitForFunction(() => document.querySelector('.amap-travel-fallback')
    ?.getContext('2d').getImageData(325, 280, 1, 1).data[3] > 0)
  assert.equal(await alphaAt(250, 280), 0)
  console.log('Browser E2E passed: configuration gating, light map, hidden scrollbars, legend placement and rendered isochrone pixels before and after panning.')
} finally {
  await browser.close()
}
