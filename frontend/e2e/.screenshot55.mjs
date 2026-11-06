// Скриншот карты с цветами культур и номерами полей (#55, для ревью).
import { chromium } from '@playwright/test'

const run = async () => {
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
  await page.goto('http://localhost:5173/')
  await page.getByLabel('Логин').fill('agronom')
  await page.getByLabel('Пароль', { exact: true }).fill('demo')
  await page.getByRole('button', { name: 'Войти' }).click()
  await page.waitForURL(/\/fields\/map/, { timeout: 15_000 })
  await page.locator('.field-label').first().waitFor({ timeout: 15_000 })
  await page.waitForTimeout(1500) // тайлы карты
  await page.screenshot({ path: '../.scratch/aleksey.dorokhov/map55_colors_labels.png' })
  await browser.close()
}

run().catch((e) => {
  console.error(e)
  process.exit(1)
})
