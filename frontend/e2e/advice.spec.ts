// E2E #137/#148: подраздел «Что делать сейчас» объединённого блока
// ассистента в карточке поля — последняя AI-рекомендация (API-55),
// принудительный пересчёт (POST 202 queued → поллинг GET), пустое
// состояние «Рассчитать», история с offset-пагинацией (API-56).
// Мок-мир: поле 1 — 22 записи, поле 3 — пусто.
import { expect, test } from '@playwright/test'

const FIELD1 = '0000000a-0000-4000-8000-000000000001'
const FIELD3 = '0000000a-0000-4000-8000-000000000003'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/, { timeout: 15_000 })
})

function adviceBlock(page: import('@playwright/test').Page) {
  return page.locator('.card360__block--insights')
}

test('рекомендация: последняя запись с датой и пересчётом', async ({ page }) => {
  await page.goto(`/fields/${FIELD1}`)
  const block = adviceBlock(page)
  await expect(block).toBeVisible({ timeout: 15_000 })

  await expect(block.locator('.card360__advice-text')).toContainText(/подкормк|обследов|окн|вегетация|обработку/, {
    timeout: 10_000,
  })
  await expect(block.locator('.card360__advice-meta')).toContainText('Рассчитано')
  await expect(block.locator('.card360__advice-btn')).toHaveText('Пересчитать')
})

test('рекомендация: пересчёт — 202 queued и поллинг до новой записи', async ({ page }) => {
  await page.goto(`/fields/${FIELD1}`)
  const block = adviceBlock(page)
  await expect(block.locator('.card360__advice-btn')).toHaveText('Пересчитать', {
    timeout: 15_000,
  })

  const before = await block.locator('.card360__advice-text').textContent()
  await block.locator('.card360__advice-btn').click()
  await expect(block.locator('.card360__advice-btn')).toHaveText('Пересчитываем…')
  // Мок «готов» через 1.5 с, поллинг — раз в 10 с: ждём обновления.
  await expect(block.locator('.card360__advice-text')).not.toHaveText(before ?? '', {
    timeout: 25_000,
  })
  await expect(block.locator('.card360__advice-text')).toContainText('пересчитана')
  await expect(block.locator('.card360__advice-btn')).toHaveText('Пересчитать')
})

test('рекомендация: пустое состояние и первый расчёт', async ({ page }) => {
  await page.goto(`/fields/${FIELD3}`)
  const block = adviceBlock(page)
  await expect(block).toBeVisible({ timeout: 15_000 })

  await expect(block).toContainText('Рекомендация ещё не рассчитана')
  await expect(block.locator('.card360__advice-btn')).toHaveText('Рассчитать')

  await block.locator('.card360__advice-btn').click()
  await expect(block.locator('.card360__advice-btn')).toHaveText('Пересчитываем…')
  await expect(block.locator('.card360__advice-text')).toContainText('пересчитана', {
    timeout: 25_000,
  })
})

test('рекомендация: история с пагинацией «Показать ещё»', async ({ page }) => {
  await page.goto(`/fields/${FIELD1}`)
  const block = adviceBlock(page)
  await expect(block.locator('.card360__advice-btn')).toHaveText('Пересчитать', {
    timeout: 15_000,
  })

  await block.locator('.card360__advice-toggle').click()
  const items = block.locator('.card360__advice-list li')
  await expect(items).toHaveCount(20, { timeout: 10_000 })
  await expect(block.locator('.card360__advice-toggle')).toContainText('(20)')

  await block.locator('.card360__advice-more').click()
  await expect(items).toHaveCount(22, { timeout: 10_000 })
  await expect(block.locator('.card360__advice-more')).toHaveCount(0)
  await expect(block.locator('.card360__advice-toggle')).toContainText('(22)')
})
