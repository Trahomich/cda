# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: offline.spec.ts >> офлайн-ввод 3 операций и синхронизация (10-mobile-pwa §3.4)
- Location: e2e/offline.spec.ts:6:1

# Error details

```
Test timeout of 120000ms exceeded.
```

```
Error: page.selectOption: Test timeout of 120000ms exceeded.
Call log:
  - waiting for locator('#field')

```

# Page snapshot

```yaml
- generic [ref=f1e2]:
  - status
```

# Test source

```ts
  1  | import { expect, test } from '@playwright/test'
  2  | 
  3  | // Офлайн-сценарий docs/10-mobile-pwa.md §3.4 (приёмка MVP §6.2):
  4  | // самолётный режим → ввод 3 операций → выход из режима → синхронизация →
  5  | // все 3 записи появляются в журнале с датами и исполнителем.
  6  | test('офлайн-ввод 3 операций и синхронизация (10-mobile-pwa §3.4)', async ({ page }) => {
  7  |   test.setTimeout(120_000)
  8  | 
  9  |   // Вход (мок: agronom/demo).
  10 |   await page.goto('/login')
  11 |   await page.fill('#login', 'agronom')
  12 |   await page.fill('#password', 'demo')
  13 |   await page.getByRole('button', { name: 'Войти' }).click()
  14 |   await expect(page).toHaveURL(/fields\/map|\/f\//)
  15 | 
  16 |   // Полевой режим → ввод операции. Кэш полей заполняется при онлайне.
  17 |   await page.goto('/f/operations')
  18 |   await expect(page.locator('#field option')).not.toHaveCount(1, { timeout: 15_000 })
  19 | 
  20 |   // Самолётный режим (CDP Network.emulateNetworkConditions через context API).
  21 |   await page.context().setOffline(true)
  22 | 
  23 |   // Ввод 3 операций офлайн.
  24 |   for (let i = 1; i <= 3; i++) {
> 25 |     await page.selectOption('#field', { index: 1 })
     |                ^ Error: page.selectOption: Test timeout of 120000ms exceeded.
  26 |     await page.fill('#area', `1${i}0`)
  27 |     await page.getByRole('button', { name: /ЗАПИСАТЬ/ }).click()
  28 |     await expect(page.locator('.op-entry__queue-btn')).toContainText(`⛁ ${i}`)
  29 |   }
  30 | 
  31 |   // Очередь E-17 показывает 3 записи со статусом «ждёт сети».
  32 |   await page.goto('/f/queue')
  33 |   await expect(page.locator('.queue__item')).toHaveCount(3)
  34 |   await expect(page.locator('.queue__item').first()).toContainText('ждёт сети')
  35 | 
  36 |   // Выход из самолётного режима: событие 'online' запускает автосинхронизацию.
  37 |   await page.context().setOffline(false)
  38 |   await expect(page.locator('.queue__item')).toHaveCount(0, { timeout: 30_000 })
  39 |   await expect(page.locator('.queue__empty')).toBeVisible()
  40 | 
  41 |   // Все 3 записи появились в журнале (API-18) с датами и исполнителем.
  42 |   await page.goto('/f/operations')
  43 |   const journalItems = page.locator('.op-entry__journal-list li')
  44 |   await expect(journalItems).toHaveCount(3, { timeout: 15_000 })
  45 |   const firstRow = (await journalItems.first().textContent()) ?? ''
  46 |   expect(firstRow).toContain(new Date().toISOString().slice(0, 10))
  47 |   expect(firstRow.length).toBeGreaterThan(0)
  48 | 
  49 |   // Идемпотентность: повторная отправка того же client_id не создаёт дубль.
  50 |   // (проверяется на стороне мока — POST с тем же client_id возвращает 201
  51 |   // с той же операцией; здесь достаточно, что в журнале ровно 3 записи).
  52 | })
  53 | 
  54 | test('422 не теряет запись: причина из problem+json показывается в очереди', async ({ page }) => {
  55 |   await page.goto('/login')
  56 |   await page.fill('#login', 'agronom')
  57 |   await page.fill('#password', 'demo')
  58 |   await page.getByRole('button', { name: 'Войти' }).click()
  59 |   await expect(page).toHaveURL(/fields\/map|\/f\//)
  60 | 
  61 |   await page.goto('/f/operations')
  62 |   await expect(page.locator('#field option')).not.toHaveCount(1, { timeout: 15_000 })
  63 |   // Первое поле демо-хозяйства — «Степное-1», 84.5 га; RULE-AGRO-12: лимит 1.05×.
  64 |   await page.selectOption('#field', { index: 1 })
  65 |   await page.fill('#area', '999') // заведомо больше 1,05 × площади
  66 |   await page.context().setOffline(true)
  67 |   await page.getByRole('button', { name: /ЗАПИСАТЬ/ }).click()
  68 |   await expect(page.locator('.op-entry__queue-btn')).toContainText('⛁ 1')
  69 | 
  70 |   await page.context().setOffline(false)
  71 |   // Автосинхронизация: 422 → запись остаётся со статусом error и причиной.
  72 |   await page.goto('/f/queue')
  73 |   const item = page.locator('.queue__item').first()
  74 |   await expect(item).toContainText('отклонено сервером', { timeout: 30_000 })
  75 |   await expect(page.locator('.queue__error').first()).toContainText('RULE-AGRO-12')
  76 | 
  77 |   // Правка площади → снова в очередь (pending) → повторная синхронизация.
  78 |   await page.getByRole('button', { name: 'Править' }).click()
  79 |   await page.locator('.queue__edit input').fill('10')
  80 |   await page.getByRole('button', { name: 'Сохранить и в очередь' }).click()
  81 |   await expect(item).toContainText('ждёт сети')
  82 | 
  83 |   await page.getByRole('button', { name: 'Синхронизировать' }).click()
  84 |   await expect(page.locator('.queue__item')).toHaveCount(0, { timeout: 30_000 })
  85 | })
  86 | 
```