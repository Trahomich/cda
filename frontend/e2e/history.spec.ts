// E2E v1-11 (#67): лента истории севооборота по полю (API-49) — в карточке
// поля (E-03) и на экране плана севооборота (E-04), включая пустое состояние.
import { expect, test } from '@playwright/test'

/** Первый field_id демо-хозяйства мока (поле №1 с историей и планом). */
const FIRST_FIELD = '0000000a-0000-4000-8000-000000000001'
/** Поле №6 мока: archived, без циклов — для пустого состояния ленты. */
const EMPTY_FIELD = '0000000a-0000-4000-8000-000000000006'

test('карточка поля: лента истории севооборота с цветами и статусами (#67)', async ({
  page,
}) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/)
  await page.goto(`/fields/${FIRST_FIELD}`)

  const strip = page.locator('.strip')
  await expect(strip).toBeVisible({ timeout: 15_000 })
  // Ось времени слева направо по возрастанию года (v2-fix-A п.6, вариант А):
  // мок отдаёт 2023..2031 (9 лет), окно 8 лет — скрыт самый старый 2023,
  // слева счётчик «… +1», затем ячейки 2024..2031 по возрастанию.
  const more = strip.locator('.strip__more')
  await expect(more).toHaveText('… +1')
  const years = page.locator('.strip__year')
  await expect(years.first()).toHaveText('2024')
  await expect(years.last()).toHaveText('2031')
  const count = await years.count()
  expect(count).toBe(8)
  // Свот цвета культуры есть и закрашен цветом справочника (не серый по умолчанию).
  const firstCell = strip.locator('.strip__cell').first()
  await expect(firstCell.locator('.strip__swatch')).toBeVisible()
  // Текущий год выделен обводкой (класс статуса).
  await expect(
    page.locator(`.strip__cell--in_progress .strip__year`, {
      hasText: String(new Date().getFullYear()),
    }),
  ).toBeVisible()
})

test('экран севооборота: клик по полю раскрывает ленту истории (#67)', async ({
  page,
}) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/)
  await page.goto('/rotation')

  const toggle = page.locator('.rotation__field-toggle').first()
  await expect(toggle).toBeVisible({ timeout: 15_000 })
  await toggle.click()

  const strip = page.locator('.rotation__history-row .strip')
  await expect(strip).toBeVisible({ timeout: 15_000 })
  await expect(strip.locator('.strip__cell').first()).toBeVisible()
  // Повторный клик сворачивает.
  await toggle.click()
  await expect(strip).toHaveCount(0)
})

test('лента истории: пустое состояние с подсказкой (#67)', async ({ page }) => {
  // Поле без истории и плана (мок: archived без циклов) — подсказка, не ошибка.
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/)
  await page.goto(`/fields/${EMPTY_FIELD}`)

  await expect(page.locator('.strip__empty')).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.strip__empty')).toContainText('Истории севооборота')
})
