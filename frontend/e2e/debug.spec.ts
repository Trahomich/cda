import { expect, test } from '@playwright/test'

// Точная копия теста «422» из offline.spec.ts + диагностика.
test('debug 422 copy', async ({ page }) => {
  page.on('console', (m) => console.log('PAGE:', m.type(), m.text()))
  page.on('pageerror', (e) => console.log('PAGEERROR:', e.message))
  await page.goto('/login')
  await page.fill('#login', 'agronom')
  await page.fill('#password', 'demo')
  await page.getByRole('button', { name: 'Войти' }).click()
  await expect(page).toHaveURL(/fields\/map|\/f\//)

  await page.goto('/f/operations')
  await expect
    .poll(async () => page.locator('#field option').count(), { timeout: 15_000 })
    .toBeGreaterThan(1)
  await page.selectOption('#field', { index: 1 })
  await page.fill('#area', '999')
  await page.context().setOffline(true)
  await page.getByRole('button', { name: /ЗАПИСАТЬ/ }).click()
  await page.waitForTimeout(3000)
  const btn = await page.locator('.op-entry__queue-btn').textContent()
  console.log('BTN:', btn)
  const state = await page.evaluate(async () => {
    const db = await (indexedDB as any).databases?.()
    return db?.map((d: any) => d.name)
  })
  console.log('DBS:', JSON.stringify(state))
  const toasts = await page.evaluate(() => Array.from(document.querySelectorAll('[class*=toast]')).map((n) => n.textContent))
  console.log('TOASTS:', JSON.stringify(toasts))
})
