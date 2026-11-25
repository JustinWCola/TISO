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
  await page.goto(base)
  assert.equal(await page.title(), 'TISO · 交通可达性')
  const generate = page.getByRole('button', { name: '生成等时圈' })
  const map = page.locator('canvas')
  async function canvasPixel() {
    return map.evaluate(canvas => {
      const ratio = window.devicePixelRatio
      const pixel = canvas.getContext('2d').getImageData(
        canvas.width / 2 + 24 * ratio, canvas.height / 2 + 11 * ratio, 1, 1,
      )
      return [...pixel.data]
    })
  }

  await map.waitFor()
  await page.waitForFunction(() => document.querySelector('canvas').width > 400)
  const before = await canvasPixel()
  await generate.click()
  await page.getByText('模拟数据', { exact: true }).waitFor()
  assert.notDeepEqual(await canvasPixel(), before)
  assert.match(await page.locator('.map-footer').innerText(), /未知区域/)
  await page.getByLabel('采样点').check()
  await page.getByLabel('批次预算').fill('1')
  await page.getByText('超出预算，请缩小半径或降低精度。').waitFor()
  assert.ok(await generate.isDisabled())
  await page.getByText('旧结果 · 模拟数据', { exact: true }).waitFor()
  await page.getByLabel('批次预算').fill('150')
  await generate.click()
  await page.getByRole('button', { name: '取消' }).click()
  await page.getByText(/已取消 ·/).waitFor()
  const oldLongitude = await page.getByLabel('经度', { exact: true }).inputValue()
  await map.click({ position: { x: 185, y: 180 } })
  assert.notEqual(await page.getByLabel('经度', { exact: true }).inputValue(), oldLongitude)
  const box = await map.boundingBox()
  const currentLatitude = await page.getByLabel('纬度', { exact: true }).inputValue()
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2 - 32, { steps: 5 })
  await page.mouse.up()
  assert.ok(Number(await page.getByLabel('纬度', { exact: true }).inputValue()) > Number(currentLatitude))
  assert.deepEqual(externalRequests, [])
  assert.deepEqual(pageErrors, [])
  console.log('Mock browser E2E passed: estimate, SSE, rendered bands, stale result, cancel and map selection.')
} finally {
  await browser.close()
}
