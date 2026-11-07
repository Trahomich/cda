// E2E #83 (§7.3): слой мини-карты в карточке поля — NDVI-рендер и
// true-color снимок поверх контура, выбор даты из render_available.
// Мок-мир: 9 сцен 2027-04-10…2027-07-31, рендеры у последних дат
// (облачная 2027-07-03 без рендера, доступные: 2027-07-17 и 2027-07-31).
import { expect, test } from '@playwright/test'

const FIRST_FIELD = '0000000a-0000-4000-8000-000000000001'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/, { timeout: 15_000 })
})

test('карточка поля: оверлей NDVI-сцены появляется и переключается (§7.3)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)

  // Контролы слоя у NDVI-таймлайна (после загрузки блока).
  await expect(page.locator('.card360__layer-switch')).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.card360__layer-btn--ndvi')).toBeVisible()
  await expect(page.locator('.card360__layer-btn--truecolor')).toBeVisible()
  await expect(page.locator('.card360__layer-btn--contour')).toBeVisible()

  // По умолчанию — NDVI на последнюю дату с рендером (2027-07-31).
  await expect(page.locator('.card360__layer-btn--ndvi')).toHaveClass(/card360__layer-btn--active/)
  await expect(page.locator('.card360__layer-date-label')).toContainText('31.07.2027')

  // Оверлей в SVG мини-карты: <image> с blob-URL, геопривязан по bbox.
  const layer = page.locator('svg.card360__contour image.card360__ndvi-layer')
  await expect(layer).toBeVisible({ timeout: 10_000 })
  await expect(layer).toHaveAttribute('href', /^blob:/)
  await expect(page.locator('polygon.card360__polygon')).toBeVisible()
  // При активном слое заливка контура прозрачна — снимок не перекрыт (v2-fix-G).
  await expect(page.locator('polygon.card360__polygon')).toHaveCSS('fill-opacity', '0')

  // Выбор даты кликом по точке графика (v2-fix-H): рендер за 2027-07-17 есть.
  await page.locator('.ndvi__hit[data-date="2027-07-17"]').click()
  await expect(page.locator('.card360__layer-date-label')).toContainText('17.07.2027')
  await expect(layer).toBeVisible()

  // «Контур» скрывает оверлей; клик по точке снова включает NDVI (v2-fix-H).
  await page.locator('.card360__layer-btn--contour').click()
  await expect(page.locator('.card360__layer-btn--contour')).toHaveClass(
    /card360__layer-btn--active/,
  )
  await expect(layer).toHaveCount(0)
  await expect(page.locator('polygon.card360__polygon')).toBeVisible()
  await page.locator('.ndvi__hit[data-date="2027-07-31"]').click()
  await expect(page.locator('.card360__layer-btn--ndvi')).toHaveClass(
    /card360__layer-btn--active/,
  )
  await expect(layer).toBeVisible()
})
