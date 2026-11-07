# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: stand77.spec.ts >> Планы внесения: окно не подходит на 2026-11-12, не оценено без прогноза
- Location: ../.scratch/tatyana.novikova/acceptance77/stand77.spec.ts:73:1

# Error details

```
Error: expect(locator).toContainText(expected) failed

Locator: locator('.tp__window')
Expected substring: "окно не оценено"
Received string:    "⚠ окно не подходитпогодное окно для «Абига-Пик, ВС»2026-11-12: Tср -0.7 °C (вне 5–25), ветер 4.9 м/с (>4)"
Timeout: 15000ms

Call log:
  - Expect "toContainText" locator('.tp__window') with timeout 15000ms
  - waiting for locator('.tp__window')
    33 × locator resolved to <div data-v-78bb68c5="" class="tp__window" data-testid="tp-window">…</div>
       - unexpected value "⚠ окно не подходитпогодное окно для «Абига-Пик, ВС»2026-11-12: Tср -0.7 °C (вне 5–25), ветер 4.9 м/с (>4)"

```

```yaml
- text: ⚠ окно не подходит погодное окно для «Абига-Пик, ВС»
- paragraph: "2026-11-12: Tср -0.7 °C (вне 5–25), ветер 4.9 м/с (>4)"
```

# Test source

```ts
  1   | // Независимая сквозная приёмка этапа v2 (#77): удобрения и СЗР.
  2   | // Стенд LAN http://192.168.99.201:8080 (публичный домен 530, чинит Артём).
  3   | // Запуск:
  4   | //   export PATH=/swarm/sessions/002-tsda/workspace/.scratch/aleksey.dorokhov/tools/node/bin:$PATH
  5   | //   export NODE_PATH=/swarm/sessions/002-tsda/workspace/frontend/node_modules
  6   | //   cd frontend && npx playwright test ../.scratch/tatyana.novikova/acceptance77/stand77.spec.ts -c ../.scratch/tatyana.novikova/acceptance77/pw.config.ts --reporter=line
  7   | import { expect, test, type Page } from '@playwright/test'
  8   | 
  9   | const BASE = 'http://192.168.99.201:8080'
  10  | const FIELD_ID = '8826f29b-ffff-4b63-a71b-848c6b4e347e' // 14/ЖАМ/74га
  11  | 
  12  | async function login(page: Page, user = 'demo_agronomist') {
  13  |   await page.goto(`${BASE}/login`)
  14  |   await page.getByLabel(/логин/i).fill(user)
  15  |   await page.getByLabel(/пароль/i).fill('demo123')
  16  |   await page.getByRole('button', { name: /войти/i }).click()
  17  |   await page.waitForURL(/fields\/map/, { timeout: 20000 })
  18  | }
  19  | 
  20  | // ── E-05v2 Календарь работ: план и факт, история и планы, фильтры ──────────
  21  | test('Календарь: план 2026, факт 2022–2025, фильтры план/факт', async ({ page }) => {
  22  |   await login(page)
  23  |   await page.goto(`${BASE}/calendar`)
  24  |   await expect(page.getByRole('heading', { name: 'Календарь работ' })).toBeVisible({ timeout: 15000 })
  25  | 
  26  |   // Текущий месяц (ноябрь 2026): есть планов — тестовый draft 2026-11-08 и
  27  |   // план приёмки 2026-11-10 (approved). Фактов за окно месяца нет.
  28  |   const legendNov = page.locator('.cal__muted')
  29  |   await expect(legendNov).toContainText(/планов [1-9]/, { timeout: 15000 })
  30  |   console.log('ноябрь 2026:', await legendNov.innerText())
  31  | 
  32  |   // Фильтр «Только план» скрывает факт-чипы.
  33  |   await page.locator('select[aria-label="План или факт"]').selectOption('plan')
  34  |   await expect(legendNov).toContainText(/фактов 0/)
  35  | 
  36  |   // История фактических работ 2022–2025 (seed-operations, 2161 операция).
  37  |   await page.locator('input[aria-label="Месяц"]').fill('2024-05')
  38  |   await expect(legendNov).toContainText(/фактов [1-9]/, { timeout: 15000 })
  39  |   console.log('май 2024:', await legendNov.innerText())
  40  | 
  41  |   // Панель дня: клик по дню с чипом факта открывает список.
  42  |   await page.locator('select[aria-label="План или факт"]').selectOption('all')
  43  |   const dayWithFact = page.locator('.cal__day', { has: page.locator('.cal__chip--fact') }).first()
  44  |   await dayWithFact.click()
  45  |   const panel = page.getByTestId('cal-day-panel')
  46  |   await expect(panel).toBeVisible()
  47  |   await expect(panel.locator('.cal__entry--fact').first()).toBeVisible()
  48  | })
  49  | 
  50  | // ── Карточка поля, вкладка «Работы»: план и факт одной лентой ─────────────
  51  | test('Поле 360° → Работы: планы 2026 и факты 2022–2025', async ({ page }) => {
  52  |   await login(page)
  53  |   await page.goto(`${BASE}/fields/${FIELD_ID}`)
  54  |   await expect(page.getByText('14/ЖАМ/74га')).toBeVisible({ timeout: 15000 })
  55  | 
  56  |   await page.getByRole('tab', { name: 'Работы' }).click()
  57  |   const works = page.locator('.card360__works-list')
  58  |   await expect(works).toBeVisible({ timeout: 15000 })
  59  |   // Планы 2026 (seed ТМТД 2026-03-17 + планы приёмки) и факты истории.
  60  |   await expect(works.locator('.card360__works-item--plan').first()).toBeVisible()
  61  |   await expect(works.locator('.card360__works-item--fact').first()).toBeVisible()
  62  |   await expect(works.getByText('ТМТД, ВСК')).toBeVisible()
  63  |   const planCount = await works.locator('.card360__works-item--plan').count()
  64  |   const factCount = await works.locator('.card360__works-item--fact').count()
  65  |   console.log(`вкладка Работы: планов ${planCount}, фактов ${factCount}`)
  66  |   // Свежие сверху: первая запись — самая поздняя дата.
  67  |   const firstDate = await works.locator('.card360__works-date').first().innerText()
  68  |   console.log('первая (самая свежая) запись:', firstDate)
  69  |   expect(factCount).toBeGreaterThanOrEqual(20) // 4 года истории по полю
  70  | })
  71  | 
  72  | // ── E-05 /treatment-plans: WEATHER-WINDOW при создании плана spraying ─────
  73  | test('Планы внесения: окно не подходит на 2026-11-12, не оценено без прогноза', async ({ page }) => {
  74  |   await login(page)
  75  |   await page.goto(`${BASE}/treatment-plans`)
  76  |   await expect(page.getByRole('heading', { name: /планы удобрений/i })).toBeVisible({ timeout: 15000 })
  77  | 
  78  |   // Форма: цикл поля 14/ЖАМ (2026, Соя), Абига-Пик, spraying, 2026-11-12 —
  79  |   // в живом прогнозе мороз/ветер → «окно не подходит» + detail.
  80  |   await page.locator('select.tp__input').first().selectOption({ label: '14/ЖАМ/74га · 2026 · Соя' })
  81  |   await page.getByLabel('Препарат / удобрение').selectOption({ label: 'Абига-Пик, ВС (СЗР)' })
  82  |   await page.getByLabel(/^Доза/).fill('2')
  83  |   await page.getByLabel('Плановая дата').fill('2026-11-12')
  84  |   await page.getByRole('button', { name: 'Создать черновик' }).click()
  85  | 
  86  |   const win = page.locator('.tp__window')
  87  |   await expect(win).toBeVisible({ timeout: 15000 })
  88  |   await expect(win).toContainText('окно не подходит')
  89  |   await expect(win).toContainText(/2026-11-12.*(°C|м\/с)/)
  90  |   console.log('окно 2026-11-12:', (await win.innerText()).replace(/\n/g, ' | '))
  91  | 
  92  |   // Дата без прогноза (июль 2026) → weather_window: null → «окно не оценено».
  93  |   await page.getByLabel('Плановая дата').fill('2026-07-01')
  94  |   await page.getByRole('button', { name: 'Создать черновик' }).click()
> 95  |   await expect(win).toContainText('окно не оценено', { timeout: 15000 })
      |                     ^ Error: expect(locator).toContainText(expected) failed
  96  |   console.log('окно 2026-07-01:', (await win.innerText()).replace(/\n/g, ' | '))
  97  | })
  98  | 
  99  | // ── API-16 в UI: менеджер согласует, окно показывается в диалоге ──────────
  100 | test('Менеджер: согласование черновика, окно в диалоге', async ({ page }) => {
  101 |   await login(page, 'demo_manager')
  102 |   await page.goto(`${BASE}/treatment-plans`)
  103 |   // Таблица с колонкой действий для менеджера.
  104 |   const approveBtn = page.locator('.tp__approve').first()
  105 |   await expect(approveBtn).toBeVisible({ timeout: 15000 })
  106 |   // Диалог согласования: статус меняется, окно (или «не оценено») показывается.
  107 |   await approveBtn.click()
  108 |   const dialog = page.getByTestId('tp-approve-dialog')
  109 |   await expect(dialog).toBeVisible({ timeout: 15000 })
  110 |   await expect(dialog.getByText(/согласован/i).first()).toBeVisible({ timeout: 15000 })
  111 |   console.log('диалог согласования:', (await dialog.innerText()).replace(/\n/g, ' | '))
  112 |   // Погодное окно в диалоге (для spraying) или пометка «не оценено».
  113 |   const winBlock = dialog.getByTestId('tp-approve-window')
  114 |   if (await winBlock.count()) {
  115 |     console.log('окно после approve:', (await winBlock.innerText()).replace(/\n/g, ' | '))
  116 |   } else {
  117 |     console.log('окно после approve: не spraying — блока окна нет')
  118 |   }
  119 | })
  120 | 
  121 | // ── Регресс v1: карта, NDVI карточки, погода, 401 ─────────────────────────
  122 | test('Регресс v1: карта 90 полей, NDVI-таймлайн, погода', async ({ page }) => {
  123 |   await login(page)
  124 |   const labels = page.locator('.field-label')
  125 |   await expect.poll(async () => labels.count(), { timeout: 30000 }).toBe(90)
  126 | 
  127 |   await page.goto(`${BASE}/fields/${FIELD_ID}`)
  128 |   await expect(page.getByText('2026-11-05', { exact: true })).toBeVisible({ timeout: 15000 })
  129 | 
  130 |   await page.goto(`${BASE}/weather`)
  131 |   await expect(page.getByText(/сервер недоступен|нет связи/i)).toHaveCount(0)
  132 | })
  133 | 
  134 | test('Регресс v1: истёкший токен → /login', async ({ page }) => {
  135 |   await login(page)
  136 |   await page.evaluate(() => {
  137 |     const token = localStorage.getItem('cda.token')!
  138 |     const [, payload] = token.split('.')
  139 |     const claims = JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')))
  140 |     claims.exp = Math.floor(Date.now() / 1000) - 60
  141 |     const b64 = btoa(JSON.stringify(claims)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  142 |     localStorage.setItem('cda.token', `${token.split('.')[0]}.${b64}.${token.split('.')[2]}`)
  143 |   })
  144 |   await page.goto(`${BASE}/fields/map`)
  145 |   await page.waitForURL(/\/login/, { timeout: 15000 })
  146 | })
  147 | 
```