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

test('тост об ошибке не перекрывает навигацию полевого режима', async ({ page }) => {
  // Логин (мок) → полевой режим; вызываем ошибку API (неизвестное поле) не
  // через UI, а напрямую через тост-стор нельзя — проще: отправить форму
  // операции без выбора поля не выйдет (required), поэтому проверяем
  // позиционирование тоста по факту его появления при 422 RULE-AGRO-01
  // невозможно без сценария. Используем прямой вызов API-клиента со страницы.
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/f\/map|\/$/)
  await page.evaluate(async () => {
    // Дергаем заведомо падающий запрос через публичный fetch —
    // API-клиент перехватит и покажет тост problem+json.
    await fetch('/api/v1/fields/00000000-0000-0000-0000-000000000000', {
      headers: { Authorization: `Bearer ${localStorage.getItem('cda.token') ?? ''}` },
    })
  })
  const toast = page.locator('.toast').first()
  await expect(toast).toBeVisible()
  const nav = page.locator('.field__nav')
  await expect(nav).toBeVisible()

  const toastBox = await toast.boundingBox()
  const navBox = await nav.boundingBox()
  expect(navBox).not.toBeNull()
  expect(toastBox).not.toBeNull()
  // Тост целиком выше нижней навигации (не перекрывает её).
  expect(toastBox!.y + toastBox!.height).toBeLessThanOrEqual(navBox!.y + 1)
})

test('выход из полевого режима: кнопка в шапке ведёт в кабинет', async ({ page }) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/f\/map|\/$/)
  if (!page.url().includes('/f/')) {
    await page.goto('/f/map')
  }
  await expect(page.locator('.field__exit')).toBeVisible()
  await page.locator('.field__exit').click()
  await page.waitForURL(/\/fields\/map|\/$/)
  await expect(page.locator('.cabinet__header')).toBeVisible()

  // И через экран «Ещё…» — полноценная кнопка.
  await page.goto('/f/queue')
  await expect(page.locator('.queue__exit')).toBeVisible()
  await page.locator('.queue__exit').click()
  await page.waitForURL(/\/fields\/map|\/$/)
  await expect(page.locator('.cabinet__header')).toBeVisible()
})
