import assert from 'node:assert/strict'
import { chromium } from 'playwright-core'

const base = process.env.TISO_TEST_URL ?? 'http://127.0.0.1:8080'
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH ?? '/usr/bin/chromium',
})

async function pairedActionStyles(page, selector) {
  return page.locator(selector).evaluate(async row => {
    const dock = row.closest('.dock')
    await Promise.all(dock.getAnimations().map(animation => animation.finished))
    const card = row.closest('.analysis-card, .amap-settings-content')
    const shell = getComputedStyle(dock)
    const inset = parseFloat(shell.borderLeftWidth) + parseFloat(getComputedStyle(card).paddingLeft)
    return [...row.querySelectorAll('button')].map(button => {
      const style = getComputedStyle(button)
      const halfHeight = parseFloat(style.height) / 2
      const radius = Math.min(parseFloat(style.borderTopLeftRadius), halfHeight, parseFloat(style.width) / 2)
      return {
        height: style.height, font: style.fontSize, fill: style.backgroundColor, radius,
        isPill: radius === halfHeight, cornerError: parseFloat(shell.borderTopLeftRadius) - inset - radius,
      }
    })
  })
}
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
  const actionBounds = await page.locator('.analysis-actions').evaluate(row => {
    const [generateButton, historyButton] = row.querySelectorAll('button')
    const generate = generateButton.getBoundingClientRect()
    const history = historyButton.getBoundingClientRect()
    return { topDifference: generate.top - history.top, gap: history.left - generate.right,
      heightDifference: generate.height - history.height }
  })
  assert.ok(Math.abs(actionBounds.topDifference) < 1 && Math.abs(actionBounds.heightDifference) < 1 && actionBounds.gap > 0)
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
  await configured.route('**/api/v1/places/reverse', async route => {
    const { lng } = route.request().postDataJSON()
    if (lng !== 121.4737) await new Promise(resolve => setTimeout(resolve, 150))
    const name = lng === 116.4074 ? '北京市' : lng === 121.4737 ? '人民广场' : '上海市黄浦区中山东一路'
    return route.fulfill({ json: { name } })
  })
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
            element.addEventListener('fixture-map-click', event => {
              const [lng, lat] = event.detail;
              this.events.get('click')?.({ lnglat: { getLng: () => lng, getLat: () => lat } });
            });
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
            window.fixtureProjectionCalls = (window.fixtureProjectionCalls ?? 0) + 1;
            return { getX: () => 220 + (lng - 121.47) * 6000 + this.offset,
              getY: () => 250 + (31.24 - lat) * 6000 };
          }
        },
        Marker: class { on() {} off() {} setMap() {} setPosition() {} },
        PlaceSearch: class {
          search(query, callback) {
            callback('complete', { poiList: { pois: [{ name: query, address: '上海市黄浦区',
              location: { getLng: () => 121.4737, getLat: () => 31.2304 } }] } });
          }
        },
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
  let showPartial = false
  let showMetro = false
  let immediateQueries = 0
  let localizedDestination
  let localizedJobs = 0
  const metroNetwork = { status: 'ready', city_name: '上海市', station_count: 412 }
  const metroOptions = {
    mode: 'metro', traffic_mode: 'static', walk_speed_m_s: 1.3,
    max_duration_min: 60, contour_interval_min: 15,
  }
  const active = { ...done, job_id: jobId + '-running', status: 'running', stage: 'refining', completed_points: 8, preview_ready: true }
  await configured.route('**/api/v1/isochrones/jobs', route => {
    immediateQueries += 1
    const { destination, destination_name, ...options } = route.request().postDataJSON()
    assert.ok(destination.lng && destination.lat)
    if (localizedDestination) {
      assert.deepEqual(destination, localizedDestination)
      assert.equal(destination_name, '北京市')
      localizedJobs += 1
    }
    assert.deepEqual(options, showMetro ? metroOptions : {
      max_duration_min: 60, contour_interval_min: 15, min_spacing_m: keepRunning ? 500 : 250,
    })
    return route.fulfill({ status: 202, json: keepRunning ? active : { ...done, status: 'queued' } })
  })
  await configured.route('**/api/v1/isochrones/jobs/' + active.job_id + '/events', route => route.fulfill({
    contentType: 'text/event-stream', body: 'retry: 60000\nevent: progress\ndata: ' + JSON.stringify(active) + '\n\n',
  }))
  const jobPath = '**/api/v1/isochrones/jobs/' + jobId
  await configured.route(jobPath + '/events', route => {
    const status = showPartial ? 'partial' : 'completed'
    return route.fulfill({
      contentType: 'text/event-stream', body: 'event: completed\ndata: ' + JSON.stringify({ ...done, status }) + '\n\n',
    })
  })
  const completedResult = {
    job_id: jobId,
    mode: 'driving',
    destination: { lng: 121.4737, lat: 31.2304 },
    bands: { features: [15, 30, 45, 60].map((maximum, index) => ({
      properties: { minimum_min: index * 15, maximum_min: maximum }, geometry: { coordinates: [[ring]] },
    })) },
    contours: { features: [] },
    samples: [],
    observed_from: '2026-11-25T03:14:27Z',
    observed_to: '2026-11-25T03:14:32Z',
  }
  const stopReason = '向外采样提前结束：高德 HTTP 请求失败（403）'
  await configured.route(jobPath + '/result', route => route.fulfill({
    json: {
      ...completedResult,
      ...(showPartial ? { boundary_truncated: true, warnings: [stopReason] } : {}),
      ...(showMetro ? { mode: 'metro', metro_network: metroNetwork,
        destination: localizedDestination ?? completedResult.destination,
        observed_from: null, observed_to: null, finished_at: localizedDestination ? '2026-11-25T04:23:11Z' : '2026-11-25T04:22:11Z',
        warnings: ['站间和换乘耗时为静态估算。站外范围按 1.3 米/秒直线计算。', '未知区域不填色。'] } : {}),
    },
  }))
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
  const pick = detail => configured.locator('.amap').evaluate((map, coordinates) => {
    map.dispatchEvent(new CustomEvent('fixture-map-click', { detail: coordinates }))
  }, detail)
  await configured.setViewportSize({ width: 320, height: 690 })
  const compactToggles = await configured.locator('#analysis-toggle, #amap-toggle').evaluateAll(buttons => buttons.map(button => {
    const shell = button.closest('.dock').getBoundingClientRect()
    const icon = button.querySelector('.brand-icon').getBoundingClientRect()
    const title = getComputedStyle(button.querySelector('.menu-title'))
    return [shell.width, shell.height, icon.left - shell.left, icon.top - shell.top, title.visibility]
  }))
  assert.deepEqual(compactToggles, [[44, 44, 1, 1, 'hidden'], [44, 44, 1, 1, 'hidden']])
  await configured.setViewportSize({ width: 632, height: 690 })
  const closedSettings = await amapToggle.boundingBox()
  await amapToggle.click()
  await amapToggle.getByText('高德设置', { exact: true }).waitFor()
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
  const settingsButtonStyles = await pairedActionStyles(configured, '.settings-actions')
  assert.ok(settingsButtonStyles.every(style => style.isPill && style.cornerError === 0))
  const credentialCornerError = await webKey.evaluate(input => {
    const eye = input.nextElementSibling
    const style = getComputedStyle(eye)
    const field = getComputedStyle(input)
    const fieldRadius = Math.min(parseFloat(field.borderTopRightRadius), parseFloat(field.height) / 2)
    const eyeRadius = Math.min(parseFloat(style.borderTopRightRadius), parseFloat(style.height) / 2)
    return fieldRadius - parseFloat(style.right) - eyeRadius
  })
  assert.equal(credentialCornerError, 0)
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
  assert.deepEqual(await pairedActionStyles(configured, '.analysis-actions'), settingsButtonStyles)
  const switchCornerError = await configured.locator('.mode-switch').evaluate(control => {
    const outer = getComputedStyle(control)
    const inner = getComputedStyle(control, '::before')
    return parseFloat(outer.borderTopLeftRadius) - parseFloat(outer.borderLeftWidth)
      - parseFloat(inner.left) - parseFloat(inner.borderTopLeftRadius)
  })
  assert.equal(switchCornerError, 0)
  assert.equal(await configured.getByLabel('数据源', { exact: true }).count(), 0)
  assert.ok(await configured.getByLabel('定时时间', { exact: true }).isHidden())
  assert.ok(await configured.getByRole('button', { name: '开启定时' }).isVisible())
  assert.equal(await configured.locator('.amap').getAttribute('data-map-style'), 'amap://styles/normal')
  assert.equal(await configured.evaluate(() => window._AMapSecurityConfig.serviceHost), new URL('/_AMapService', base).href)
  await configured.getByRole('button', { name: '生成等时圈', exact: true }).click()
  assert.equal(await configured.locator('#analysis-toggle').getAttribute('aria-expanded'), 'false')
  const canvas = configured.locator('.amap canvas[data-follow-map="true"]')
  const alphaAt = (x, y) => canvas.evaluate((node, [x, y]) =>
    node.getContext('2d').getImageData(x, y, 1, 1).data[3], [x, y])
  const legend = configured.getByRole('complementary', { name: '等时圈图例' })
  const notice = configured.locator('.map-notice')
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
  assert.equal(await legend.getByRole('button', { name: /导出/ }).count(), 0)
  const basemap = configured.locator('.amap [data-basemap]')
  const compass = await configured.locator('.compass').boundingBox()
  const layerButton = await configured.locator('#layers-toggle').boundingBox()
  const zoomGroup = await configured.locator('.zoom-controls').boundingBox()
  assert.deepEqual([compass.width, compass.height], [layerButton.width, layerButton.height])
  assert.equal(zoomGroup.width, layerButton.width)
  assert.equal(layerButton.width, closedSettings.width + 2)
  assert.equal(layerButton.x + layerButton.width, closedSettings.x + closedSettings.width + 1)
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
  await configured.evaluate(() => {
    window.feedbackExits = []
    document.addEventListener('animationstart', event => {
      if (event.animationName === 'feedback-exit' && event.target.matches('.map-feedback-panel')) {
        const isLegend = event.target.classList.contains('map-feedback-legend')
        window.feedbackExits.push(isLegend ? 'legend' : 'popup')
        if (isLegend) {
          const panel = event.target
          window.legendExitHeights = []
          function sample() {
            if (!panel.isConnected) return
            window.legendExitHeights.push(panel.offsetHeight)
            requestAnimationFrame(sample)
          }
          sample()
        }
      }
    })
  })
  await configured.locator('#analysis-toggle').click()
  await configured.getByRole('slider', { name: '最小间距' }).press('ArrowRight')
  const leavingLegend = configured.locator('.map-feedback-legend[data-exiting="true"]')
  await configured.getByRole('button', { name: '生成等时圈', exact: true }).click()
  await configured.waitForFunction(() => window.feedbackExits.includes('legend'))
  await leavingLegend.waitFor({ state: 'detached' })
  const [firstHeight, ...exitHeights] = await configured.evaluate(() => window.legendExitHeights)
  assert.ok(exitHeights.some(height => height > 2 && height < firstHeight - 2))
  const progress = configured.locator('.map-bottom .map-job')
  const cancelJob = progress.getByRole('button', { name: '取消', exact: true })
  await cancelJob.waitFor()
  const progressBackground = progress.getByRole('progressbar')
  assert.equal(await progressBackground.getAttribute('aria-valuenow'), null)
  assert.equal(await progressBackground.evaluate(element => getComputedStyle(element).animationName), 'progress-sweep')
  assert.ok(await progressBackground.evaluate(element =>
    element.offsetWidth === element.parentElement.clientWidth &&
    element.offsetHeight === element.parentElement.clientHeight &&
    getComputedStyle(element).position === 'absolute'))
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
  await progress.evaluate(element => {
    const start = performance.now()
    window.progressTops = []
    function sample() {
      window.progressTops.push(element.getBoundingClientRect().top)
      if (performance.now() - start < 500) requestAnimationFrame(sample)
    }
    sample()
  })
  showPreview()
  await legend.waitFor()
  await legend.evaluate(async element => {
    await Promise.all(element.closest('.map-feedback-legend').getAnimations().map(animation => animation.finished))
  })
  const [firstTop, ...tops] = await configured.evaluate(() => window.progressTops)
  const settledTop = (await progress.boundingBox()).y
  assert.ok(firstTop - settledTop > 20)
  assert.ok(tops.some(top => top < firstTop - 2 && top > settledTop + 2))
  assert.equal(await legend.locator('p').count(), 1)
  await progress.getByText('六边形网格加密中', { exact: true }).waitFor()
  for (const width of [632, 390]) {
    await configured.setViewportSize({ width, height: 690 })
    const [legendBox, progressBox, layersBox] = await Promise.all([
      legend.boundingBox(), progress.boundingBox(), configured.locator('#layers-toggle').boundingBox(),
    ])
    assert.ok(progressBox.y + progressBox.height < legendBox.y)
    assert.equal(progressBox.x, legendBox.x)
    assert.ok(legendBox.x + legendBox.width < layersBox.x)
    assert.ok(Math.abs(legendBox.y + legendBox.height - layersBox.y - layersBox.height) < 1)

    const progressNode = await progress.elementHandle()
    await configured.locator('#analysis-toggle').click()
    await configured.getByRole('radiogroup', { name: '交通方式' }).waitFor()
    assert.equal(await configured.locator('#analysis-controls [role="progressbar"]').count(), 0)
    assert.ok(await progress.evaluate((element, previous) => element === previous, progressNode))
    assert.deepEqual(await progress.boundingBox(), progressBox)
    await cancelJob.click({ trial: true })
    assert.equal((await legend.boundingBox()).width, legendBox.width)
    await configured.locator('#analysis-toggle').click()
    assert.ok(await progress.evaluate((element, previous) => element === previous, progressNode))
    await progressNode.dispose()
  }
  keepRunning = false
  showPartial = true
  await configured.setViewportSize({ width: 632, height: 690 })
  await configured.goto(new URL('?fixture=raster', base).href)
  await configured.locator('#analysis-toggle').click()
  await configured.getByRole('button', { name: '生成等时圈', exact: true }).click()
  await legend.waitFor()
  await notice.getByText(stopReason, { exact: true }).waitFor()
  assert.equal(await legend.getByText(stopReason, { exact: true }).count(), 0)
  const noticeBounds = await notice.boundingBox()
  assert.ok(noticeBounds.y + noticeBounds.height < (await legend.boundingBox()).y)
  await notice.waitFor({ state: 'hidden', timeout: 8000 })
  assert.equal(await progress.count(), 0)
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

  showMetro = true
  showPartial = false
  await configured.locator('#analysis-toggle').click()
  assert.equal(await configured.locator('#analysis-controls .job-status').count(), 0)
  const metroGenerate = configured.getByRole('button', { name: '生成等时圈', exact: true })
  const modeSwitch = configured.getByRole('radiogroup', { name: '交通方式' })
  const drivingMode = modeSwitch.getByRole('radio', { name: '驾车', exact: true })
  const metroMode = modeSwitch.getByRole('radio', { name: '地铁', exact: true })
  assert.ok(await drivingMode.isChecked())
  const walkSpeed = configured.getByRole('slider', { name: '步行速度', exact: true })
  assert.equal(await walkSpeed.count(), 0)
  await drivingMode.press('ArrowRight')
  assert.ok(await metroMode.isChecked())
  await legend.waitFor({ state: 'hidden' })
  await metroMode.press('ArrowLeft')
  assert.ok(await drivingMode.isChecked())
  await legend.getByText('算路时间 11:14:27–11:14:32（北京时间）').waitFor()
  await metroMode.check()
  assert.equal(await modeSwitch.getAttribute('data-mode'), 'metro')
  assert.equal(await walkSpeed.getAttribute('aria-valuetext'), '1.2 m/s')
  await walkSpeed.press('ArrowRight')
  assert.equal(await walkSpeed.getAttribute('aria-valuetext'), '1.3 m/s')
  assert.equal(await configured.getByLabel('到达时间（北京时间）').count(), 0)
  await metroGenerate.click()
  await legend.getByText('算路时间 12:22:11（北京时间）', { exact: true }).waitFor()
  const attribution = configured.getByRole('link', { name: '© OpenStreetMap 贡献者（ODbL）' })
  await attribution.waitFor()
  const corner = await attribution.evaluate(element => {
    const credit = element.getBoundingClientRect()
    const map = element.closest('.map').getBoundingClientRect()
    return { right: map.right - credit.right, bottom: map.bottom - credit.bottom }
  })
  assert.deepEqual(corner, { right: 6, bottom: 6 })
  assert.ok(await alphaAt(325, 280) > 0)
  const projectionsBeforePan = await configured.evaluate(() => window.fixtureProjectionCalls)
  await configured.mouse.move(160, 520)
  await configured.mouse.down()
  await configured.mouse.move(200, 520)
  await configured.mouse.up()
  await configured.waitForFunction(() => document.querySelector('.amap canvas')
    .getContext('2d').getImageData(290, 280, 1, 1).data[3] > 0)
  assert.equal(await alphaAt(325, 280), 0)
  const panProjections = (await configured.evaluate(() => window.fixtureProjectionCalls)) - projectionsBeforePan
  assert.ok(panProjections > 0 && panProjections <= 6)
  assert.equal(await legend.locator('p').count(), 1)
  assert.equal(await legend.getByRole('link').count(), 0)
  assert.equal(await notice.getByText(/站外范围按|未知区域不填色/).count(), 0)
  await notice.waitFor({ state: 'detached' })
  const queriesBeforeSwitch = immediateQueries
  await configured.locator('#analysis-toggle').click()
  await drivingMode.check()
  await legend.getByText('算路时间 11:14:27–11:14:32（北京时间）').waitFor()
  assert.equal(await attribution.count(), 0)
  await metroMode.check()
  await legend.getByText('算路时间 12:22:11（北京时间）').waitFor()
  await attribution.waitFor()
  assert.equal(await notice.getByText('旧结果 · 参数已修改', { exact: true }).count(), 0)
  assert.equal(immediateQueries, queriesBeforeSwitch)

  localizedDestination = { lng: 116.4074, lat: 39.9042 }
  let builds = 0
  let networkStatus = 'missing'
  const buildError = 'OpenStreetMap 数据服务暂不可用，请稍后重试'
  await configured.route('**/api/v1/isochrones/estimate', route => route.fulfill({
    json: { fits_budget: networkStatus === 'ready', metro_network: networkStatus === 'ready'
      ? { ...metroNetwork, city_name: '北京市', station_count: 2 }
      : { status: networkStatus, ...(networkStatus === 'failed' ? { message: buildError } : {}) } },
  }))
  await configured.route('**/api/v1/metro/networks', route => {
    assert.deepEqual(route.request().postDataJSON(), localizedDestination)
    builds += 1
    networkStatus = 'building'
    return route.fulfill({ status: 202, json: { status: 'building' } })
  })
  assert.equal(await configured.getByText('目的地坐标', { exact: true }).count(), 0)
  assert.equal(await configured.getByText('使用上海示例', { exact: true }).count(), 0)
  await pick([localizedDestination.lng, localizedDestination.lat])
  await legend.waitFor({ state: 'hidden' })
  assert.equal(await configured.getByRole('button', { name: '生成城市地铁路网' }).count(), 0)
  const requestNetwork = () => Promise.all([
    configured.waitForResponse(response => response.url().endsWith('/api/v1/metro/networks')),
    metroGenerate.click(),
  ])
  await requestNetwork()
  const networkProgress = configured.getByRole('progressbar', { name: '正在生成地铁路网…' })
  await networkProgress.waitFor()
  assert.equal(builds, 1)
  assert.equal(localizedJobs, 0)
  assert.equal(await networkProgress.getAttribute('aria-valuenow'), null)
  assert.equal(await networkProgress.evaluate(element => getComputedStyle(element).animationName), 'progress-sweep')
  networkStatus = 'failed'
  await notice.getByText(buildError, { exact: true }).waitFor()
  assert.equal(localizedJobs, 0)
  await configured.locator('#analysis-toggle').click()
  await requestNetwork()
  await networkProgress.waitFor()
  assert.equal(builds, 2)
  assert.equal(localizedJobs, 0)
  networkStatus = 'ready'
  await legend.getByText('算路时间 12:23:11（北京时间）', { exact: true }).waitFor()
  assert.equal(localizedJobs, 1)
  assert.equal(await configured.getByText(/本地路网 .* 静态耗时估算/).count(), 0)
  await configured.locator('#analysis-toggle').click()
  await configured.waitForFunction(() => !document.querySelector('#analysis-controls .generate').disabled)

  const savedParams = { destination: completedResult.destination, destination_name: '人民广场',
    max_duration_min: 60, contour_interval_min: 15, min_spacing_m: 250 }
  const summary = { id: jobId, params: savedParams, status: 'completed', finished_at: '2026-11-25T03:14:32Z' }
  let historyDeleted = false
  await configured.route(url => url.pathname.endsWith('/isochrones/history'), route =>
    route.fulfill({ json: { items: historyDeleted ? [] : [summary], more: false } }))
  await configured.route('**/api/v1/isochrones/history/' + jobId, route => {
    if (route.request().method() === 'DELETE') {
      historyDeleted = true
      return route.fulfill({ json: { deleted: true } })
    }
    return route.fulfill({ json: { summary, result: completedResult } })
  })
  await configured.getByRole('button', { name: '历史结果', exact: true }).click()
  const management = configured.getByRole('dialog', { name: '历史结果' })
  await management.getByRole('button', { name: '查看', exact: true }).waitFor()
  await management.getByText('人民广场', { exact: true }).waitFor()
  await management.getByText('驾车', { exact: true }).waitFor()
  const historyHeight = (await management.boundingBox()).height
  assert.equal(historyHeight, 560)
  const menuStyles = await configured.evaluate(() => ['#analysis-toggle', '#amap-toggle', '.query-heading'].map(selector => {
    const heading = document.querySelector(selector)
    const title = getComputedStyle(heading.querySelector('.menu-title'))
    const icon = getComputedStyle(heading.querySelector('.brand-icon'))
    const glyph = getComputedStyle(heading.querySelector('.brand-icon svg'))
    return [title.fontSize, title.fontWeight, title.lineHeight, icon.width, icon.height, glyph.width, glyph.height]
  }))
  assert.deepEqual(menuStyles[1], menuStyles[0])
  assert.deepEqual(menuStyles[2], menuStyles[0])
  const corners = await management.evaluate(panel => {
    const radius = element => parseFloat(getComputedStyle(element).borderTopLeftRadius)
    const card = panel.querySelector('.query-list > li')
    const label = card.querySelector('.query-mode')
    const cardBox = card.getBoundingClientRect()
    const labelBox = label.getBoundingClientRect()
    return {
      menus: [radius(document.querySelector('.analysis-dock')), radius(document.querySelector('.settings-dock')), radius(panel)],
      panelInset: radius(panel) - radius(card),
      recordInset: radius(card) - labelBox.height / 2,
      labelLeft: labelBox.left - cardBox.left,
      labelTop: labelBox.top - cardBox.top,
    }
  })
  assert.deepEqual(corners.menus, [32, 32, 32])
  assert.equal(corners.panelInset, 11)
  assert.equal(corners.labelLeft, corners.recordInset)
  assert.equal(corners.labelTop, corners.recordInset)
  assert.equal(await management.getByRole('button', { name: '关闭历史结果' }).textContent(), '')
  assert.ok(await management.evaluate(panel => {
    const header = panel.querySelector('.query-header')
    const content = panel.querySelector('.query-content')
    const record = panel.querySelector('.query-record-content')
    const actions = panel.querySelector('.query-list .query-actions')
    content.style.maxHeight = '32px'
    const before = header.getBoundingClientRect().top
    content.scrollTop = 100
    const verified = content.scrollTop > 0 && header.getBoundingClientRect().top === before
      && actions.getBoundingClientRect().left >= record.getBoundingClientRect().right
    content.style.removeProperty('max-height')
    return verified
  }))
  assert.equal(await management.getByRole('button', { name: /导出/ }).count(), 0)
  await management.getByRole('button', { name: '查看', exact: true }).click()
  await legend.getByText('算路时间 11:14:27–11:14:32（北京时间）').waitFor()
  await configured.locator('#analysis-toggle').click()
  await configured.getByRole('button', { name: '历史结果', exact: true }).click()
  configured.once('dialog', dialog => void dialog.accept())
  await management.getByRole('button', { name: '删除', exact: true }).click()
  await management.getByText('暂无历史结果').waitFor()
  assert.equal((await management.boundingBox()).height, historyHeight)

  let plan = null
  let schedulePosts = 0
  const schedulePath = '**/api/v1/isochrones/schedules'
  await configured.route(schedulePath, route => {
    if (route.request().method() === 'POST') {
      const { time, params } = route.request().postDataJSON()
      assert.equal(time, '09:15')
      assert.deepEqual(params, savedParams)
      schedulePosts += 1
      if (schedulePosts === 1) {
        return route.fulfill({ status: 503, json: { error: { message: '暂时无法保存定时查询' } } })
      }
      plan = { id: 'morning', name: '驾车等时圈', clock: time, params, enabled: true, next_run: '2026-11-27T01:15:00Z' }
      return route.fulfill({ status: 201, json: plan })
    }
    return route.fulfill({ json: plan ? [plan] : [] })
  })
  await configured.route(schedulePath + '/morning', route => {
    assert.equal(route.request().method(), 'PATCH')
    plan.enabled = route.request().postDataJSON().enabled
    return route.fulfill({ json: plan })
  })
  let immediateJobs = 0
  await configured.route('**/api/v1/isochrones/jobs', route => {
    immediateJobs += 1
    return route.fulfill({ status: 409, json: { error: { message: '应创建日程而非立即算路' } } })
  })
  await management.getByRole('button', { name: '关闭历史结果' }).click()
  await configured.getByLabel('地点关键词').fill('人民广场')
  await configured.getByRole('button', { name: '搜索', exact: true }).click()
  await configured.getByRole('button', { name: '人民广场 · 上海市黄浦区' }).click()
  await pick([121.49, 31.24])
  await configured.waitForFunction(() => document.querySelector('.search input').value === '上海市黄浦区中山东一路')
  await pick([121.49, 31.24])
  await pick([121.4737, 31.2304])
  await configured.waitForFunction(() => document.querySelector('.search input').value === '人民广场')
  await configured.locator('#analysis-toggle').click()
  await configured.locator('.map-point-label').getByText('人民广场', { exact: true }).waitFor()
  await configured.waitForFunction(() => {
    const bounds = selector => document.querySelector(selector).getBoundingClientRect()
    const label = bounds('.map-point-label')
    const first = bounds('.analysis-dock')
    const last = bounds('.settings-dock')
    return label.top === first.top && label.top === last.top && label.height === first.height
      && label.height === last.height && first.right < label.left && label.right < last.left
  })
  await configured.locator('#analysis-toggle').click()
  const enableSchedule = configured.getByRole('button', { name: '开启定时' })
  const scheduleTime = configured.getByLabel('定时时间', { exact: true })
  const scheduleGenerate = configured.getByRole('button', { name: '生成等时圈', exact: true })
  await configured.waitForFunction(() => !document.querySelector('#analysis-controls .generate').disabled)
  await enableSchedule.click()
  assert.equal(await configured.getByLabel('地点关键词').inputValue(), '人民广场')
  const closeSchedule = configured.getByRole('button', { name: '关闭定时' })
  assert.equal(await closeSchedule.getAttribute('aria-expanded'), 'true')
  const scheduleOption = configured.locator('.schedule-option')
  assert.match(await scheduleOption.evaluate(row => getComputedStyle(row).transitionProperty), /grid-template-columns/)
  const fieldLayout = await scheduleOption.evaluate(async row => {
    await Promise.all(row.getAnimations({ subtree: true }).map(animation => animation.finished))
    const button = row.querySelector('button').getBoundingClientRect()
    const input = row.querySelector('input').getBoundingClientRect()
    return { topDifference: button.top - input.top, gap: input.left - button.right }
  })
  assert.ok(Math.abs(fieldLayout.topDifference) < 1 && fieldLayout.gap > 0)
  assert.ok(await scheduleGenerate.isDisabled())
  await scheduleTime.fill('08:30')
  assert.ok(await scheduleGenerate.isEnabled())
  await scheduleTime.press('Escape')
  await scheduleTime.waitFor({ state: 'hidden' })
  assert.equal(await enableSchedule.getAttribute('aria-expanded'), 'false')
  assert.ok(await enableSchedule.evaluate(button => button.ownerDocument.activeElement === button))
  assert.ok(await scheduleGenerate.isEnabled())
  await enableSchedule.click()
  assert.equal(await scheduleTime.inputValue(), '')
  await scheduleTime.fill('09:15')
  assert.equal(await configured.locator('#schedule-time-hint').count(), 0)
  await scheduleGenerate.click()
  await notice.getByText('暂时无法保存定时查询').waitFor()
  assert.equal(await scheduleTime.inputValue(), '09:15')
  assert.equal(await closeSchedule.getAttribute('aria-expanded'), 'true')
  await scheduleGenerate.evaluate(button => { button.click(); button.click() })
  await notice.getByText('已设置每日 09:15（北京时间）自动查询。').waitFor()
  assert.equal(schedulePosts, 2)
  assert.equal(immediateJobs, 0)
  await configured.locator('#analysis-toggle').click()
  assert.ok(await scheduleTime.isHidden())
  assert.ok(await enableSchedule.isVisible())
  await configured.getByRole('button', { name: '历史结果', exact: true }).click()
  await management.getByRole('heading', { name: '已设置的日程' }).waitFor()
  await management.getByRole('button', { name: '暂停', exact: true }).click()
  await management.getByRole('button', { name: '恢复', exact: true }).waitFor()
  console.log('Browser E2E passed: map rendering, metro, history and schedules.')
} finally {
  await browser.close()
}
