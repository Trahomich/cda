# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: stand.spec.ts >> E-03 Поле 360°: карточка реального поля — NDVI-таймлайн, севооборот, погода
- Location: ../.scratch/tatyana.novikova/acceptance53/stand.spec.ts:34:1

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator:  getByText('2026-11-05').first()
Expected: visible
Received: hidden
Timeout:  15000ms

Call log:
  - Expect "toBeVisible" getByText('2026-11-05').first() with timeout 15000ms
  - waiting for getByText('2026-11-05').first()
    30 × locator resolved to <title data-v-c0ec5f5a="">2026-11-05 · NDVI 0.165 · облачность 0 %</title>
       - unexpected value "hidden"

```

```yaml
- banner:
  - text: "ЦДА · Восход [роль: Агроном]"
  - link "Поля":
    - /url: /fields/map
  - link "Севооборот":
    - /url: /rotation
  - link "Планы СЗР":
    - /url: /treatment-plans
  - link "Погода":
    - /url: /weather
  - link "Журнал":
    - /url: /operations
  - link "Техника":
    - /url: /machinery
  - link "Справочники":
    - /url: /references
  - link "Полевой режим":
    - /url: /f/map
  - button "Выйти"
- main:
  - link "‹ Поля":
    - /url: /fields/map
  - heading "14/ЖАМ/74га · 78,12 га" [level=1]
  - button "Изменить ✎" [disabled]
  - heading "Контур поля" [level=2]
  - img "Контур поля"
  - heading "Севооборот по годам" [level=2]
  - text: 2030 Овёс 2029 Озимая 2028 Подсолнечник 2027 Горох 2026 Соя 2025 Подсолнечник 2024 Яровая 2023 Пар +1 …
  - paragraph: Цвет полосы — культура справочника (API-48); пунктир — план, обводка — текущий год.
  - heading "NDVI-таймлайн (сезон 2026)" [level=2]
  - img "NDVI-таймлайн сезона"
  - text: 2026-11-05 2026-10-28 2026-10-26
  - paragraph: "Дата последнего снимка: 2026-10-26 · ◌ — облачные точки"
  - heading "Агрохимия" [level=2]
  - paragraph: Данных агрохимии нет (импорт — API-30)
  - heading "Погода (сейчас + 7 сут)" [level=2]
  - paragraph: "Сейчас: 6,3 °C, ветер 9,3 м/с, влажность 70 % Осадки за 7 сут: 0,8 мм · GDD: 2.5 °C·сут"
  - table:
    - rowgroup:
      - row "Дата °C Осадки, мм":
        - columnheader "Дата"
        - columnheader "°C"
        - columnheader "Осадки, мм"
    - rowgroup:
      - row "11-06 2.3…10.3 0,3":
        - cell "11-06"
        - cell "2.3…10.3"
        - cell "0,3"
      - row "11-07 -2.6…4.1 0":
        - cell "11-07"
        - cell "-2.6…4.1"
        - cell "0"
      - row "11-08 -1.4…4.2 2,6":
        - cell "11-08"
        - cell "-1.4…4.2"
        - cell "2,6"
      - row "11-09 -2.1…1.3 3,2":
        - cell "11-09"
        - cell "-2.1…1.3"
        - cell "3,2"
      - row "11-10 -5.4…-0.2 0":
        - cell "11-10"
        - cell "-5.4…-0.2"
        - cell "0"
      - row "11-11 -3.2…0.6 0":
        - cell "11-11"
        - cell "-3.2…0.6"
        - cell "0"
      - row "11-12 -1.9…1 0":
        - cell "11-12"
        - cell "-1.9…1"
        - cell "0"
  - heading "Планы и журнал 2026" [level=2]
  - list:
    - listitem: Планов внесения нет
  - heading "Журнал (факты)" [level=3]
  - list:
    - listitem: Фактов работ пока нет
- status
```

# Test source

```ts
  1  | // Независимая приёмка этапа v1 (#53) на публичном стенде https://cda.oblaka.pw.
  2  | // Реальные данные: 90 полей, живая погода/NDVI. Запуск:
  3  | //   cd frontend && npx playwright test ../.scratch/tatyana.novikova/acceptance53/stand.spec.ts -c ../.scratch/tatyana.novikova/acceptance53/pw.config.ts
  4  | import { expect, test } from '@playwright/test'
  5  | 
  6  | const BASE = 'https://cda.oblaka.pw'
  7  | 
  8  | async function login(page: import('@playwright/test').Page) {
  9  |   await page.goto(`${BASE}/login`)
  10 |   await page.getByLabel(/логин/i).fill('demo_agronomist')
  11 |   await page.getByLabel(/пароль/i).fill('demo123')
  12 |   await page.getByRole('button', { name: /войти/i }).click()
  13 |   await page.waitForURL(/fields\/map/, { timeout: 20000 })
  14 | }
  15 | 
  16 | test('E-02 карта: 90 полей реального хозяйства, легенда по культурам, производительность', async ({
  17 |   page,
  18 | }) => {
  19 |   const t0 = Date.now()
  20 |   await login(page)
  21 |   // Все 90 номеров полей в центроидах — производительность карты на реальных данных.
  22 |   const labels = page.locator('.field-label')
  23 |   await expect.poll(async () => labels.count(), { timeout: 30000 }).toBe(90)
  24 |   const loadedMs = Date.now() - t0
  25 |   console.log(`карта 90 полей от логина до 90 подписей: ${loadedMs} мс`)
  26 |   // Легенда культур (API-48: 27 культур в справочнике; на карте — культуры 2026 г.: 10).
  27 |   const legend = page.locator('.map-legend__name')
  28 |   const legendCount = await legend.count()
  29 |   console.log(`легенда: ${legendCount} культур`)
  30 |   expect(legendCount).toBeGreaterThanOrEqual(8)
  31 |   await expect(legend.filter({ hasText: 'Соя' })).toHaveCount(1)
  32 | })
  33 | 
  34 | test('E-03 Поле 360°: карточка реального поля — NDVI-таймлайн, севооборот, погода', async ({
  35 |   page,
  36 | }) => {
  37 |   await login(page)
  38 |   // Первое поле из списка API (реальный id стенда, см. fields.json приёмки).
  39 |   await page.goto(`${BASE}/fields/8826f29b-ffff-4b63-a71b-848c6b4e347e`)
  40 |   await expect(page.getByText('14/ЖАМ/74га')).toBeVisible({ timeout: 15000 })
  41 |   // NDVI-таймлайн: живые даты Sentinel-2 ноября (title точки + подпись оси).
> 42 |   await expect(page.getByText('2026-11-05').first()).toBeVisible({ timeout: 15000 })
     |                                                      ^ Error: expect(locator).toBeVisible() failed
  43 |   await expect(page.getByText(/NDVI 0\.1\d/).first()).toBeVisible()
  44 | })
  45 | 
  46 | test('E-10 Погода: живой факт и прогноз на стенде', async ({ page }) => {
  47 |   await login(page)
  48 |   await page.goto(`${BASE}/weather`)
  49 |   await expect(page.getByText(/погода/i).first()).toBeVisible({ timeout: 15000 })
  50 |   // Живые значения open-meteo (статус источника не error).
  51 |   await expect(page.getByText(/сервер недоступен|нет связи/i)).toHaveCount(0)
  52 | })
  53 | 
  54 | test('Истёкший токен (баг #58): подмена → /login, один тост, без «нет связи»', async ({
  55 |   page,
  56 | }) => {
  57 |   await login(page)
  58 |   await page.evaluate(() => {
  59 |     const token = localStorage.getItem('cda.token')!
  60 |     const [, payload] = token.split('.')
  61 |     const claims = JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')))
  62 |     const b64 = (s: string) => btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  63 |     claims.exp = Math.floor(Date.now() / 1000) - 3600
  64 |     localStorage.setItem(
  65 |       'cda.token',
  66 |       `${b64(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))}.${b64(JSON.stringify(claims))}.x`,
  67 |     )
  68 |   })
  69 |   await page.goto(`${BASE}/fields/map`)
  70 |   await page.waitForURL(/\/login\?redirect=/, { timeout: 20000 })
  71 |   const text = (await page.locator('[class*="toast"]').allTextContents()).join('\n').toLowerCase()
  72 |   expect(text).toContain('сессия истекла')
  73 |   expect(text).not.toContain('нет связи')
  74 | })
  75 | 
```