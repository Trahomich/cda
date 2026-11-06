// E2E UX-фиксы (MVP-23, #45): форма входа (401 → понятная ошибка), тосты не
// перекрывают навигацию, явный выход из полевого режима.
import { expect, test } from '@playwright/test'

test('вход: 401 → понятная ошибка, подсказка демо-доступа по режиму', async ({ page }) => {
  await page.goto('/login')
  await expect(page.locator('.login__label[for="login"]')).toHaveText('Логин')
  await expect(page.locator('.login__label[for="password"]')).toHaveText('Пароль')
  await expect(page.locator('.login__hint')).toContainText('Демо-доступ')

  await page.locator('#login').fill('agronom')
  await page.locator('#password').fill('wrong-password')
  await page.locator('.login__submit').click()
  await expect(page.locator('.login__error')).toHaveText('Неверный логин или пароль')
})

test('тост об ошибке не перекрывает нижнюю навигацию полевого режима', async ({ page }) => {
  // Логин (мок) → полевой режим → онлайн-отправка операции с урожайностью
  // при типе ≠ harvesting → 422 yield_requires_harvesting → тост problem+json.
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/)
  await page.goto('/f/operations')

  await page.locator('#field').selectOption({ index: 1 })
  await page.locator('#type').selectOption({ label: 'Уборка' })
  await page.locator('#yield').fill('45')
  // Реальный сценарий ошибки: тип сменили, урожайность осталась в форме.
  await page.locator('#type').selectOption({ label: 'Обработка СЗР' })
  await page.locator('#area').fill('10')
  await page.locator('.op-entry__submit').click()

  const toast = page.locator('.toast').first()
  await expect(toast).toBeVisible({ timeout: 15_000 })
  const nav = page.locator('.field__nav')
  await expect(nav).toBeVisible()

  const toastBox = await toast.boundingBox()
  const navBox = await nav.boundingBox()
  expect(navBox).not.toBeNull()
  expect(toastBox).not.toBeNull()
  // Тост целиком выше нижней навигации (не перекрывает её).
  expect(toastBox!.y + toastBox!.height).toBeLessThanOrEqual(navBox!.y + 1)
})

test('выход из полевого режима: кнопка в шапке и экран «Ещё…» ведут в кабинет', async ({
  page,
}) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/)

  await page.goto('/f/map')
  await expect(page.locator('.field__exit')).toBeVisible()
  await page.locator('.field__exit').click()
  await page.waitForURL(/\/fields\/map/)
  await expect(page.locator('.cabinet__header')).toBeVisible()

  // И через экран «Ещё…» — полноценная кнопка.
  await page.goto('/f/queue')
  await expect(page.locator('.queue__exit')).toBeVisible()
  await page.locator('.queue__exit').click()
  await page.waitForURL(/\/fields\/map/)
  await expect(page.locator('.cabinet__header')).toBeVisible()
})
