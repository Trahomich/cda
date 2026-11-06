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

Locator: getByText('05.11.2026').or(getByText('2026-11-05'))
Expected: visible
Error: strict mode violation: getByText('05.11.2026').or(getByText('2026-11-05')) resolved to 2 elements:
    1) <title data-v-c0ec5f5a="">2026-11-05 · NDVI 0.165 · облачность 0 %</title> aka getByText('2026-11-05 · NDVI 0.165')
    2) <span data-v-c0ec5f5a="">2026-11-05</span> aka getByText('2026-11-05', { exact: true })

Call log:
  - Expect "toBeVisible" getByText('05.11.2026').or(getByText('2026-11-05')) with timeout 15000ms
  - waiting for getByText('05.11.2026').or(getByText('2026-11-05'))

```

# Page snapshot

```yaml
- generic [ref=f1e2]:
  - generic [ref=f1e3]:
    - banner [ref=f1e4]:
      - generic [ref=f1e5]: ЦДА · Восход
      - generic [ref=f1e6]: "[роль: Агроном]"
      - link "Поля" [ref=f1e7] [cursor=pointer]:
        - /url: /fields/map
      - link "Севооборот" [ref=f1e8] [cursor=pointer]:
        - /url: /rotation
      - link "Планы СЗР" [ref=f1e9] [cursor=pointer]:
        - /url: /treatment-plans
      - link "Погода" [ref=f1e10] [cursor=pointer]:
        - /url: /weather
      - link "Журнал" [ref=f1e11] [cursor=pointer]:
        - /url: /operations
      - link "Техника" [ref=f1e12] [cursor=pointer]:
        - /url: /machinery
      - link "Справочники" [ref=f1e13] [cursor=pointer]:
        - /url: /references
      - link "Полевой режим" [ref=f1e14] [cursor=pointer]:
        - /url: /f/map
      - button "Выйти" [ref=f1e15] [cursor=pointer]
    - main [ref=f1e16]:
      - generic [ref=f1e17]:
        - generic [ref=f1e18]:
          - link "‹ Поля" [ref=f1e19] [cursor=pointer]:
            - /url: /fields/map
          - heading "14/ЖАМ/74га · 78,12 га" [level=1] [ref=f1e20]
          - button "Изменить ✎" [disabled] [ref=f1e21]
        - generic [ref=f1e22]:
          - generic [ref=f1e23]:
            - heading "Контур поля" [level=2] [ref=f1e24]
            - img "Контур поля" [ref=f1e25]
            - heading "Севооборот по годам" [level=2] [ref=f1e27]
            - generic [ref=f1e28]:
              - generic "2030 · Овёс · запланирован" [ref=f1e29]:
                - generic [ref=f1e30]: "2030"
                - generic [ref=f1e32]: Овёс
              - generic "2029 · Озимая пшеница · запланирован" [ref=f1e33]:
                - generic [ref=f1e34]: "2029"
                - generic [ref=f1e36]: Озимая
              - generic "2028 · Подсолнечник · запланирован" [ref=f1e37]:
                - generic [ref=f1e38]: "2028"
                - generic [ref=f1e40]: Подсолнечник
              - generic "2027 · Горох · запланирован" [ref=f1e41]:
                - generic [ref=f1e42]: "2027"
                - generic [ref=f1e44]: Горох
              - generic "2026 · Соя · текущий" [ref=f1e45]:
                - generic [ref=f1e46]: "2026"
                - generic [ref=f1e48]: Соя
              - generic "2025 · Подсолнечник · прошедший" [ref=f1e49]:
                - generic [ref=f1e50]: "2025"
                - generic [ref=f1e52]: Подсолнечник
              - generic "2024 · Яровая пшеница · прошедший" [ref=f1e53]:
                - generic [ref=f1e54]: "2024"
                - generic [ref=f1e56]: Яровая
              - generic "2023 · Пар · прошедший" [ref=f1e57]:
                - generic [ref=f1e58]: "2023"
                - generic [ref=f1e60]: Пар
              - 'generic "Ещё 1 гг. раньше: 2022 — Кукуруза" [ref=f1e61]': +1 …
            - paragraph [ref=f1e62]: Цвет полосы — культура справочника (API-48); пунктир — план, обводка — текущий год.
          - generic [ref=f1e63]:
            - heading "NDVI-таймлайн (сезон 2026)" [level=2] [ref=f1e64]
            - generic [ref=f1e65]:
              - img "NDVI-таймлайн сезона" [ref=f1e66]:
                - generic "2026-11-05 · NDVI 0.165 · облачность 0 %" [ref=f1e68]
                - generic "2026-10-31 · NDVI 0.151 · облачность 0 %" [ref=f1e69]
                - generic "2026-10-28 · NDVI 0.171 · облачность 0 %" [ref=f1e70]
                - generic "2026-10-26 · NDVI 0.168 · облачность 0 %" [ref=f1e71]
              - generic [ref=f1e72]:
                - generic [ref=f1e73]: 2026-11-05
                - generic [ref=f1e74]: 2026-10-28
                - generic [ref=f1e75]: 2026-10-26
            - paragraph [ref=f1e76]: "Дата последнего снимка: 2026-10-26 · ◌ — облачные точки"
          - generic [ref=f1e77]:
            - heading "Агрохимия" [level=2] [ref=f1e78]
            - paragraph [ref=f1e79]: Данных агрохимии нет (импорт — API-30)
          - generic [ref=f1e80]:
            - heading "Погода (сейчас + 7 сут)" [level=2] [ref=f1e81]
            - paragraph [ref=f1e82]: "Сейчас: 6,3 °C, ветер 9,3 м/с, влажность 70 % Осадки за 7 сут: 0,8 мм · GDD: 2.5 °C·сут"
            - table [ref=f1e83]:
              - rowgroup [ref=f1e84]:
                - row [ref=f1e85]:
                  - columnheader "Дата" [ref=f1e86]
                  - columnheader "°C" [ref=f1e87]
                  - columnheader "Осадки, мм" [ref=f1e88]
              - rowgroup [ref=f1e89]:
                - row [ref=f1e90]:
                  - cell "11-06" [ref=f1e91]
                  - cell "2.3…10.3" [ref=f1e92]
                  - cell "0,3" [ref=f1e93]
                - row [ref=f1e94]:
                  - cell "11-07" [ref=f1e95]
                  - cell "-2.6…4.1" [ref=f1e96]
                  - cell "0" [ref=f1e97]
                - row [ref=f1e98]:
                  - cell "11-08" [ref=f1e99]
                  - cell "-1.4…4.2" [ref=f1e100]
                  - cell "2,6" [ref=f1e101]
                - row [ref=f1e102]:
                  - cell "11-09" [ref=f1e103]
                  - cell "-2.1…1.3" [ref=f1e104]
                  - cell "3,2" [ref=f1e105]
                - row [ref=f1e106]:
                  - cell "11-10" [ref=f1e107]
                  - cell "-5.4…-0.2" [ref=f1e108]
                  - cell "0" [ref=f1e109]
                - row [ref=f1e110]:
                  - cell "11-11" [ref=f1e111]
                  - cell "-3.2…0.6" [ref=f1e112]
                  - cell "0" [ref=f1e113]
                - row [ref=f1e114]:
                  - cell "11-12" [ref=f1e115]
                  - cell "-1.9…1" [ref=f1e116]
                  - cell "0" [ref=f1e117]
          - generic [ref=f1e118]:
            - heading "Планы и журнал 2026" [level=2] [ref=f1e119]
            - list [ref=f1e120]:
              - listitem [ref=f1e121]: Планов внесения нет
            - heading "Журнал (факты)" [level=3] [ref=f1e122]
            - list [ref=f1e123]:
              - listitem [ref=f1e124]: Фактов работ пока нет
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
  41 |   // NDVI-таймлайн: живые даты Sentinel-2 ноября.
> 42 |   await expect(page.getByText('05.11.2026').or(page.getByText('2026-11-05'))).toBeVisible({
     |                                                                               ^ Error: expect(locator).toBeVisible() failed
  43 |     timeout: 15000,
  44 |   })
  45 | })
  46 | 
  47 | test('E-10 Погода: живой факт и прогноз на стенде', async ({ page }) => {
  48 |   await login(page)
  49 |   await page.goto(`${BASE}/weather`)
  50 |   await expect(page.getByText(/погода/i).first()).toBeVisible({ timeout: 15000 })
  51 |   // Живые значения open-meteo (статус источника не error).
  52 |   await expect(page.getByText(/сервер недоступен|нет связи/i)).toHaveCount(0)
  53 | })
  54 | 
  55 | test('Истёкший токен (баг #58): подмена → /login, один тост, без «нет связи»', async ({
  56 |   page,
  57 | }) => {
  58 |   await login(page)
  59 |   await page.evaluate(() => {
  60 |     const token = localStorage.getItem('cda.token')!
  61 |     const [, payload] = token.split('.')
  62 |     const claims = JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')))
  63 |     const b64 = (s: string) => btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  64 |     claims.exp = Math.floor(Date.now() / 1000) - 3600
  65 |     localStorage.setItem(
  66 |       'cda.token',
  67 |       `${b64(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))}.${b64(JSON.stringify(claims))}.x`,
  68 |     )
  69 |   })
  70 |   await page.goto(`${BASE}/fields/map`)
  71 |   await page.waitForURL(/\/login\?redirect=/, { timeout: 20000 })
  72 |   const text = (await page.locator('[class*="toast"]').allTextContents()).join('\n').toLowerCase()
  73 |   expect(text).toContain('сессия истекла')
  74 |   expect(text).not.toContain('нет связи')
  75 | })
  76 | 
```