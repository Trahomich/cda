# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: offline.spec.ts >> офлайн-ввод 3 операций и синхронизация (10-mobile-pwa §3.4)
- Location: e2e/offline.spec.ts:6:1

# Error details

```
Error: expect(page).toHaveURL(expected) failed

Expected pattern: /fields\/map|\/f\//
Received string:  "http://localhost:5173/login"
Timeout: 5000ms

Call log:
  - Expect "toHaveURL" with timeout 5000ms
    11 × locator resolved to <html lang="ru">…</html>
       - unexpected value "http://localhost:5173/login"

```

```yaml
- heading "Цифровой двойник агронома" [level=1]
- paragraph: КФХ «Восход»
- text: Логин
- textbox "Логин": agronom
- text: Пароль
- textbox "Пароль": demo
- button "Войти"
- paragraph: "Демо-доступ (мок): agronom / demo"
- status
```

# Test source

```ts
  1   | import { expect, test } from '@playwright/test'
  2   | 
  3   | // Офлайн-сценарий docs/10-mobile-pwa.md §3.4 (приёмка MVP §6.2):
  4   | // самолётный режим → ввод 3 операций → выход из режима → синхронизация →
  5   | // все 3 записи появляются в журнале с датами и исполнителем.
  6   | test('офлайн-ввод 3 операций и синхронизация (10-mobile-pwa §3.4)', async ({ page }) => {
  7   |   test.setTimeout(120_000)
  8   | 
  9   |   // Вход (мок: agronom/demo).
  10  |   await page.goto('/login')
  11  |   await page.fill('#login', 'agronom')
  12  |   await page.fill('#password', 'demo')
  13  |   await page.getByRole('button', { name: 'Войти' }).click()
> 14  |   await expect(page).toHaveURL(/fields\/map|\/f\//)
      |                      ^ Error: expect(page).toHaveURL(expected) failed
  15  | 
  16  |   // Полевой режим → ввод операции. Кэш полей заполняется при онлайне.
  17  |   // Ждём именно появления опций (>1): not.toHaveCount(1) проходит и при 0,
  18  |   // когда экран ещё догружается ленивыми импортами.
  19  |   await page.goto('/f/operations')
  20  |   await expect
  21  |     .poll(async () => page.locator('#field option').count(), { timeout: 15_000 })
  22  |     .toBeGreaterThan(1)
  23  | 
  24  |   // Предзагружаем экран очереди (E-17): его ленивый чанк должен быть в кэше
  25  |   // модулей ДО офлайна, иначе SPA-переход офлайн упадёт на fetch чанка
  26  |   // (в production то же обеспечивает SW precache).
  27  |   await page.getByRole('button', { name: 'Ещё…', exact: true }).click()
  28  |   await expect(page.locator('.queue__empty')).toBeVisible({ timeout: 15_000 })
  29  |   await page.getByRole('button', { name: 'Работы', exact: true }).click()
  30  |   await expect(page.locator('#field')).toBeVisible({ timeout: 15_000 })
  31  | 
  32  |   // Самолётный режим (CDP Network.emulateNetworkConditions через context API).
  33  |   await page.context().setOffline(true)
  34  | 
  35  |   // Ввод 3 операций офлайн (площади валидны для Степное-1: 84.5 га, лимит 88.7).
  36  |   for (let i = 1; i <= 3; i++) {
  37  |     await page.selectOption('#field', { index: 1 })
  38  |     await page.fill('#area', `1${i}`)
  39  |     await page.getByRole('button', { name: /ЗАПИСАТЬ/ }).click()
  40  |     await expect(page.locator('.op-entry__queue-btn')).toContainText(`⛁ ${i}`)
  41  |   }
  42  | 
  43  |   // Очередь E-17 показывает 3 записи со статусом «ждёт сети».
  44  |   // В офлайне нельзя page.goto (full page load) — ходим SPA-навигацией.
  45  |   await page.getByRole('button', { name: 'Ещё…', exact: true }).click()
  46  |   await expect(page.locator('.queue__item')).toHaveCount(3)
  47  |   await expect(page.locator('.queue__item').first()).toContainText('ждёт сети')
  48  | 
  49  |   // Выход из самолётного режима: событие 'online' запускает автосинхронизацию.
  50  |   await page.context().setOffline(false)
  51  |   await expect(page.locator('.queue__item')).toHaveCount(0, { timeout: 30_000 })
  52  |   await expect(page.locator('.queue__empty')).toBeVisible()
  53  | 
  54  |   // Все 3 записи появились в журнале (API-18) с датами и исполнителем.
  55  |   await page.goto('/f/operations')
  56  |   const journalItems = page.locator('.op-entry__journal-list li')
  57  |   await expect(journalItems).toHaveCount(3, { timeout: 15_000 })
  58  |   const firstRow = (await journalItems.first().textContent()) ?? ''
  59  |   expect(firstRow).toContain(new Date().toISOString().slice(0, 10))
  60  |   expect(firstRow.length).toBeGreaterThan(0)
  61  | 
  62  |   // Идемпотентность: повторная отправка того же client_id не создаёт дубль.
  63  |   // (проверяется на стороне мока — POST с тем же client_id возвращает 201
  64  |   // с той же операцией; здесь достаточно, что в журнале ровно 3 записи).
  65  | })
  66  | 
  67  | test('422 не теряет запись: причина из problem+json показывается в очереди', async ({ page }) => {
  68  |   await page.goto('/login')
  69  |   await page.fill('#login', 'agronom')
  70  |   await page.fill('#password', 'demo')
  71  |   await page.getByRole('button', { name: 'Войти' }).click()
  72  |   await expect(page).toHaveURL(/fields\/map|\/f\//)
  73  | 
  74  |   await page.goto('/f/operations')
  75  |   await expect
  76  |     .poll(async () => page.locator('#field option').count(), { timeout: 15_000 })
  77  |     .toBeGreaterThan(1)
  78  |   // Первое поле демо-хозяйства — «Степное-1», 84.5 га; RULE-AGRO-12: лимит 1.05×.
  79  |   await page.selectOption('#field', { index: 1 })
  80  |   await page.fill('#area', '999') // заведомо больше 1,05 × площади
  81  |   await page.context().setOffline(true)
  82  |   await page.getByRole('button', { name: /ЗАПИСАТЬ/ }).click()
  83  |   await expect(page.locator('.op-entry__queue-btn')).toContainText('⛁ 1')
  84  | 
  85  |   await page.context().setOffline(false)
  86  |   // Автосинхронизация: 422 → запись остаётся со статусом error и причиной.
  87  |   await page.goto('/f/queue')
  88  |   const item = page.locator('.queue__item').first()
  89  |   await expect(item).toContainText('отклонено сервером', { timeout: 30_000 })
  90  |   await expect(page.locator('.queue__error').first()).toContainText('RULE-AGRO-12')
  91  | 
  92  |   // Правка площади → снова в очередь (pending) → повторная синхронизация.
  93  |   await page.getByRole('button', { name: 'Править' }).click()
  94  |   await page.locator('.queue__edit input').fill('10')
  95  |   await page.getByRole('button', { name: 'Сохранить и в очередь' }).click()
  96  |   await expect(item).toContainText('ждёт сети')
  97  | 
  98  |   await page.getByRole('button', { name: 'Синхронизировать' }).click()
  99  |   await expect(page.locator('.queue__item')).toHaveCount(0, { timeout: 30_000 })
  100 | })
  101 | 
```