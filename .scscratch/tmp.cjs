/* #130: скриншоты мини-карты с индексными слоями (мок-режим dev-сервера).
 * Запуск: node .scratch/dmitriy.sokolov/ui_smoke_130.cjs 5175 */
const { chromium } = require('/swarm/sessions/002-tsda/workspace/frontend/node_modules/@playwright/test')

const PORT = process.argv[2] || '5175'
const FIELD = '0000000a-0000-4000-8000-000000000001'
const OUT = '/swarm/sessions/002-tsda/workspace/.scratch/dmitriy.sokolov'

;(async () => {
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1280, height: 1400 } })
  await page.goto(`http://localhost:${PORT}/login`)
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/, { timeout: 15000 })
  await page.goto(`http://localhost:${PORT}/fields/${FIELD}`)

  const switcher = page.locator('.card360__layer-switch')
  await switcher.waitFor({ timeout: 15000 })
  const mapBlock = page.locator('.card360__map-wrap')
  await page.locator('.card360__layer-btn--ndre').waitFor({ state: 'visible' })
  await page.waitForTimeout(1500)

  for (const key of ['ndre', 'ndmi', 'bsi']) {
    await page.locator(`.card360__layer-btn--${key}`).click()
    await page.locator('svg.card360__contour image.card360__ndvi-layer').waitFor({ timeout: 10000 })
    await page.waitForTimeout(400)
    await page.screenshot({ path: `${OUT}/shot_130_${key}.png`, fullPage: false })
    // Крупно блок мини-карты с переключателем
    const block = page.locator('.card360__block', {
      has: page.locator('.card360__block-title', { hasText: 'Контур поля' }),
    })
    await block.scrollIntoViewIfNeeded()
    await block.screenshot({ path: `${OUT}/shot_130_${key}_block.png` })
    console.log('saved', key)
  }
  await browser.close()
})().catch((e) => {
  console.error(e)
  process.exit(1)
})
