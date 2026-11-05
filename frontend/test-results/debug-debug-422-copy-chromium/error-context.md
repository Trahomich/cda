# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: debug.spec.ts >> debug 422 copy
- Location: e2e/debug.spec.ts:4:1

# Error details

```
Test timeout of 60000ms exceeded.
```

```
Error: expect(page).toHaveURL(expected) failed

Expected pattern: /fields\/map|\/f\//
Received string:  "http://localhost:5173/login"

Call log:
  - Expect "toHaveURL" with timeout 5000ms
    6 × locator resolved to <html lang="ru">…</html>
      - unexpected value "http://localhost:5173/login"
  - Test timeout of 60000ms exceeded.

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
  1  | import { expect, test } from '@playwright/test'
  2  | 
  3  | // Точная копия теста «422» из offline.spec.ts + диагностика.
  4  | test('debug 422 copy', async ({ page }) => {
  5  |   page.on('console', (m) => console.log('PAGE:', m.type(), m.text()))
  6  |   page.on('pageerror', (e) => console.log('PAGEERROR:', e.message))
  7  |   await page.goto('/login')
  8  |   await page.fill('#login', 'agronom')
  9  |   await page.fill('#password', 'demo')
  10 |   await page.getByRole('button', { name: 'Войти' }).click()
> 11 |   await expect(page).toHaveURL(/fields\/map|\/f\//)
     |                      ^ Error: expect(page).toHaveURL(expected) failed
  12 | 
  13 |   await page.goto('/f/operations')
  14 |   await expect
  15 |     .poll(async () => page.locator('#field option').count(), { timeout: 15_000 })
  16 |     .toBeGreaterThan(1)
  17 |   await page.selectOption('#field', { index: 1 })
  18 |   await page.fill('#area', '999')
  19 |   await page.context().setOffline(true)
  20 |   await page.waitForTimeout(1000)
  21 |   const areaVal = await page.locator('#area').inputValue().catch((e) => `ERR:${e.message}`)
  22 |   const fieldVal = await page.locator('#field').inputValue().catch((e) => `ERR:${e.message}`)
  23 |   console.log('BEFORE CLICK area=', areaVal, 'field=', fieldVal)
  24 |   await page.getByRole('button', { name: /ЗАПИСАТЬ/ }).click()
  25 |   await page.waitForTimeout(3000)
  26 |   const btn = await page.locator('.op-entry__queue-btn').textContent()
  27 |   console.log('BTN:', btn)
  28 |   const state = await page.evaluate(async () => {
  29 |     const db = await (indexedDB as any).databases?.()
  30 |     return db?.map((d: any) => d.name)
  31 |   })
  32 |   console.log('DBS:', JSON.stringify(state))
  33 |   const toasts = await page.evaluate(() => Array.from(document.querySelectorAll('[class*=toast]')).map((n) => n.textContent))
  34 |   console.log('TOASTS:', JSON.stringify(toasts))
  35 | })
  36 | 
```