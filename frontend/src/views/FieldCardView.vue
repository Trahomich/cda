<script setup lang="ts">
// E-03 «Поле 360°» (docs/07-ux §3.2): карточка поля — контур, история
// культур, предшественник ✓/нарушение, NDVI-таймлайн (облачные точки ◌),
// агрохимия, погода сейчас+7 сут (осадки, GDD), планы/журнал.
// Данные: API-05/06/07 + API-18 (журнал поля).
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  completeOperation,
  confirmOperation,
  createOperationPlan,
  getFieldCard,
  getFieldCropCycles,
  getFieldAdvice,
  getFieldAdviceHistory,
  getFieldEconomics,
  getFieldNdvi,
  getFieldSeasons,
  getFieldVra,
  getFieldVraMap,
  exportFieldVra,
  getFieldWeather,
  getAgroInputs,
  getInsights,
  getMachines,
  getNdviRender,
  getOperations,
  getTreatmentPlans,
  getYieldForecast,
  refreshFieldAdvice,
} from '@/api'
import type {
  AgroInput,
  CropCycleHistoryItem,
  FieldAdvice,
  FieldCard,
  FieldCycle,
  FieldEconomics,
  FieldOperation,
  FieldWeather,
  InsightSeverity,
  InsightsResponse,
  Machine,
  NdviPoint,
  OperationType,
  SeasonItem,
  TreatmentPlan,
  VraZoneItem,
  VraZonesResponse,
  YieldForecastResponse,
} from '@/api/types'
import type { NdviRender } from '@/api'
import { useAuthStore } from '@/stores/auth'
import CropHistoryStrip from '@/components/CropHistoryStrip.vue'
import NdviTimeline from '@/components/NdviTimeline.vue'
import Skeleton from '@/components/Skeleton.vue'
import VraLightbox from '@/components/VraLightbox.vue'

const route = useRoute()
const fieldId = route.params.fieldId as string
const auth = useAuthStore()
/** «Сегодня» (ISO) — для подписей «нет данных на …» и форм CRUD. */
const todayIso = new Date().toISOString().slice(0, 10)

const card = ref<FieldCard | null>(null)
const cropHistory = ref<CropCycleHistoryItem[]>([])
const ndvi = ref<NdviPoint[]>([])
const weather = ref<FieldWeather | null>(null)
const operations = ref<FieldOperation[]>([])

/** Вкладки карточки (v2): «Обзор» — блоки 360°, «Работы» — план+факт поля. */
const activeTab = ref<'overview' | 'works'>('overview')
/** Вкладка «Работы»: планы API-15 (field_id) + журнал API-18 (field_id). */
const fieldPlans = ref<TreatmentPlan[]>([])
const fieldOperations = ref<FieldOperation[]>([])

/** Лента «Работы» (#674/#174): единый источник — операции API-18 выбранного
 *  сезона (план = статусы draft/planned/in_progress, факт = done);
 *  планы внесений (API-15) — отдельная сущность, сюда не смешиваются. */
const worksSeasonOps = computed(() => {
  const w = journalWindow.value
  const ops = w
    ? fieldOperations.value.filter((o) => o.date >= w.from && o.date <= w.to)
    : fieldOperations.value
  return [...ops].sort((a, b) => b.date.localeCompare(a.date))
})

/* ── Вкладка «Работы» (§7.10, #174): группировка по месяцам, чипы план/факт,
 *  стоимости, техника, подтверждение и минимальный CRUD ── */

const MONTH_NAMES = [
  'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
  'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь',
]

/** Группировка работ сезона по месяцам (свежие сверху). */
const worksGroups = computed(() => {
  const map = new Map<string, FieldOperation[]>()
  for (const op of worksSeasonOps.value) {
    const key = op.date.slice(0, 7)
    const bucket = map.get(key)
    if (bucket) bucket.push(op)
    else map.set(key, [op])
  }
  return [...map.entries()]
    .sort((a, b) => b[0].localeCompare(a[0]))
    .map(([key, ops]) => ({
      key,
      label: `${MONTH_NAMES[Number(key.slice(5, 7)) - 1]} ${key.slice(0, 4)}`,
      ops,
    }))
})

/** Подсказка техники строки: агрегат(+прицеп) · оператор · часы. */
function machinesTitle(op: FieldOperation): string {
  return (op.machines ?? [])
    .map((m) =>
      [
        m.machine_name + (m.trailer_name ? ` + ${m.trailer_name}` : ''),
        m.operator,
        m.hours != null ? `${m.hours} мото-ч` : null,
      ]
        .filter(Boolean)
        .join(' · '),
    )
    .join('; ')
}

/** Стоимость строки: план и факт, обе — при различии (§7.10);
 *  суммы — в тыс ₽ (#180). */
function worksCost(op: FieldOperation): string {
  const plan = op.cost_plan_rub ?? null
  const fact = op.cost_fact_rub ?? null
  if (plan != null && fact != null && plan !== fact) {
    return `план ${fmtThousand(plan)} / факт ${fmtThousand(fact)}`
  }
  const v = fact ?? plan
  return v != null ? fmtThousand(v) : 'стоимость не задана'
}

/** Подтверждать факты может руководитель (и админ). */
const canConfirm = computed(() => ['manager', 'admin'].includes(auth.user?.role ?? ''))

/* ── CRUD (§7.10): добавить план / закрыть фактом / подтвердить ── */

const planFormOpen = ref(false)
const planForm = ref({
  operation_type: 'tillage' as OperationType,
  planned_date: todayIso,
  input_id: '',
  input_dose: null as number | null,
  cost_plan_mode: 'unit' as 'unit' | 'total',
  cost_plan_value: null as number | null,
})
const completingOp = ref<FieldOperation | null>(null)
const completeForm = ref({
  fact_date: todayIso,
  input_amount_total: null as number | null,
  cost_fact_mode: 'unit' as 'unit' | 'total',
  cost_fact_value: null as number | null,
  performer: '',
})
/** Несколько единиц техники (#180): агрегат + прицеп + механизатор + часы. */
type MachineRow = { machine_id: string; trailer_id: string; operator: string; hours: number | null }
const completeMachines = ref<MachineRow[]>([])
function addMachineRow(): void {
  completeMachines.value.push({ machine_id: '', trailer_id: '', operator: '', hours: null })
}
/** Форма закрытия валидна: дата и ЯВНАЯ стоимость (#707 — обязательна). */
const completeValid = computed(
  () =>
    !!completeForm.value.fact_date &&
    completeForm.value.cost_fact_value != null &&
    completeForm.value.cost_fact_value > 0,
)

/** Редактирование плана (#180): PATCH до закрытия фактом. */
const editingOp = ref<FieldOperation | null>(null)
const editForm = ref({
  planned_date: '',
  input_id: '',
  input_dose: null as number | null,
  cost_plan_mode: 'unit' as 'unit' | 'total',
  cost_plan_value: null as number | null,
})
function startEdit(op: FieldOperation): void {
  editingOp.value = op
  editForm.value = {
    planned_date: op.planned_date ?? op.date,
    input_id: op.input_id ?? '',
    input_dose: op.input_dose ?? null,
    cost_plan_mode: 'unit',
    cost_plan_value: op.cost_plan_rub != null ? Math.round(op.cost_plan_rub / (op.actual_area_ga || 1)) : null,
  }
}
async function submitEdit(): Promise<void> {
  const op = editingOp.value
  if (!op || !editForm.value.planned_date) return
  crudBusy.value = true
  try {
    await patchOperation(op.operation_id, {
      planned_date: editForm.value.planned_date,
      input_id: editForm.value.input_id || null,
      input_dose: editForm.value.input_dose,
      cost_plan_mode: editForm.value.cost_plan_mode,
      cost_plan_value: editForm.value.cost_plan_value,
    })
    editingOp.value = null
    await reloadWorks()
  } finally {
    crudBusy.value = false
  }
}
/** Справочники для форм: расходники и техника. */
const inputsRef = ref<AgroInput[]>([])
const machinesRef = ref<Machine[]>([])
const crudBusy = ref(false)

async function loadRefs(): Promise<void> {
  if (inputsRef.value.length && machinesRef.value.length) return
  try {
    const [inp, mach] = await Promise.all([getAgroInputs(), getMachines()])
    inputsRef.value = inp.items
    machinesRef.value = mach.items
  } catch {
    /* формы без справочников — поля просто пустые */
  }
}

async function reloadWorks(): Promise<void> {
  await loadWorks()
  void loadEconomics()
}

async function submitPlan(): Promise<void> {
  if (!planForm.value.planned_date) return
  crudBusy.value = true
  try {
    await createOperationPlan({
      field_id: fieldId,
      operation_type: planForm.value.operation_type,
      planned_date: planForm.value.planned_date,
      input_id: planForm.value.input_id || null,
      input_dose: planForm.value.input_dose,
      cost_plan_mode: planForm.value.cost_plan_mode,
      cost_plan_value: planForm.value.cost_plan_value,
    })
    planFormOpen.value = false
    planForm.value = {
      operation_type: 'tillage',
      planned_date: todayIso,
      input_id: '',
      input_dose: null,
      cost_plan_mode: 'unit',
      cost_plan_value: null,
    }
    await reloadWorks()
  } finally {
    crudBusy.value = false
  }
}

function startComplete(op: FieldOperation): void {
  completingOp.value = op
  completeForm.value = {
    fact_date: todayIso,
    input_amount_total: null,
    cost_fact_mode: 'unit',
    cost_fact_value: null,
    performer: op.performer ?? '',
  }
  completeMachines.value = [{ machine_id: '', trailer_id: '', operator: '', hours: null }]
}

async function submitComplete(): Promise<void> {
  const op = completingOp.value
  if (!op || !completeValid.value) return
  crudBusy.value = true
  try {
    await completeOperation(op.operation_id, {
      fact_date: completeForm.value.fact_date,
      input_amount_total: completeForm.value.input_amount_total,
      cost_fact_mode: completeForm.value.cost_fact_mode,
      cost_fact_value: completeForm.value.cost_fact_value,
      performer: completeForm.value.performer || null,
      machines: completeMachines.value
        .filter((m) => m.machine_id)
        .map((m) => ({
          machine_id: m.machine_id,
          trailer_id: m.trailer_id || null,
          operator: m.operator || null,
          hours: m.hours,
        })),
    })
    completingOp.value = null
    await reloadWorks()
  } finally {
    crudBusy.value = false
  }
}

async function confirmOp(op: FieldOperation): Promise<void> {
  crudBusy.value = true
  try {
    await confirmOperation(op.operation_id)
    await reloadWorks()
  } finally {
    crudBusy.value = false
  }
}

const PLAN_STATUS_LABELS: Record<string, string> = {
  draft: 'черновик',
  approved: 'согласован',
  in_progress: 'выполняется',
  completed: 'выполнен',
  cancelled: 'отменён',
}

const OP_LABELS: Record<string, string> = {
  spraying: 'опрыскивание',
  fertilizing: 'внесение удобрений',
  seeding: 'посев',
  harvesting: 'уборка',
  tillage: 'обработка почвы',
  other: 'прочее',
}

/** Текущий сезон: item истории севооборота (API-49) со статусом in_progress;
 *  cycles из API-08 содержат только планируемые годы (2027+ в моке),
 *  поэтому текущий год ищем не по реальной дате, а по статусу цикла. */
const currentSeason = computed(
  () =>
    cropHistory.value.find((c) => c.status === 'in_progress') ??
    cropHistory.value.find((c) => c.year === new Date().getFullYear()) ??
    null,
)

const currentCycle = computed(() => {
  const season = currentSeason.value
  const bySeason = season ? card.value?.cycles.find((c) => c.year === season.year) : undefined
  // Поля seasons у API-08 и API-49 совпадают (year/culture_name/kind).
  return bySeason ?? (season as FieldCycle | null) ?? card.value?.cycles[0] ?? null
})

/** Цвет контура (п.5, v2-fix-A): color_hex культуры текущего цикла из
 *  истории севооборота (API-49 отдаёт цвет из справочника API-48);
 *  без культуры/цвета — прежний зелёный. */
const contourColor = computed(
  () => currentSeason.value?.color_hex ?? cropHistory.value.find((c) => c.year === currentCycle.value?.year)?.color_hex ?? null,
)

/** Урожайность по году (из API-05 cycles) — в подсказку ленты API-49. */
const cycleYields = computed(() => {
  const out: Record<number, number> = {}
  for (const c of card.value?.cycles ?? []) {
    if (c.yield_c_ha) out[c.year] = c.yield_c_ha
  }
  return out
})

const lastSatelliteDate = computed(() => {
  const clear = chartPoints.value.filter((p) => p.cloud_pct <= 80)
  return clear.length ? clear[clear.length - 1].date : ''
})

/** Рамка нормализации lon/lat → SVG viewBox 200×120 (для контура и слоя §7.3). */
const mapFrame = computed(() => {
  const ring = card.value?.field.contour.coordinates[0]
  if (!ring || ring.length < 3) return null
  const lons = ring.map((p) => p[0])
  const lats = ring.map((p) => p[1])
  const minLon = Math.min(...lons)
  const maxLon = Math.max(...lons)
  const minLat = Math.min(...lats)
  const maxLat = Math.max(...lats)
  const s = Math.min(180 / (maxLon - minLon || 1), 100 / (maxLat - minLat || 1))
  return { minLon, minLat, s }
})

/** Контур в координатах SVG viewBox 200×120 (нормализация lon/lat в рамку). */
const contourPoints = computed(() => {
  const ring = card.value?.field.contour.coordinates[0]
  const frame = mapFrame.value
  if (!ring || !frame) return ''
  const { minLon, minLat, s } = frame
  return ring
    .map(([lon, lat]) => {
      const x = 10 + (lon - minLon) * s
      const y = 110 - (lat - minLat) * s
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
})

/* ── Слой мини-карты (§7.3): NDVI-рендер / truecolor снимок / контур.
 *  #130 (§7.7): индексные слои NDRE/NDMI/NDSI/EVI/BSI — те же PNG-рендеры
 *  API-50 ?variant={index}; SAVI не включаем (дублирует EVI). ── */

/** Индексные слои: краткая метка кнопки + русская подсказка (§7.7). */
const INDEX_LAYERS = [
  { key: 'ndvi', label: 'NDVI', hint: 'вегетация' },
  { key: 'ndre', label: 'NDRE', hint: 'азотный статус' },
  { key: 'ndmi', label: 'NDMI', hint: 'влажность биомассы' },
  { key: 'ndsi', label: 'NDSI', hint: 'снежный покров' },
  { key: 'evi', label: 'EVI', hint: 'вегетация, устойчивая к почве' },
  { key: 'bsi', label: 'BSI', hint: 'голая почва' },
] as const

type IndexLayerKey = (typeof INDEX_LAYERS)[number]['key']
/** Режим слоя: индекс — PNG-оверлей на контуре, truecolor — RGB-снимок
 *  (#583: отдельный режим «Контур» убран; без слоя контур виден заливкой). */
type LayerMode = IndexLayerKey | 'truecolor'

const layerMode = ref<LayerMode>('ndvi')

/** Даты со свежими рендерами (render_available у API-06, §7.3):
 *  NDVI — из основного таймлайна, прочие индексы — из ?index= (§7.7). */
type ExtraIndexKey = Exclude<IndexLayerKey, 'ndvi'>
const EXTRA_INDEX_KEYS: readonly ExtraIndexKey[] = ['ndre', 'ndmi', 'ndsi', 'evi', 'bsi']

const emptyIndexSeries = (): Record<ExtraIndexKey, NdviPoint[]> => ({
  ndre: [],
  ndmi: [],
  ndsi: [],
  evi: [],
  bsi: [],
})

const indexDates = ref<Record<ExtraIndexKey, string[]>>({
  ndre: [],
  ndmi: [],
  ndsi: [],
  evi: [],
  bsi: [],
})
/** Полные ряды сцен индексов сезона (#151): точки для графика временного
 *  ряда выбранного индекса (значения + полоса min–max). */
const indexSeries = ref<Record<ExtraIndexKey, NdviPoint[]>>(emptyIndexSeries())
const indexLoading = ref(true)

/** Доступность индексного слоя: сцены с рендерами сезона (API-06 ?index=).
 *  Ошибки/пусто — слой недоступен (кнопка задизейблена). */
/** Флаг: индексные слои загружены без ?year (год сезона ещё не был известен) —
 *  перезапросить вместе с NDVI, когда loadSeasons определит сезон (#495). */
const indexNeedsSeasonYear = ref(false)

async function loadIndexLayers(): Promise<void> {
  const token = seasonToken
  indexLoading.value = true
  indexNeedsSeasonYear.value = false
  const loaded = await Promise.all(
    EXTRA_INDEX_KEYS.map(async (key) => {
      try {
        // Текущему сезону — явный ?year: без него API-06 отдаёт сцены ВСЕХ
        // лет (бэкфилл 2023+) и годы смешиваются (регрессия #146/#495).
        const year =
          seasonQueryYear.value ?? selectedYear.value ?? currentSeasonYear.value ?? undefined
        indexNeedsSeasonYear.value = year === undefined
        const points = await getFieldNdvi(fieldId, year, key)
        return [key, points] as const
      } catch {
        return [key, []] as const
      }
    }),
  )
  if (token !== seasonToken) return
  indexSeries.value = Object.fromEntries(loaded) as Record<ExtraIndexKey, NdviPoint[]>
  indexDates.value = Object.fromEntries(
    loaded.map(([key, points]) => [key, points.filter((p) => p.render_available).map((p) => p.date).sort()] as const),
  ) as Record<ExtraIndexKey, string[]>
  // Выбранный индекс в новом сезоне недоступен — возвращаем NDVI.
  const mode = layerMode.value
  if (mode !== 'ndvi' && mode !== 'truecolor' && !indexDates.value[mode].length) {
    layerMode.value = 'ndvi'
  }
  indexLoading.value = false
}

/** Кнопка индексного слоя недоступна, пока сцены не загружены или их нет. */
function indexLayerDisabled(key: IndexLayerKey): boolean {
  if (key === 'ndvi') return false
  return indexLoading.value || indexDates.value[key].length === 0
}

const renderDates = computed(() => {
  const mode = layerMode.value
  if (mode !== 'ndvi' && mode !== 'truecolor') {
    return indexDates.value[mode]
  }
  return ndvi.value.filter((p) => p.render_available).map((p) => p.date)
})
/** Выбранная дата слоя; по умолчанию — последняя с рендером. */
const renderDate = ref('')
/** Загруженный PNG (blob URL + геопривязка из X-Field-Bbox/X-Image-Size). */
const layer = ref<NdviRender | null>(null)
/** Русская подсказка активного слоя (#130; #583: truecolor — «RGB-снимок»). */
const activeIndexHint = computed(() => {
  const found = INDEX_LAYERS.find((l) => l.key === layerMode.value)
  return found ? found.hint : 'RGB-снимок'
})

/* ── График временного ряда индекса (#151): единый выбор со слоем мини-карты.
 *  Выбор индексного слоя переключает и график на ряд того же индекса;
 *  «RGB» график не меняет. Автосброс слоя при смене сезона (нет сцен)
 *  график не трогает — у выбранного индекса покажется нейтральное пустое
 *  состояние «нет данных за сезон». ── */
const chartIndex = ref<IndexLayerKey>('ndvi')

/** Клик по кнопке индексного слоя: слой и график — один индекс (#151). */
function onIndexLayerBtn(key: LayerMode): void {
  layerMode.value = key
  if (key !== 'truecolor') chartIndex.value = key
}

const chartMeta = computed(
  () => INDEX_LAYERS.find((l) => l.key === chartIndex.value) ?? INDEX_LAYERS[0]!,
)
const chartPoints = computed(() =>
  chartIndex.value === 'ndvi' ? ndvi.value : indexSeries.value[chartIndex.value],
)
const chartLoading = computed(() =>
  chartIndex.value === 'ndvi' ? ndviLoading.value : indexLoading.value,
)
/** Кликабельные даты графика: даты с рендером ряда выбранного индекса. */
const chartSelectable = computed(() =>
  chartIndex.value === 'ndvi'
    ? ndvi.value.filter((p) => p.render_available).map((p) => p.date)
    : indexDates.value[chartIndex.value],
)

watch(
  renderDates,
  (dates) => {
    if (!dates.length) {
      renderDate.value = ''
      return
    }
    if (!dates.includes(renderDate.value)) renderDate.value = dates[dates.length - 1]
  },
  { immediate: true },
)

let layerToken = 0

async function loadLayer() {
  const token = ++layerToken
  if (!renderDate.value) {
    const prev = layer.value
    layer.value = null
    if (prev) URL.revokeObjectURL(prev.url)
    return
  }
  // Индексные режимы (§7.7) — вариант рендера = имя индекса (API-50).
  const mode = layerMode.value
  const variant: 'truecolor' | IndexLayerKey = mode === 'truecolor' ? 'truecolor' : mode
  const r = await getNdviRender(fieldId, renderDate.value, variant)
  if (token !== layerToken) {
    // Пользователь успел переключить режим/дату — этот ответ устарел.
    if (r) URL.revokeObjectURL(r.url)
    return
  }
  const prev = layer.value
  layer.value = r
  if (prev) URL.revokeObjectURL(prev.url)
  if (!r) {
    // Рендера нет (404) или заголовки битые — оверлей чистим, режим
    // не меняем (#583: режима «Контур» больше нет).
    layer.value = null
  }
}

watch([renderDate, layerMode], () => void loadLayer())

/** Клик по точке графика (v2-fix-H; #151): даты без рендера не выбираем;
 *  при RGB-снимке включаем слой индекса графика. */
function onDotSelect(date: string): void {
  if (!chartSelectable.value.includes(date)) return
  renderDate.value = date
  if (layerMode.value === 'truecolor') {
    layerMode.value = chartIndex.value
  }
}

onUnmounted(() => {
  if (layer.value) URL.revokeObjectURL(layer.value.url)
})

/* ── Дифференцированное внесение (API-52, §7.4): зоны + карта ── */

const vra = ref<VraZonesResponse | null>(null)
const vraLoading = ref(true)
/** Лайтбокс карты дифвнесения (#141): открыт по клику на превью. */
const vraLightbox = ref(false)
/** PNG-карта зон (raw + геопривязка X-Field-Bbox/X-Image-Size). */
const vraMap = ref<NdviRender | null>(null)

function resetVra(): void {
  vra.value = null
  if (vraMap.value) {
    URL.revokeObjectURL(vraMap.value.url)
    vraMap.value = null
  }
}

/* ── Выгрузка карты дифвнесения (API-60, §7.4, #140) ── */

const EXPORT_FORMATS = [
  { value: 'isoxml', label: 'ISOXML (терминалы)' },
  { value: 'shp', label: 'Shapefile (ГИС)' },
  { value: 'geojson', label: 'GeoJSON' },
] as const

const exportFormat = ref<(typeof EXPORT_FORMATS)[number]['value']>('isoxml')
/** Базовая доза из формы (когда нет approved-плана, API-60 → 422). */
const exportDose = ref('')
const exportBusy = ref(false)
/** Нужна базовая доза: 422 vra_no_base_dose или base_dose_kg_ha=null. */
const exportNeedsDose = ref(false)
const exportError = ref(false)

/** Скачать blob как файл (a[download] + object URL, revoke сразу). */
function downloadExportFile(file: { blob: Blob; filename: string }): void {
  const url = URL.createObjectURL(file.blob)
  const a = document.createElement('a')
  a.href = url
  a.download = file.filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/** Выгрузить карту зон в выбранном формате; без базовой дозы — 422 → форма. */
async function exportVra(): Promise<void> {
  if (!vra.value || exportBusy.value) return
  exportError.value = false
  // v-model на input[type=number] отдаёт число; строка может прийти с запятой
  const rawDose: string | number = exportDose.value
  const manualDose = Number(typeof rawDose === 'string' ? rawDose.replace(',', '.') : rawDose)
  const dose =
    Number.isFinite(manualDose) && manualDose > 0 && manualDose <= 2000
      ? manualDose
      : (vra.value.base_dose_kg_ha ?? undefined)
  exportBusy.value = true
  try {
    const res = await exportFieldVra(fieldId, exportFormat.value, {
      baseDoseKgHa: dose,
      year: seasonQueryYear.value,
    })
    if (res.kind === 'file') {
      exportNeedsDose.value = false
      downloadExportFile(res.file)
    } else if (res.kind === 'no-dose') {
      exportNeedsDose.value = true // форма ввода базовой дозы
    } else {
      exportError.value = true
    }
  } catch {
    exportError.value = true
  } finally {
    exportBusy.value = false
  }
}

onUnmounted(() => {
  if (layer.value) URL.revokeObjectURL(layer.value.url)
  if (vraMap.value) URL.revokeObjectURL(vraMap.value.url)
})

/* ── Сезоны NDVI (API-54, §7.5): переключатель в паспорте поля ── */

/** Сезоны поля, годы по убыванию (текущий помечен is_current). */
const seasons = ref<SeasonItem[]>([])
/** Выбранный сезон; null — список ещё не загружен (данные = текущий). */
const selectedYear = ref<number | null>(null)
/** Токен защиты от гонок: клики по сезонам подряд, устаревшие ответы
 *  (медленный прошлый сезон после быстрого текущего) не применяются. */
let seasonToken = 0

/** Год текущего сезона (is_current из API-54). */
const currentSeasonYear = computed(
  () => seasons.value.find((s) => s.is_current)?.year ?? null,
)

/** Выбран прошлый сезон — визуально отличим бейджем «сезон YYYY». */
const isPastSeason = computed(
  () =>
    selectedYear.value !== null &&
    currentSeasonYear.value !== null &&
    selectedYear.value !== currentSeasonYear.value,
)

/** Выбранный год для запросов: null — без ?year (текущий сезон, §7.5). */
const seasonQueryYear = computed(() => {
  if (selectedYear.value === null) return undefined
  // текущий сезон = отсутствие параметра — не гоняем лишний запрос иначе
  return selectedYear.value === currentSeasonYear.value
    ? undefined
    : selectedYear.value
})

/** Годы с сезонными данными (API-54) — кликабельные ячейки ленты
 *  севооборота под снимками поля переключают сезон карточки (#122). */
const seasonYearsList = computed(() => seasons.value.map((s) => s.year))

/** Клик по году ленты севооборота: год с сезоном — переключить сезон. */
function onSeasonCell(year: number): void {
  if (seasonYearsList.value.includes(year)) selectSeason(year)
}

async function loadSeasons() {
  try {
    seasons.value = await getFieldSeasons(fieldId)
    const current = seasons.value.find((s) => s.is_current) ?? seasons.value[0]
    selectedYear.value = current ? current.year : null
  } catch {
    seasons.value = [] // API-54 недоступен — карточка остаётся в режиме текущего сезона
  }
  // NDVI успел загрузиться до Seasons (гонка параллельных блоков) и без ?year
  // — перезагружаем с годом текущего сезона (#495); индексные слои — тоже.
  if (ndviNeedsSeasonYear.value && currentSeasonYear.value != null) {
    ndviNeedsSeasonYear.value = false
    void loadNdvi()
  }
  if (indexNeedsSeasonYear.value && currentSeasonYear.value != null) {
    indexNeedsSeasonYear.value = false
    void loadIndexLayers()
  }
  // Журнал успел загрузиться без года — перезагружаем с окном сезона (#165).
  if (journalNeedsSeasonYear.value && currentSeasonYear.value != null) {
    journalNeedsSeasonYear.value = false
    void loadJournal()
  }
}

/** Выбор сезона (§7.5): сезонозависимые блоки (NDVI, VRA, урожайность,
 *  выводы) перезагружаются с ?year=YYYY. */
function selectSeason(year: number): void {
  if (year === selectedYear.value) return
  selectedYear.value = year
  seasonToken++
  void loadNdvi()
  void loadVra()
  void loadYield()
  void loadEconomics()
  void loadInsights()
  void loadIndexLayers()
  void loadJournal()
}

async function loadVra() {
  const token = seasonToken
  vraLoading.value = true
  resetVra()
  try {
    const res = await getFieldVra(fieldId, seasonQueryYear.value)
    if (token !== seasonToken) return
    if (res.kind === 'ok') {
      vra.value = res.data
      vraMap.value = await getFieldVraMap(fieldId, seasonQueryYear.value)
      if (token !== seasonToken) {
        if (vraMap.value) URL.revokeObjectURL(vraMap.value.url)
        resetVra()
        return
      }
    }
    // 'empty' (нет чистых сцен сезона) — в шаблоне «Нет данных…»
  } finally {
    if (token === seasonToken) vraLoading.value = false
  }
}

/** Позиция карты зон в SVG-превью: bbox → координаты рамки мини-карты. */
const vraMapRect = computed(() => {
  const frame = mapFrame.value
  const r = vraMap.value
  if (!frame || !r) return null
  const [minLon, minLat, maxLon, maxLat] = r.bbox
  return {
    x: 10 + (minLon - frame.minLon) * frame.s,
    y: 110 - (maxLat - frame.minLat) * frame.s,
    width: (maxLon - minLon) * frame.s,
    height: (maxLat - minLat) * frame.s,
  }
})

/** Доза зоны: «N» (кг/га — в шапке таблицы) при рассчитанной; иначе
 *  отклонение от базовой. */
function doseLabel(zone: VraZoneItem): string {
  if (zone.dose_kg_ha !== null) return fmtNum(zone.dose_kg_ha)
  const pct = Math.round(Math.abs(zone.dose_factor - 1) * 100)
  if (!pct) return 'базовая'
  return `${zone.dose_factor > 1 ? '+' : '−'}${pct} %`
}

/* ── «Что делать сейчас»: AI-рекомендация (API-55/56, §9.3, #137) ── */

const advice = ref<FieldAdvice | null>(null)
const adviceLoading = ref(true)
/** POST прошёл (202 queued) — опрашиваем результат. */
const adviceBusy = ref(false)
const adviceError = ref(false)
const adviceHistory = ref<FieldAdvice[]>([])
/** Есть ли ещё страницы: API-56 не отдаёт total — полная страница = 20 шт. */
const adviceHasMore = ref(false)
const adviceHistoryOpen = ref(false)
const ADVICE_PAGE = 20
/** Поллинг после 202 queued: каждые 10 с, максимум 12 попыток (~2 мин). */
const ADVICE_POLL_MS = 10_000
const ADVICE_POLL_MAX = 12
let advicePollTimer: ReturnType<typeof setTimeout> | null = null
let advicePollLeft = 0
/** Метка, по которой отличаем «свою» серию опросов от перезагрузки. */
let advicePollToken = 0

async function loadAdvice(): Promise<void> {
  const got = await getFieldAdvice(fieldId)
  if (got && adviceBusy.value && got.created_at !== advice.value?.created_at) {
    // Пересчёт завершился — останавливаем поллинг.
    stopAdvicePoll()
  }
  advice.value = got
  adviceLoading.value = false
}

function stopAdvicePoll(): void {
  adviceBusy.value = false
  if (advicePollTimer != null) {
    clearTimeout(advicePollTimer)
    advicePollTimer = null
  }
}

function scheduleAdvicePoll(token: number): void {
  if (token !== advicePollToken || advicePollLeft <= 0) {
    stopAdvicePoll()
    return
  }
  advicePollLeft--
  advicePollTimer = setTimeout(async () => {
    await loadAdvice()
    if (adviceBusy.value) scheduleAdvicePoll(token)
  }, ADVICE_POLL_MS)
}

/** POST → 202 queued → индикатор + поллинг GET до новой записи (~2 мин). */
async function recalcAdvice(): Promise<void> {
  adviceError.value = false
  try {
    await refreshFieldAdvice(fieldId)
    adviceBusy.value = true
    advicePollLeft = ADVICE_POLL_MAX
    advicePollToken++
    scheduleAdvicePoll(advicePollToken)
  } catch {
    adviceError.value = true
  }
}

/** #148: единая кнопка «Пересчитать» блока ассистента: пересчитывает
 *  рекомендацию (POST API-55 202 queued + поллинг) и обновляет
 *  выводы (GET API-51; отдельного POST-пересчёта выводов API не имеет —
 *  детекторы считаются событийно, GET отдаёт актуальное/кэш). */
async function recalcAssistant(): Promise<void> {
  await recalcAdvice()
  void loadInsights()
}

async function loadAdviceHistory(): Promise<void> {
  try {
    const page = await getFieldAdviceHistory(fieldId, {
      limit: ADVICE_PAGE,
      offset: adviceHistory.value.length,
    })
    adviceHistory.value = [...adviceHistory.value, ...page.items]
    adviceHasMore.value = page.items.length === ADVICE_PAGE
  } catch {
    adviceHasMore.value = false
  }
}

watch(adviceHistoryOpen, (open) => {
  if (open && !adviceHistory.value.length) void loadAdviceHistory()
})

onUnmounted(() => stopAdvicePoll())

/* ── Прогноз урожайности (API-53, §7.4) ── */

const yieldForecast = ref<YieldForecastResponse | 'insufficient' | null>(null)
const yieldLoading = ref(true)

async function loadYield() {
  const token = seasonToken
  yieldLoading.value = true
  yieldForecast.value = null
  try {
    const data = await getYieldForecast(fieldId, seasonQueryYear.value)
    if (token !== seasonToken) return
    yieldForecast.value = data
  } catch {
    if (token === seasonToken) yieldForecast.value = null
  } finally {
    if (token === seasonToken) yieldLoading.value = false
  }
}

/* ── Экономика сезона (API-62, §7.9; #163): план/факт стоимости техработ,
 *  руб/га, руб/т (null без фактической урожайности) + детализация ── */

const econ = ref<FieldEconomics | null>(null)
const econLoading = ref(true)
/** Раскрытая детализация по операциям (по умолчанию свернута). */
const econOpen = ref(false)

async function loadEconomics() {
  const token = seasonToken
  econLoading.value = true
  econ.value = null
  try {
    const data = await getFieldEconomics(fieldId, seasonQueryYear.value)
    if (token !== seasonToken) return
    econ.value = data
  } catch {
    if (token === seasonToken) econ.value = null
  } finally {
    if (token === seasonToken) econLoading.value = false
  }
}

/** Отклонение факт−план: < 0 — экономия (зелёный), > 0 — перерасход. */
const econDiff = computed(() =>
  econ.value ? econ.value.fact_cost_rub - econ.value.plan_cost_rub : 0,
)

/** Есть плановые операции, но ни один факт не выполнен (#635/#165):
 *  к плитке факта — нейтральная подпись «работы не начаты». */
const econNoFacts = computed(
  () =>
    econ.value != null &&
    econ.value.fact_cost_rub === 0 &&
    econ.value.operations.some((o) => o.plan != null),
)

/* ── Вкладка «Работы» (#665/#171/#674): цена каждой строки (API-18 cost_rub)
 *  и итоги сезона = «Экономика сезона» (API-62 поля) ── */

function fmtRub(v: number): string {
  return `${Math.round(v).toLocaleString('ru-RU')} ₽`
}

/** Сводные суммы — в ТЫСЯЧАХ рублей (#707/#180): «1 234 тыс ₽»;
 *  значения до 10 тыс — с одним знаком («52,4 тыс ₽»). */
function fmtThousand(v: number): string {
  return `${(v / 1000).toLocaleString('ru-RU', {
    maximumFractionDigits: v < 10_000 ? 1 : 0,
  })} тыс ₽`
}

/* ── «Выводы ассистента» (API-51, §7.4): детекторы + LLM-сводка ── */

const insights = ref<InsightsResponse | null>(null)
const insightsLoading = ref(true)

async function loadInsights() {
  const token = seasonToken
  insightsLoading.value = true
  insights.value = null
  // null (404 поля/прочее) — блок покажет «недоступны»; кэша на клиенте нет.
  const data = await getInsights(fieldId, seasonQueryYear.value)
  if (token !== seasonToken) return
  insights.value = data
  insightsLoading.value = false
}

/** Значок важности детектора (§7.4): info ℹ / warn ⚠ / alert ⛔. */
const SEVERITY_MARKS: Record<InsightSeverity, string> = {
  info: 'ℹ',
  warn: '⚠',
  alert: '⛔',
}

/** Позиция PNG в SVG: bbox рендера (X-Field-Bbox) → координаты рамки мини-карты. */
const layerRect = computed(() => {
  const frame = mapFrame.value
  const r = layer.value
  if (!frame || !r) return null
  const [minLon, minLat, maxLon, maxLat] = r.bbox
  return {
    x: 10 + (minLon - frame.minLon) * frame.s,
    y: 110 - (maxLat - frame.minLat) * frame.s,
    width: (maxLon - minLon) * frame.s,
    height: (maxLat - minLat) * frame.s,
  }
})

/* ── Зум и панорамирование мини-карты (v2-fix-I): динамический viewBox
 *  рамки 200×120; зум 1×–8× (w от 200 до 25), рамку не покидаем. ── */
const contourSvg = ref<SVGSVGElement | null>(null)
const VB_FULL = { w: 200, h: 120 }
const ZOOM_MAX = 8
const vb = ref({ ...VB_FULL, x: 0, y: 0 })
const vbStr = computed(() => `${vb.value.x} ${vb.value.y} ${vb.value.w} ${vb.value.h}`)

function resetView(): void {
  vb.value = { ...VB_FULL, x: 0, y: 0 }
}

// Смена поля — исходный масштаб.
watch(
  () => card.value?.field.field_id,
  () => resetView(),
)

function clampView(): void {
  const v = vb.value
  v.w = Math.min(v.w, VB_FULL.w)
  v.h = v.w * (VB_FULL.h / VB_FULL.w)
  v.x = Math.min(Math.max(v.x, 0), VB_FULL.w - v.w)
  v.y = Math.min(Math.max(v.y, 0), VB_FULL.h - v.h)
}

/** Зум с сохранением точки (px, py) в координатах viewBox; factor > 1 — ближе. */
function zoomAt(px: number, py: number, factor: number): void {
  const v = { ...vb.value }
  const w = Math.min(Math.max(v.w / factor, VB_FULL.w / ZOOM_MAX), VB_FULL.w)
  const k = w / v.w
  vb.value = { x: px - (px - v.x) * k, y: py - (py - v.y) * k, w, h: w * (VB_FULL.h / VB_FULL.w) }
  clampView()
}

/** Координаты курсорного события в системе viewBox. */
function eventPoint(e: MouseEvent): { x: number; y: number } {
  const rect = contourSvg.value?.getBoundingClientRect()
  if (!rect) return { x: vb.value.x + vb.value.w / 2, y: vb.value.y + vb.value.h / 2 }
  return {
    x: vb.value.x + ((e.clientX - rect.left) / rect.width) * vb.value.w,
    y: vb.value.y + ((e.clientY - rect.top) / rect.height) * vb.value.h,
  }
}

/** Кнопки зума — к центру видимой рамки. */
function zoomBy(factor: number): void {
  zoomAt(vb.value.x + vb.value.w / 2, vb.value.y + vb.value.h / 2, factor)
}

function onWheel(e: WheelEvent): void {
  e.preventDefault()
  const p = eventPoint(e)
  zoomAt(p.x, p.y, e.deltaY < 0 ? 1.25 : 1 / 1.25)
}

/* Панорамирование перетаскиванием (Pointer Events + capture). */
const dragging = ref(false)
let dragStart: { sx: number; sy: number; vx: number; vy: number } | null = null

function onPanStart(e: PointerEvent): void {
  if (e.pointerType === 'mouse' && e.button !== 0) return
  dragging.value = true
  dragStart = { sx: e.clientX, sy: e.clientY, vx: vb.value.x, vy: vb.value.y }
  ;(e.currentTarget as Element).setPointerCapture(e.pointerId)
}

function onPanMove(e: PointerEvent): void {
  const rect = contourSvg.value?.getBoundingClientRect()
  if (!dragging.value || !dragStart || !rect) return
  vb.value.x = dragStart.vx - ((e.clientX - dragStart.sx) / rect.width) * vb.value.w
  vb.value.y = dragStart.vy - ((e.clientY - dragStart.sy) / rect.height) * vb.value.h
  clampView()
}

function onPanEnd(): void {
  dragging.value = false
  dragStart = null
}

function fmtDate(iso: string): string {
  return iso.split('-').reverse().join('.')
}

/** Дата+время для created_at рекомендаций (§9.3): «08.11.2026 14:02». */
function fmtDateTime(iso: string): string {
  const d = new Date(iso)
  const dd = String(d.getDate()).padStart(2, '0')
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${dd}.${mm}.${d.getFullYear()} ${hh}:${mi}`
}

/** Загрузка по блокам (v2-fix-E): каждый блок карточки рендерится и
 *  заполняется независимо — структура страницы видна сразу, вместо общего
 *  гейта «Загрузка карточки поля…» скелетоны внутри блоков. Ошибка ядра
 *  (API-05-композит) — сообщение, ошибки блока — внутри блока. */
const coreLoading = ref(true)
const coreFailed = ref(false)
const historyLoading = ref(true)
const ndviLoading = ref(true)
const weatherLoading = ref(true)
const weatherFailed = ref(false)
const journalLoading = ref(true)
const worksLoading = ref(true)

async function loadCore() {
  try {
    card.value = await getFieldCard(fieldId)
  } catch {
    coreFailed.value = true // тост с problem+json уже показал API-клиент
  } finally {
    coreLoading.value = false
  }
}

async function loadHistory() {
  try {
    cropHistory.value = await getFieldCropCycles(fieldId)
  } catch {
    cropHistory.value = []
  } finally {
    historyLoading.value = false
  }
}

/** Флаг: NDVI загружен без ?year (год сезона ещё не был известен) —
 *  перезапросить, когда loadSeasons определит текущий сезон (#495). */
const ndviNeedsSeasonYear = ref(false)

async function loadNdvi() {
  const token = seasonToken
  ndviLoading.value = true
  ndvi.value = []
  try {
    // Текущему сезону — явный ?year: без него API-06 отдаёт последние сцены
    // ВСЕХ лет (бэкфилл 2023+), и график «текущего сезона» мешает годы (#495).
    const year =
      seasonQueryYear.value ?? selectedYear.value ?? currentSeasonYear.value ?? undefined
    ndviNeedsSeasonYear.value = year === undefined
    const points = await getFieldNdvi(fieldId, year)
    if (token !== seasonToken) return // сезон уже переключили — ответ устарел
    // API-06 отдаёт desc (свежая первой); фронту нужен asc: график слева-направо
    // по времени и «последний снимок»/дефолт слоя мини-карты = свежая сцена (#142).
    ndvi.value = [...points].sort((a, b) => a.date.localeCompare(b.date))
  } catch {
    if (token === seasonToken) ndvi.value = []
  } finally {
    if (token === seasonToken) ndviLoading.value = false
  }
}

async function loadWeather() {
  try {
    weather.value = await getFieldWeather(fieldId)
  } catch {
    weatherFailed.value = true
  } finally {
    weatherLoading.value = false
  }
}

/** Окно сезона для журнала карточки (#635/#165): операции выбранного сезона
 *  без смешения лет; яровые — календарный год, озимые (культура «озимая») —
 *  с августа Y−1 по июль Y (посев осенью, уборка летом Y). */
const journalWindow = computed(() => {
  const year = selectedYear.value ?? currentSeasonYear.value
  if (year == null) return null
  const culture = cropHistory.value.find((c) => c.year === year)?.culture_name ?? ''
  const winter = /озим/i.test(culture)
  return winter
    ? { from: `${year - 1}-08-01`, to: `${year}-07-31` }
    : { from: `${year}-01-01`, to: `${year}-12-31` }
})

/** Флаг: журнал загружен без окна сезона (год сезона ещё не был известен) —
 *  перезапросить вместе с NDVI, когда loadSeasons определит сезон (#165). */
const journalNeedsSeasonYear = ref(false)

async function loadJournal() {
  journalLoading.value = true
  try {
    // #165: только операции выбранного сезона (окно с озимыми), не последние
    // работы всех лет — иначе журнал противоречил экономике сезона на экране.
    const w = journalWindow.value
    journalNeedsSeasonYear.value = w == null
    operations.value = (
      await getOperations({
        field_id: fieldId,
        limit: 5,
        ...(w ? { date_from: w.from, date_to: w.to } : {}),
      })
    )
      // #674: блок «Журнал (факты)» — только выполненные (план — во вкладке
      // «Работы», единый источник API-18).
      .items.filter((o) => o.status === 'done')
  } catch {
    operations.value = []
  } finally {
    journalLoading.value = false
  }
}

async function loadWorks() {
  try {
    // Вкладка «Работы» — планы API-15 (field_id) + полный журнал поля API-18.
    const [wplans, wops] = await Promise.all([
      getTreatmentPlans({ field_id: fieldId }),
      getOperations({ field_id: fieldId, limit: 100 }),
    ])
    fieldPlans.value = wplans.items
    fieldOperations.value = wops.items
  } catch {
    fieldPlans.value = []
    fieldOperations.value = []
  } finally {
    worksLoading.value = false
  }
}

function load() {
  void loadCore()
  void loadHistory()
  void loadSeasons()
  void loadNdvi()
  void loadWeather()
  void loadJournal()
  void loadWorks()
  void loadRefs()
  void loadVra()
  void loadYield()
  void loadEconomics()
  void loadInsights()
  void loadAdvice()
  void loadIndexLayers()
}

onMounted(load)

function fmtNum(v: number, digits = 1): string {
  return v.toLocaleString('ru-RU', { maximumFractionDigits: digits })
}
</script>

<template>
  <section class="card360">
    <header class="card360__header">
      <RouterLink
        class="card360__back"
        :to="{ name: 'fields-map' }"
      >
        ‹ Поля
      </RouterLink>
      <h1 class="card360__title">
        <template v-if="card">
          {{ card.field.name }}
          <span class="card360__area">· {{ fmtNum(card.field.area_ga, 2) }} га</span>
          <span
            v-if="isPastSeason"
            class="card360__season-badge"
          >сезон {{ selectedYear }}</span>
        </template>
        <Skeleton
          v-else
          width="11rem"
          height="1.3rem"
          pill
        />
      </h1>
      <button
        type="button"
        class="card360__edit"
        disabled
        title="Правка поля (API-03) — вне экранов MVP-15"
      >
        Изменить ✎
      </button>
    </header>

    <!-- Переключение сезонов (API-54, §7.5) — кликами по годам ленты
         «Севооборот по годам» под снимками поля (фидбек user, #122);
         отдельная строка-переключатель убрана. Все блоки карточки следуют
         выбранному сезону. -->
    <p
      v-if="coreFailed"
      class="card360__state card360__state--error"
    >
      Не удалось загрузить карточку поля
    </p>

    <template v-else>
      <div
        class="card360__tabs"
        role="tablist"
        aria-label="Разделы карточки поля"
      >
        <button
          type="button"
          class="card360__tab"
          :class="{ 'card360__tab--active': activeTab === 'overview' }"
          role="tab"
          :aria-selected="activeTab === 'overview'"
          @click="activeTab = 'overview'"
        >
          Обзор
        </button>
        <button
          type="button"
          class="card360__tab card360__tab--works"
          :class="{ 'card360__tab--active': activeTab === 'works' }"
          role="tab"
          :aria-selected="activeTab === 'works'"
          @click="activeTab = 'works'"
        >
          Работы
        </button>
      </div>

      <!-- Обзор: блоки «Поле 360°» -->
      <div
        v-show="activeTab === 'overview'"
        class="card360__grid"
      >
        <!-- Контур + история культур -->
        <div class="card360__block card360__block--map">
          <h2 class="card360__block-title">
            Контур поля
          </h2>
          <Skeleton
            v-if="!card"
            :lines="3"
          />
          <div
            v-else
            class="card360__map-wrap"
          >
            <!-- Зум мини-карты (v2-fix-I): кнопки, колесо, перетаскивание -->
            <div
              class="card360__zoom"
              role="group"
              aria-label="Масштаб мини-карты"
            >
              <button
                type="button"
                class="card360__zoom-btn"
                title="Приблизить"
                aria-label="Приблизить"
                @click="zoomBy(1.6)"
              >
                +
              </button>
              <button
                type="button"
                class="card360__zoom-btn"
                title="Отдалить"
                aria-label="Отдалить"
                @click="zoomBy(1 / 1.6)"
              >
                −
              </button>
              <button
                type="button"
                class="card360__zoom-btn"
                title="Сбросить масштаб"
                aria-label="Сбросить масштаб"
                @click="resetView"
              >
                ⤢
              </button>
            </div>
            <svg
              ref="contourSvg"
              class="card360__contour"
              :class="{
                'card360__contour--layered': !!layerRect,
                'card360__contour--dragging': dragging,
              }"
              :viewBox="vbStr"
              role="img"
              aria-label="Контур поля (колесо мыши — масштаб, перетаскивание — сдвиг)"
              @wheel="onWheel"
              @pointerdown="onPanStart"
              @pointermove="onPanMove"
              @pointerup="onPanEnd"
              @pointercancel="onPanEnd"
            >
              <image
                v-if="layer && layerRect"
                class="card360__ndvi-layer"
                :href="layer.url"
                :x="layerRect.x"
                :y="layerRect.y"
                :width="layerRect.width"
                :height="layerRect.height"
                preserveAspectRatio="none"
              />
              <polygon
                :points="contourPoints"
                class="card360__polygon"
                :style="contourColor ? { fill: contourColor } : undefined"
              />
            </svg>
          </div>
          <h2 class="card360__block-title">
            Севооборот по годам
          </h2>
          <Skeleton
            v-if="historyLoading"
            height="2.2rem"
            pill
          />
          <CropHistoryStrip
            v-else
            :items="cropHistory"
            :yields="cycleYields"
            :season-years="seasonYearsList"
            :selected-year="selectedYear"
            @select="onSeasonCell"
          />
          <p class="card360__muted">
            Цвет полосы — культура справочника (API-48); пунктир — план, обводка —
            текущий год.
            <template v-if="seasons.length > 1">
              Клик по году с данными сезона — показать карточку за тот сезон.
            </template>
          </p>
          <p
            v-if="card && currentCycle && card.rotation_check"
            class="card360__check"
            :class="{ 'card360__check--warn': !card.rotation_check.ok }"
          >
            Культура {{ currentCycle.year }}: {{ currentCycle.culture_name }} —
            <template v-if="card.rotation_check.ok">
              ✓ {{ card.rotation_check.message }}
            </template>
            <template v-else>
              ⚠ {{ card.rotation_check.message }}
            </template>
          </p>
        </div>

        <!-- NDVI -->
        <div class="card360__block">
          <h2 class="card360__block-title">
            {{ chartMeta.label }}-таймлайн (сезон {{ selectedYear ?? currentCycle?.year ?? '—' }})
          </h2>
          <!-- Пустое состояние («снимков нет») рендерит сам компонент -->
          <Skeleton
            v-if="chartLoading"
            :lines="3"
          />
          <NdviTimeline
            v-else
            :points="chartPoints"
            :index="chartIndex"
            :label="chartMeta.label"
            :hint="chartMeta.hint"
            :as-of="todayIso"
            :selectable="chartSelectable"
            :selected="renderDate"
            @select="onDotSelect"
          />
          <!-- §7.3: слой мини-карты — вариант рендера; дата выбирается
               кликом по точке графика (v2-fix-H) -->
          <div
            v-if="!ndviLoading && renderDates.length"
            class="card360__layers"
          >
            <div
              class="card360__layer-switch"
              role="group"
              aria-label="Слой мини-карты"
            >
              <button
                v-for="item in INDEX_LAYERS"
                :key="item.key"
                type="button"
                class="card360__layer-btn"
                :class="[
                  `card360__layer-btn--${item.key}`,
                  { 'card360__layer-btn--active': layerMode === item.key },
                ]"
                :aria-pressed="layerMode === item.key"
                :disabled="indexLayerDisabled(item.key)"
                :title="`${item.label} — ${item.hint}${
                  indexLayerDisabled(item.key) ? ' (нет сцен за сезон)' : ''
                }`"
                @click="onIndexLayerBtn(item.key)"
              >
                {{ item.label }}
              </button>
              <button
                type="button"
                class="card360__layer-btn card360__layer-btn--truecolor"
                :class="{ 'card360__layer-btn--active': layerMode === 'truecolor' }"
                :aria-pressed="layerMode === 'truecolor'"
                title="RGB-снимок (truecolor)"
                @click="layerMode = 'truecolor'"
              >
                RGB
              </button>
            </div>
            <span class="card360__layer-date-label">
              {{ activeIndexHint }} · снимок за {{ renderDate ? fmtDate(renderDate) : '—' }}
            </span>
          </div>
          <!-- #544: сцены на «сегодня» нет — явно подписываем, какой датой показано -->
          <p
            v-if="renderDate && renderDate !== todayIso"
            class="card360__muted card360__layer-asof"
          >
            нет данных на {{ fmtDate(todayIso) }}, показано за {{ fmtDate(renderDate) }}
          </p>
          <p
            v-if="lastSatelliteDate"
            class="card360__muted"
          >
            Дата последнего снимка: {{ lastSatelliteDate }}
          </p>
        </div>

        <div class="card360__block">
          <h2 class="card360__block-title">
            Погода (сейчас + 7 сут)
          </h2>
          <Skeleton
            v-if="weatherLoading"
            :lines="4"
          />
          <p
            v-else-if="weatherFailed"
            class="card360__muted"
          >
            Не удалось загрузить погоду
          </p>
          <p
            v-else-if="weather"
            class="card360__weather"
          >
            Сейчас: {{ fmtNum(weather.now.temp_c) }} °C,
            ветер {{ fmtNum(weather.now.wind_ms) }} м/с,
            влажность {{ weather.now.humidity_pct }} %<br>
            Осадки за 7 сут: {{ fmtNum(weather.summary.precip_7d_mm) }} мм ·
            GDD: {{ weather.summary.gdd }} °C·сут
          </p>
          <table
            v-if="weather"
            class="card360__forecast"
          >
            <thead>
              <tr>
                <th scope="col">
                  Дата
                </th>
                <th scope="col">
                  °C
                </th>
                <th scope="col">
                  Осадки, мм
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="day in weather.forecast"
                :key="day.date"
              >
                <td>{{ day.date.slice(5) }}</td>
                <td>{{ day.temp_min_c }}…{{ day.temp_max_c }}</td>
                <td>{{ fmtNum(day.precip_mm) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Планы и журнал -->
        <div class="card360__block card360__block--plans">
          <h2 class="card360__block-title">
            Планы и журнал {{ selectedYear ?? currentCycle?.year ?? '' }}
          </h2>
          <Skeleton
            v-if="!card || journalLoading"
            :lines="4"
          />
          <template v-else>
            <ul class="card360__plans">
              <li
                v-for="plan in card.treatment_plans"
                :key="plan.plan_id"
              >
                • {{ plan.input_name }} — {{ fmtNum(plan.dose, 2) }}
                {{ plan.unit === 'l_ha' ? 'л/га' : plan.unit === 'kg_ha' ? 'кг/га' : 'кг д.в./га' }},
                {{ plan.planned_date }} — {{ PLAN_STATUS_LABELS[plan.status] ?? plan.status }}
              </li>
              <li
                v-if="card.treatment_plans.length === 0"
                class="card360__muted"
              >
                Планов внесения нет
              </li>
            </ul>
            <h3 class="card360__block-title">
              Журнал (факты)
            </h3>
            <ul class="card360__plans">
              <li
                v-for="op in operations.slice(0, 3)"
                :key="op.operation_id"
              >
                ✓ {{ fmtDate(op.date) }} · {{ OP_LABELS[op.operation_type] ?? op.operation_type }} ·
                {{ fmtNum(op.actual_area_ga, 2) }} га
                <span v-if="op.performer">· {{ op.performer }}</span>
              </li>
              <li
                v-if="operations.length === 0"
                class="card360__muted"
              >
                Операций в этом сезоне пока нет
              </li>
            </ul>
            <button
              type="button"
              class="card360__allworks"
              @click="activeTab = 'works'"
            >
              все работы (план и факт) →
            </button>
          </template>
        </div>
        <div class="card360__block">
          <h2 class="card360__block-title">
            Агрохимия
          </h2>
          <Skeleton
            v-if="!card"
            :lines="2"
          />
          <p
            v-else-if="card.agrochem"
            class="card360__agrochem"
          >
            Проба от {{ card.agrochem.sample_date }}:<br>
            pH {{ fmtNum(card.agrochem.ph) }} · гумус {{ fmtNum(card.agrochem.humus_pct) }} %<br>
            P₂O₅ {{ card.agrochem.p2o5_mg100 }} · K₂O {{ card.agrochem.k2o_mg100 }} мг/100 г<br>
            N {{ fmtNum(card.agrochem.n_mg100) }} мг/100 г
          </p>
          <p
            v-else
            class="card360__muted"
          >
            Данных агрохимии нет (импорт — API-30)
          </p>
        </div>

        <!-- Погода -->
        <!-- Дифференцированное внесение (API-52, §7.4) -->
        <div
          id="vra"
          class="card360__block"
        >
          <h2 class="card360__block-title">
            Дифференцированное внесение
          </h2>
          <Skeleton
            v-if="vraLoading"
            :lines="4"
          />
          <p
            v-else-if="!vra"
            class="card360__muted"
          >
            Нет данных для зон внесения за сезон {{ selectedYear ?? '—' }} (нужна чистая NDVI-сцена)
          </p>
          <template v-else>
            <p class="card360__muted">
              По чистой сцене {{ fmtDate(vra.date) }} ·
              неоднородность NDVI (IQR): {{ fmtNum(vra.iqr_ndvi, 2) }} ·
              пространственная: {{ fmtNum(vra.spatial_spread, 2) }}
            </p>
            <!-- SVG-превью карты зон: PNG-оверлей в рамке контура (§7.4);
                 клик — полноэкранный лайтбокс с зумом (#141, #447) -->
            <button
              type="button"
              class="card360__vra-map-btn"
              :disabled="!vraMap"
              :title="vraMap ? 'Открыть карту на весь экран' : 'Карта недоступна'"
              @click="vraLightbox = vraMap !== null"
            >
              <svg
                class="card360__vra-map"
                viewBox="0 0 200 120"
                role="img"
                aria-label="Карта зон дифференцированного внесения"
              >
                <image
                  v-if="vraMap && vraMapRect"
                  class="card360__vra-layer"
                  :href="vraMap.url"
                  :x="vraMapRect.x"
                  :y="vraMapRect.y"
                  :width="vraMapRect.width"
                  :height="vraMapRect.height"
                  preserveAspectRatio="none"
                />
                <polygon
                  :points="contourPoints"
                  class="card360__vra-contour"
                />
              </svg>
            </button>
            <VraLightbox
              v-if="vraLightbox && vraMap"
              :url="vraMap.url"
              :caption="`Зоны дифвнесения · сцена ${fmtDate(vra.date)}`"
              @close="vraLightbox = false"
            />
            <table class="card360__vra-table">
              <thead>
                <tr>
                  <th
                    scope="col"
                    title="Номер зоны"
                  >
                    №
                  </th>
                  <th
                    scope="col"
                    title="Площадь зоны"
                  >
                    га
                  </th>
                  <th scope="col">
                    NDVI
                  </th>
                  <th
                    scope="col"
                    title="Доза удобрения"
                  >
                    кг/га
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="zone in vra.zones"
                  :key="zone.zone"
                  :class="`card360__vra-row--${zone.zone}`"
                  :title="`Зона ${zone.zone} — ${zone.label}`"
                >
                  <td>{{ zone.zone }}</td>
                  <td>{{ fmtNum(zone.area_ga) }}</td>
                  <td>{{ fmtNum(zone.ndvi_mean, 2) }}</td>
                  <td>{{ doseLabel(zone) }}</td>
                </tr>
              </tbody>
            </table>
            <p class="card360__muted card360__vra-legend">
              1 — пониженное · 2 — среднее · 3 — повышенное развитие
            </p>
            <p class="card360__muted">
              Дозы — рекомендация, не план. Базовая доза:
              {{
                vra.base_dose_kg_ha !== null
                  ? `${fmtNum(vra.base_dose_kg_ha)} кг/га (последний согласованный план)`
                  : 'нет согласованного плана внесения — показано отклонение от базовой'
              }}
            </p>
            <div class="card360__vra-export">
              <label class="card360__vra-export-label">
                Формат
                <select
                  v-model="exportFormat"
                  class="card360__vra-export-format"
                  aria-label="Формат выгрузки карты дифвнесения"
                >
                  <option
                    v-for="f in EXPORT_FORMATS"
                    :key="f.value"
                    :value="f.value"
                  >
                    {{ f.label }}
                  </option>
                </select>
              </label>
              <button
                type="button"
                class="card360__vra-export-btn"
                :disabled="exportBusy"
                @click="exportVra"
              >
                {{ exportBusy ? 'Готовим файл…' : 'Выгрузить' }}
              </button>
              <label
                v-if="exportNeedsDose"
                class="card360__vra-export-dose"
              >
                Базовая доза, кг/га
                <input
                  v-model="exportDose"
                  class="card360__vra-export-input"
                  type="number"
                  inputmode="decimal"
                  min="1"
                  max="2000"
                  step="1"
                  placeholder="например, 75"
                >
              </label>
              <p
                v-if="exportError"
                class="card360__vra-export-error"
                role="alert"
              >
                Выгрузка не удалась — попробуйте ещё раз
              </p>
            </div>
          </template>
        </div>

        <!-- Прогноз урожайности (API-53, §7.4) -->
        <div class="card360__block">
          <h2 class="card360__block-title">
            Прогноз урожайности
          </h2>
          <Skeleton
            v-if="yieldLoading"
            :lines="2"
          />
          <p
            v-else-if="yieldForecast === 'insufficient'"
            class="card360__muted"
          >
            Данных сезона недостаточно для прогноза
          </p>
          <p
            v-else-if="!yieldForecast"
            class="card360__muted"
          >
            Прогноз недоступен
          </p>
          <div
            v-else
            class="card360__yield"
          >
            <p class="card360__yield-value">
              {{ fmtNum(yieldForecast.forecast_c_ha) }} ц/га
              <span class="card360__yield-range">
                интервал {{ fmtNum(yieldForecast.low_c_ha) }}–{{ fmtNum(yieldForecast.high_c_ha) }}
              </span>
            </p>
            <p class="card360__muted">
              {{ yieldForecast.culture_name }}, сезон {{ yieldForecast.cycle_year }} ·
              оценка по спутнику (NDVI), точность растёт с числом сезонов
            </p>
          </div>
        </div>

        <!-- Экономика сезона (API-62, §7.9; #163): план/факт стоимости
             техработ, руб/га, руб/т + детализация по операциям -->
        <div class="card360__block card360__block--econ">
          <h2 class="card360__block-title">
            Экономика сезона {{ selectedYear ?? currentCycle?.year ?? '' }}
          </h2>
          <Skeleton
            v-if="econLoading"
            :lines="3"
          />
          <template v-else-if="econ">
            <dl class="card360__econ-tiles">
              <div>
                <dt>План</dt>
                <dd>{{ fmtRub(econ.plan_cost_rub) }}</dd>
              </div>
              <div>
                <dt>Факт</dt>
                <dd>{{ fmtRub(econ.fact_cost_rub) }}</dd>
                <span
                  v-if="econNoFacts"
                  class="card360__muted card360__econ-hint"
                >работы не начаты</span>
              </div>
              <div>
                <dt>{{ econDiff > 0 ? 'Перерасход' : 'Экономия' }}</dt>
                <dd
                  class="card360__econ-diff"
                  :class="econDiff > 0 ? 'card360__econ-diff--bad' : 'card360__econ-diff--good'"
                >
                  {{ econDiff > 0 ? '+' : '' }}{{ fmtRub(Math.abs(econDiff)) }}
                </dd>
              </div>
              <div>
                <dt>Руб/га</dt>
                <dd>{{ fmtRub(econ.cost_per_ga_rub) }}</dd>
              </div>
            </dl>
            <p class="card360__econ-ton">
              <template v-if="econ.fact_cost_per_ton_rub != null">
                Себестоимость зерна:
                <strong>{{ fmtRub(econ.fact_cost_per_ton_rub) }}/т</strong>
                <span class="card360__muted card360__econ-unit">
                  (фактическая урожайность {{ fmtNum(econ.yield_c_ha ?? 0) }} ц/га)
                </span>
              </template>
              <template v-else>
                Себестоимость зерна: —
                <span class="card360__muted">укажите фактическую урожайность цикла</span>
              </template>
            </p>
            <div
              v-if="econ.operations.length"
              class="card360__econ-details"
            >
              <button
                type="button"
                class="card360__econ-toggle"
                @click="econOpen = !econOpen"
              >
                Детализация по операциям ({{ econ.operations.length }})
                {{ econOpen ? '▴' : '▾' }}
              </button>
              <ul
                v-if="econOpen"
                class="card360__econ-ops"
              >
                <li
                  v-for="op in econ.operations"
                  :key="`${op.type}-${op.title}`"
                  class="card360__econ-op"
                  :class="{ 'card360__econ-op--todo': op.fact == null }"
                >
                  <span class="card360__econ-op-title">
                    {{ op.title }}<template v-if="op.plan == null"> · вне плана</template>
                  </span>
                  <span class="card360__econ-op-side">
                    план:
                    <template v-if="op.plan">
                      {{ op.plan.date ? fmtDate(op.plan.date) : '—' }} ·
                      {{ op.plan.cost_rub != null ? fmtRub(op.plan.cost_rub) : 'стоимость не задана' }}
                    </template>
                    <template v-else>—</template>
                  </span>
                  <span class="card360__econ-op-side">
                    факт:
                    <template v-if="op.fact">
                      {{ op.fact.date ? fmtDate(op.fact.date) : '—' }} ·
                      {{ op.fact.cost_rub != null ? fmtRub(op.fact.cost_rub) : 'стоимость не задана' }}
                    </template>
                    <template v-else>не выполнено</template>
                  </span>
                </li>
              </ul>
            </div>
          </template>
          <p
            v-else
            class="card360__muted"
          >
            Экономика сезона недоступна
          </p>
        </div>

        <!-- Агрохимия -->
        <!-- Ассистент (#148): выводы (API-51) + «что делать сейчас» (API-55/56,
             §9.3) одним блоком с одной кнопкой «Пересчитать» -->
        <div class="card360__block card360__block--insights">
          <h2 class="card360__block-title">
            Выводы и рекомендации
          </h2>
          <!-- 1. Сводка-вердикт: LLM-сводка API-51; при source=rules её нет
               (только детекторы ниже), пустой список — «не выявлено» -->
          <Skeleton
            v-if="insightsLoading"
            :lines="3"
          />
          <p
            v-else-if="!insights"
            class="card360__muted"
          >
            Выводы недоступны
          </p>
          <template v-else>
            <p
              v-if="insights.summary"
              class="card360__insights-summary"
            >
              {{ insights.summary }}
            </p>
            <p
              v-else-if="!insights.items.length"
              class="card360__muted"
            >
              Отклонений по данным детекторов не выявлено.
            </p>
          </template>

          <!-- 2. «Что делать сейчас»: рекомендация API-55, пересчёт API-55
               POST 202 queued → поллинг GET; одна кнопка на весь блок -->
          <h3 class="card360__sub-title">
            Что делать сейчас
          </h3>
          <Skeleton
            v-if="adviceLoading"
            :lines="2"
          />
          <template v-else>
            <template v-if="advice">
              <p class="card360__advice-text">
                {{ advice.answer }}
              </p>
              <p class="card360__muted card360__advice-meta">
                Рассчитано {{ fmtDateTime(advice.created_at) }}{{
                  advice.status === 'fallback' ? ' · упрощённый расчёт' : ''
                }}
              </p>
            </template>
            <p
              v-else
              class="card360__muted"
            >
              Рекомендация ещё не рассчитана.
            </p>
            <p
              v-if="adviceError"
              class="card360__advice-error"
              role="alert"
            >
              Не удалось {{ advice ? 'пересчитать' : 'рассчитать' }} рекомендацию — попробуйте ещё раз
            </p>
            <div class="card360__advice-actions">
              <button
                type="button"
                class="card360__advice-btn"
                :disabled="adviceBusy"
                @click="recalcAssistant"
              >
                {{ adviceBusy ? 'Пересчитываем…' : advice ? 'Пересчитать' : 'Рассчитать' }}
              </button>
              <span
                v-if="adviceBusy"
                class="card360__muted"
              >Это занимает до минуты</span>
            </div>
            <div class="card360__advice-history">
              <button
                type="button"
                class="card360__advice-toggle"
                @click="adviceHistoryOpen = !adviceHistoryOpen"
              >
                История рекомендаций{{
                  adviceHistory.length ? ` (${adviceHistory.length})` : ''
                }}
                {{ adviceHistoryOpen ? '▴' : '▾' }}
              </button>
              <ul
                v-if="adviceHistoryOpen"
                class="card360__advice-list"
              >
                <li
                  v-for="item in adviceHistory"
                  :key="item.created_at"
                >
                  <span class="card360__advice-date">{{ fmtDate(item.created_at.slice(0, 10)) }}</span>
                  <span class="card360__advice-item-text">{{ item.answer }}</span>
                </li>
              </ul>
              <button
                v-if="adviceHistoryOpen && adviceHasMore"
                type="button"
                class="card360__advice-more"
                @click="loadAdviceHistory"
              >
                Показать ещё
              </button>
            </div>
          </template>

          <!-- 3. Детекторы (API-51): компактные пункты — визуально
               подчинены сводке и рекомендации -->
          <template v-if="!insightsLoading && insights && insights.items.length">
            <ul class="card360__insights">
              <li
                v-for="item in insights.items"
                :key="item.code"
                class="card360__insight"
                :class="`card360__insight--${item.severity}`"
              >
                <span
                  class="card360__insight-mark"
                  :title="item.severity"
                >{{ SEVERITY_MARKS[item.severity] }}</span>
                <span class="card360__insight-text">
                  {{ item.title }} — {{ item.detail }}<a
                    v-if="item.code === 'vra_heterogeneity'"
                    class="card360__insight-link"
                    href="#vra"
                  >зоны внесения</a>
                </span>
              </li>
            </ul>
          </template>
          <p
            v-if="!insightsLoading && insights"
            class="card360__muted card360__insights-meta"
          >
            По числовым детекторам (NDVI, NDWI, агрохимия){{
              insights.source === 'rules+llm' ? ' и сводке ИИ' : ''
            }} · {{ fmtDate(insights.generated_at.slice(0, 10)) }}
          </p>
        </div>
      </div>

      <!-- Работы: планы (API-15 field_id) + журнал (API-18 field_id) одной
           хронологией, свежие сверху (v2, docs/06 §7.2). -->
      <div
        v-if="activeTab === 'works'"
        class="card360__works"
      >
        <h2 class="card360__block-title">
          Работы по полю (план и факт)
        </h2>
        <Skeleton
          v-if="worksLoading"
          :lines="5"
        />
        <template v-else>
          <!-- #174 (§7.10): итоги сезона — те же числа, что в «Экономике
               сезона» (API-62 поля) -->
          <p
            v-if="econ"
            class="card360__wtotals"
          >
            Сезон {{ econ.year }}:
            план Σ <strong>{{ fmtThousand(econ.plan_cost_rub) }}</strong> ·
            факт Σ <strong>{{ fmtThousand(econ.fact_cost_rub) }}</strong> ·
            {{ econDiff > 0 ? 'перерасход' : 'экономия' }}
            <strong :class="econDiff > 0 ? 'card360__econ-diff--bad' : 'card360__econ-diff--good'">{{
              fmtThousand(Math.abs(econDiff))
            }}</strong>
            <span
              v-if="econNoFacts"
              class="card360__muted"
            >(работы не начаты)</span>
          </p>

          <!-- CRUD (§7.10): добавить плановую работу -->
          <div class="card360__wcrud">
            <button
              type="button"
              class="card360__wadd"
              @click="planFormOpen = !planFormOpen; void loadRefs()"
            >
              {{ planFormOpen ? 'Свернуть' : '+ Добавить работу' }}
            </button>
            <form
              v-if="planFormOpen"
              class="card360__wform"
              @submit.prevent="submitPlan"
            >
              <label>
                Тип
                <select
                  v-model="planForm.operation_type"
                  required
                >
                  <option
                    v-for="(label, key) in OP_LABELS"
                    :key="key"
                    :value="key"
                  >
                    {{ label }}
                  </option>
                </select>
              </label>
              <label>
                Дата плана
                <input
                  v-model="planForm.planned_date"
                  type="date"
                  required
                >
              </label>
              <label v-if="planForm.operation_type === 'spraying' || planForm.operation_type === 'fertilizing'">
                Препарат
                <select v-model="planForm.input_id">
                  <option value="">
                    —
                  </option>
                  <option
                    v-for="i in inputsRef"
                    :key="i.input_id"
                    :value="i.input_id"
                  >
                    {{ i.name }}
                  </option>
                </select>
              </label>
              <label v-if="planForm.input_id">
                Доза, кг/га (л/га)
                <input
                  v-model.number="planForm.input_dose"
                  type="number"
                  min="0"
                  step="0.01"
                >
              </label>
              <label>
                Стоимость
                <span class="card360__wform-cost">
                  <select v-model="planForm.cost_plan_mode">
                    <option value="unit">
                      руб/га
                    </option>
                    <option value="total">
                      за поле
                    </option>
                  </select>
                  <input
                    v-model.number="planForm.cost_plan_value"
                    type="number"
                    min="0"
                    step="1"
                    placeholder="не задана"
                  >
                </span>
              </label>
              <button
                type="submit"
                class="card360__wsubmit"
                :disabled="crudBusy"
              >
                Создать план
              </button>
            </form>
          </div>

          <!-- Список работ сезона (#174, §7.10): строки только из API-18,
               группировка по месяцам -->
          <div
            v-for="group in worksGroups"
            :key="group.key"
            class="card360__wgroup"
          >
            <h3 class="card360__wgroup-title">
              {{ group.label }}
            </h3>
            <ul class="card360__works-list">
              <li
                v-for="op in group.ops"
                :key="op.operation_id"
                class="card360__works-item"
                :class="op.fact_date ? 'card360__works-item--fact' : 'card360__works-item--plan'"
              >
                <span
                  class="card360__works-chip"
                  :class="op.fact_date ? 'card360__works-chip--fact' : 'card360__works-chip--plan'"
                >{{ op.fact_date ? 'факт' : 'план · ожидается' }}</span>
                <span class="card360__works-date">
                  <template v-if="op.planned_date && op.fact_date && op.planned_date !== op.fact_date">
                    план {{ fmtDate(op.planned_date) }} → факт {{ fmtDate(op.fact_date) }}
                  </template>
                  <template v-else>{{ fmtDate(op.date) }}</template>
                </span>
                <span>
                  {{ OP_LABELS[op.operation_type] ?? op.operation_type }} ·
                  {{ fmtNum(op.actual_area_ga, 2) }} га<template v-if="op.input_name"> ·
                    {{ op.input_name }}<template v-if="op.input_dose != null">
                      {{ fmtNum(op.input_dose, 2) }} {{ op.input_id && inputsRef.find((i) => i.input_id === op.input_id)?.unit === 'l_ha' ? 'л/га' : 'кг/га' }}
                    </template>
                  </template>
                </span>
                <span
                  v-if="machinesTitle(op)"
                  class="card360__wmachines"
                  :title="machinesTitle(op)"
                >🚜</span>
                <span class="card360__works-cost">{{ worksCost(op) }}</span>
                <span
                  v-if="op.performer"
                  class="card360__works-status"
                >{{ op.performer }}</span>
                <span
                  v-if="op.confirmed_at"
                  class="card360__wconfirmed"
                  :title="`Подтверждено${op.confirmed_by ? `: ${op.confirmed_by}` : ''} ${fmtDate(op.confirmed_at.slice(0, 10))}`"
                >✓</span>
                <button
                  v-else-if="op.fact_date && canConfirm"
                  type="button"
                  class="card360__wconfirm"
                  :disabled="crudBusy"
                  @click="confirmOp(op)"
                >
                  Подтвердить
                </button>
                <button
                  v-if="!op.fact_date"
                  type="button"
                  class="card360__wclose"
                  :disabled="crudBusy"
                  @click="startComplete(op)"
                >
                  Закрыть фактом
                </button>
                <button
                  v-if="!op.fact_date"
                  type="button"
                  class="card360__wedit"
                  :disabled="crudBusy"
                  @click="startEdit(op)"
                >
                  Редактировать
                </button>
              </li>
            </ul>
          </div>

          <!-- Закрытие плана фактом (§7.10) -->
          <form
            v-if="completingOp"
            class="card360__wform card360__wform--complete"
            @submit.prevent="submitComplete"
          >
            <strong>Закрыть фактом: {{ OP_LABELS[completingOp.operation_type] }} от {{ fmtDate(completingOp.date) }}</strong>
            <label>
              Дата факта
              <input
                v-model="completeForm.fact_date"
                type="date"
                required
              >
            </label>
            <label v-if="completingOp.input_id">
              Расход всего, кг (л)
              <input
                v-model.number="completeForm.input_amount_total"
                type="number"
                min="0"
                step="0.1"
                placeholder="доза × площадь"
              >
            </label>
            <label>
              Стоимость факта
              <span class="card360__wform-cost">
                <select v-model="completeForm.cost_fact_mode">
                  <option value="unit">
                    руб/га
                  </option>
                  <option value="total">
                    за поле
                  </option>
                </select>
                <input
                  v-model.number="completeForm.cost_fact_value"
                  type="number"
                  min="0"
                  step="1"
                  placeholder="норматив"
                >
              </span>
            </label>
            <label>
              Исполнитель
              <input
                v-model="completeForm.performer"
                type="text"
              >
            </label>
            <label>
              Техника
              <select v-model="completeForm.machine_id">
                <option value="">
                  —
                </option>
                <option
                  v-for="m in machinesRef"
                  :key="m.machine_id"
                  :value="m.machine_id"
                >
                  {{ m.model }}{{ m.gov_number ? ` (${m.gov_number})` : '' }}
                </option>
              </select>
            </label>
            <button
              type="submit"
              class="card360__wsubmit"
              :disabled="crudBusy"
            >
              Закрыть фактом
            </button>
            <button
              type="button"
              class="card360__wcancel"
              @click="completingOp = null"
            >
              Отмена
            </button>
          </form>

          <p
            v-if="worksSeasonOps.length === 0"
            class="card360__muted"
          >
            Работ по полю в этом сезоне пока нет
          </p>
        </template>
      </div>
    </template>
  </section>
</template>

<style scoped>
/* Бейдж прошлого сезона в заголовке карточки (§7.5). */
.card360__season-badge {
  display: inline-block;
  vertical-align: middle;
  margin-left: 0.5rem;
  padding: 0.1rem 0.55rem;
  background: #fff3e0;
  border: 1px solid #ffcc80;
  border-radius: 999px;
  color: #8d5b00;
  font-size: 0.8rem;
  font-weight: 600;
}

.card360__tabs {
  display: flex;
  gap: 0.4rem;
}

.card360__tab {
  background: #fff;
  border: 2px solid #c8e6c9;
  border-radius: 8px 8px 0 0;
  padding: 0.45rem 1rem;
  font-size: 1rem;
  min-height: 44px;
  cursor: pointer;
  color: #1b5e20;
}

.card360__tab--active {
  background: #e8f5e9;
  font-weight: 700;
}

.card360__works {
  background: #fff;
  border: 1px solid #c8e6c9;
  border-radius: 10px;
  padding: 0.8rem 1rem;
}

.card360__works-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}

.card360__works-item {
  display: flex;
  gap: 0.6rem;
  align-items: baseline;
  flex-wrap: wrap;
  font-size: 0.98rem;
}

.card360__works-chip {
  border-radius: 6px;
  padding: 0.05rem 0.45rem;
  font-size: 0.8rem;
}

.card360__works-chip--plan {
  border: 1.5px dashed #2e7d32;
  color: #1b5e20;
  background: #f1f8f1;
}

.card360__works-chip--fact {
  background: #2e7d32;
  color: #fff;
}

.card360__works-date {
  font-variant-numeric: tabular-nums;
  color: #424242;
}

.card360__works-status {
  color: #757575;
  font-size: 0.9rem;
}

/* #665/#171: цена работы в строке и итоги сезона вкладки «Работы» */
.card360__works-cost {
  font-variant-numeric: tabular-nums;
  color: #37474f;
  font-size: 0.9rem;
  white-space: nowrap;
}

.card360__wtotals {
  margin: 0.2rem 0 0.6rem;
  font-size: 0.95rem;
  font-variant-numeric: tabular-nums;
}

/* ── CRUD и группировка вкладки «Работы» (§7.10, #174) ── */

.card360__wgroup {
  margin-top: 0.5rem;
}

.card360__wgroup-title {
  font-size: 0.95rem;
  margin: 0.4rem 0 0.2rem;
  color: #33691e;
}

.card360__wmachines {
  cursor: help;
}

.card360__wconfirmed {
  color: #2e7d32;
  font-weight: 700;
  cursor: help;
}

.card360__wcrud {
  margin: 0.3rem 0;
}

.card360__wadd {
  background: #fff;
  color: #2e7d32;
  border: 1.5px solid #2e7d32;
  border-radius: 8px;
  padding: 0.35rem 0.8rem;
  font-weight: 600;
  cursor: pointer;
  min-height: 38px;
}

.card360__wform {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem 0.9rem;
  align-items: flex-end;
  border: 1px solid #c8e6c9;
  border-radius: 8px;
  padding: 0.6rem 0.75rem;
  margin-top: 0.5rem;
  background: #f7faf7;
}

.card360__wform label {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  font-size: 0.85rem;
  color: #444;
}

.card360__wform select,
.card360__wform input {
  min-height: 36px;
  font-size: 0.95rem;
}

.card360__wform-cost {
  display: flex;
  gap: 0.35rem;
}

.card360__wsubmit {
  min-height: 38px;
  background: #2e7d32;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 0.35rem 0.9rem;
  font-weight: 600;
  cursor: pointer;
}

.card360__wcancel {
  min-height: 38px;
  background: none;
  border: none;
  color: #666;
  cursor: pointer;
}

.card360__wclose {
  border: 1px solid #2e7d32;
  background: #fff;
  color: #2e7d32;
  border-radius: 8px;
  padding: 0.15rem 0.6rem;
  font-size: 0.85rem;
  cursor: pointer;
  min-height: 32px;
}

.card360__wconfirm {
  border: 1px solid #1565c0;
  background: #fff;
  color: #1565c0;
  border-radius: 8px;
  padding: 0.15rem 0.6rem;
  font-size: 0.85rem;
  cursor: pointer;
  min-height: 32px;
}

.card360 {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  padding: 1rem;
}

.card360__header {
  display: flex;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
}

.card360__back {
  color: #1b5e20;
  font-weight: 600;
  text-decoration: none;
  min-height: 48px;
  display: inline-flex;
  align-items: center;
}

.card360__back:hover {
  text-decoration: underline;
}

.card360__title {
  font-size: 1.25rem;
  margin: 0;
  flex: 1;
}

.card360__area {
  font-weight: 400;
  color: #424242;
}

.card360__edit {
  background: #fff;
  color: #2e7d32;
  border: 2px solid #2e7d32;
  border-radius: 8px;
  padding: 0.5rem 0.9rem;
  font-size: 1rem;
  font-weight: 600;
  min-height: 48px;
  cursor: not-allowed;
  opacity: 0.6;
}

.card360__grid {
  display: grid;
  /* #644: колонки данных паспорта шире на +10% (300→330px минимум) —
   *  auto-fit кладёт меньше колонок в ряд, каждая заметно шире; отступы
   *  чуть ужаты в пользу ширины колонок */
  grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
  gap: 0.7rem;
  align-items: start;
}

/* Карта поля — на всю ширину карточки и крупнее (v2-fix-I) */
.card360__block--map {
  grid-column: 1 / -1;
}

.card360__map-wrap {
  position: relative;
}

.card360__zoom {
  position: absolute;
  top: 0.4rem;
  right: 0.4rem;
  display: flex;
  flex-direction: column;
  gap: 2px;
  z-index: 2;
}

.card360__zoom-btn {
  width: 1.8rem;
  height: 1.8rem;
  border: 1px solid #c8e6c9;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.92);
  color: #1b5e20;
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
}

.card360__zoom-btn:hover {
  background: #e8f5e9;
}

.card360__block {
  background: #fff;
  border: 1px solid #c8e6c9;
  border-radius: 10px;
  padding: 0.8rem 1rem;
}

.card360__block-title {
  font-size: 1rem;
  margin: 0.2rem 0 0.5rem;
  color: #1b5e20;
}

.card360__contour {
  width: 100%;
  height: auto;
  max-height: 300px;
  /* зум/пан (v2-fix-I): жесты перетаскивания не скроллят страницу */
  touch-action: none;
  cursor: grab;
}

.card360__contour--dragging {
  cursor: grabbing;
}

polygon.card360__polygon {
  fill: #a5d6a7;
  /* #572 п.2: обводку контура убрали — слой снимка читается чище,
   *  сам контур остаётся виден заливкой (когда слоя нет) */
  stroke: none;
}

/* §7.3: PNG-слой под контуром; при активном слое контур stroke-only,
 *  чтобы заливка не перекрывала снимок (v2-fix-G) */
.card360__ndvi-layer {
  opacity: 0.95;
}

.card360__contour--layered polygon.card360__polygon {
  fill-opacity: 0;
}

.card360__layers {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-top: 0.6rem;
}

/* #605 (#583/#151): гарантированно одна строка на реальном телефоне
 * (320–390px, системный шрифт крупнее headless) — компактный шрифт 0.7rem,
 * letter-spacing 0, минимальные паддинги; wrap оставлен как защита */
.card360__layer-switch {
  display: flex;
  flex-wrap: wrap;
  gap: 1px;
  border: 1px solid #c8e6c9;
  border-radius: 10px;
  padding: 2px;
  max-width: 100%;
}

.card360__layer-btn {
  border: none;
  border-radius: 8px;
  background: #f1f8f1;
  color: #1b5e20;
  font-size: 0.7rem;
  letter-spacing: 0;
  padding: 0.16rem 0.3rem;
  min-height: 30px;
  white-space: nowrap;
  flex: 0 0 auto;
  cursor: pointer;
}

/* #608/#609: на десктопе плашки крупнее и читабельнее (~+10–15% к мобильным
 * 0.7rem; прежний десктопный 0.8rem), ряд один на всех ширинах — узкая
 * колонка карточки (~265px) ограничивает рост, проверено 600–1920px */
@media (min-width: 600px) {
  .card360__layer-btn {
    font-size: 0.8rem;
    padding: 0.18rem 0.3rem;
    min-height: 34px;
  }
}

.card360__layer-btn--active {
  background: #2e7d32;
  color: #fff;
  font-weight: 600;
}

/* #130: индекс без сцен за сезон — кнопка задизейблена (title-подсказка) */
.card360__layer-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.card360__layer-date-label {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.85rem;
  color: #33691e;
}

/* ── «Выводы ассистента» (API-51, §7.4) ── */

.card360__insights {
  list-style: none;
  margin: 0.2rem 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}

.card360__insight {
  display: flex;
  gap: 0.5rem;
  align-items: flex-start;
  padding: 0.45rem 0.6rem;
  border-radius: 8px;
  background: #f5f5f5;
  font-size: 0.88rem;
}

.card360__insight--alert {
  background: #ffebee;
}

.card360__insight--warn {
  background: #fff8e1;
}

.card360__insight--info {
  background: #e8f5e9;
}

.card360__insight-mark {
  flex: 0 0 auto;
  font-weight: 700;
}

.card360__insight--alert .card360__insight-mark {
  color: #c62828;
}

.card360__insight--warn .card360__insight-mark {
  color: #ef6c00;
}

.card360__insight--info .card360__insight-mark {
  color: #1b5e20;
}

.card360__insight-text {
  flex: 1 1 auto;
}

.card360__insight-link {
  margin-left: 0.4rem;
  color: #1565c0;
}

.card360__insights-summary {
  margin: 0.2rem 0;
  font-size: 0.9rem;
}

.card360__insights-meta {
  font-size: 0.78rem;
}

/* Заголовок смысловой части объединённого блока ассистента (#148) */
.card360__sub-title {
  margin: 0.9rem 0 0.3rem;
  font-size: 0.95rem;
  font-weight: 600;
  color: #1b5e20;
}

.card360__advice-meta {
  font-size: 0.78rem;
}

/* ── «Что делать сейчас» (API-55/56, #137) ── */
.card360__advice-text {
  margin: 0;
  line-height: 1.45;
}

.card360__advice-actions {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-wrap: wrap;
  margin: 0.5rem 0;
}

.card360__advice-btn {
  min-height: 40px;
  padding: 0.35rem 1rem;
  border-radius: 8px;
  border: 1px solid #2e7d32;
  background: #2e7d32;
  color: #fff;
  font-size: 0.95rem;
  cursor: pointer;
}

.card360__advice-btn:disabled {
  opacity: 0.65;
  cursor: progress;
}

.card360__advice-error {
  margin: 0.3rem 0;
  color: #b3261e;
  font-size: 0.9rem;
}

.card360__advice-toggle {
  border: 0;
  background: none;
  padding: 0.3rem 0;
  color: #2e7d32;
  font-size: 0.9rem;
  cursor: pointer;
}

.card360__advice-list {
  list-style: none;
  margin: 0.4rem 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.card360__advice-list li {
  border-left: 3px solid #a5d6a7;
  padding-left: 0.6rem;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.card360__advice-date {
  font-size: 0.78rem;
  color: #66756a;
}

.card360__advice-item-text {
  font-size: 0.9rem;
  line-height: 1.4;
}

.card360__advice-more {
  border: 1px solid #9db59e;
  border-radius: 8px;
  background: #fff;
  min-height: 36px;
  padding: 0.2rem 0.9rem;
  font-size: 0.9rem;
  cursor: pointer;
}


.card360__check {
  margin: 0.6rem 0 0;
  padding: 0.5rem 0.6rem;
  border-radius: 8px;
  background: #e8f5e9;
}

.card360__allworks {
  background: none;
  border: none;
  color: #2e7d32;
  font-weight: 600;
  font-size: 0.9rem;
  cursor: pointer;
  padding: 0.25rem 0;
}

.card360__check--warn {
  background: #fff8e1;
}

.card360__muted {
  color: #616161;
  margin: 0.2rem 0;
}

/* ── Экономика сезона (API-62, §7.9; #163) ── */

.card360__econ-tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(8.5rem, 1fr));
  gap: 0.5rem;
  margin: 0.4rem 0;
}

.card360__econ-tiles > div {
  border: 1px solid #d7e0d8;
  border-radius: 8px;
  padding: 0.45rem 0.6rem;
  background: #f7faf7;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.card360__econ-tiles dt {
  font-size: 0.8rem;
  color: #66756a;
}

.card360__econ-tiles dd {
  margin: 0;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.card360__econ-diff--good {
  color: #1b5e20;
}

.card360__econ-diff--bad {
  color: #b3261e;
}

.card360__econ-ton {
  margin: 0.2rem 0 0.4rem;
  font-variant-numeric: tabular-nums;
}

/* единицы («₽/т», «ц/га») не рвутся при переносе строки */
.card360__econ-ton strong,
.card360__econ-unit {
  white-space: nowrap;
}

/* #635/#165: нейтральная подпись к плитке факта без выполненных работ */
.card360__econ-hint {
  font-size: 0.75rem;
}

.card360__econ-toggle {
  border: none;
  background: none;
  padding: 0.2rem 0;
  color: #2e7d32;
  font-size: 0.9rem;
  font-weight: 600;
  cursor: pointer;
}

.card360__econ-ops {
  list-style: none;
  margin: 0.3rem 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.card360__econ-op {
  border: 1px solid #e0e8e1;
  border-radius: 8px;
  padding: 0.4rem 0.6rem;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  font-size: 0.88rem;
}

/* план без факта — приглушаем всю строку (§7.9: fact=null — не выполнено) */
.card360__econ-op--todo {
  background: #f5f5f5;
  color: #757575;
}

.card360__econ-op-title {
  font-weight: 600;
}

.card360__econ-op-side {
  font-variant-numeric: tabular-nums;
  color: inherit;
}

.card360__agrochem,
.card360__weather {
  line-height: 1.6;
}

.card360__forecast {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.95rem;
}

.card360__forecast th,
.card360__forecast td {
  text-align: left;
  padding: 0.3rem 0.4rem;
  border-bottom: 1px solid #e0e0e0;
}

/* ── Дифференцированное внесение (§7.4): карта зон + таблица ── */

/* Превью карты — кнопка, открывающая лайтбокс (#141) */
.card360__vra-map-btn {
  display: block;
  padding: 0;
  border: 0;
  background: none;
  cursor: zoom-in;
}

.card360__vra-map-btn:disabled {
  cursor: default;
}

.card360__vra-map-btn .card360__vra-map {
  display: block;
}

.card360__vra-map {
  width: 100%;
  max-width: 280px;
  background: #f1f8e9;
  border: 1px solid #c5e1a5;
  border-radius: 6px;
}

.card360__vra-legend {
  font-size: 0.8rem;
  margin: 0;
}

.card360__vra-contour {
  fill: none;
  stroke: #33691e;
  stroke-width: 1.5;
}

.card360__vra-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.95rem;
}

.card360__vra-table th,
.card360__vra-table td {
  text-align: left;
  padding: 0.3rem 0.4rem;
  border-bottom: 1px solid #e0e0e0;
}

/* Цветной маркер зоны слева (зона 1 — красная, 3 — зелёная) */
.card360__vra-table tbody td:first-child {
  box-shadow: inset 3px 0 0 #ccc;
}

.card360__vra-row--1 td:first-child {
  box-shadow: inset 3px 0 0 #e57373;
}

.card360__vra-row--2 td:first-child {
  box-shadow: inset 3px 0 0 #fff176;
}

.card360__vra-row--3 td:first-child {
  box-shadow: inset 3px 0 0 #81c784;
}

/* Выгрузка карты дифвнесения (API-60, #140): формат + кнопка,
   при 422 vra_no_base_dose — поле ввода базовой дозы. */
.card360__vra-export {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.4rem;
  font-size: 0.85rem;
}

.card360__vra-export-label,
.card360__vra-export-dose {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  color: #455a64;
}

.card360__vra-export-format,
.card360__vra-export-input {
  border: 1px solid #cfd8dc;
  border-radius: 6px;
  padding: 0.3rem 0.45rem;
  font-size: 0.85rem;
  background: #fff;
}

.card360__vra-export-btn {
  border: 1px solid var(--cda-green, #2e7d32);
  background: var(--cda-green, #2e7d32);
  color: #fff;
  border-radius: 6px;
  padding: 0.35rem 0.8rem;
  font-size: 0.85rem;
  cursor: pointer;
}

.card360__vra-export-btn:disabled {
  opacity: 0.6;
  cursor: progress;
}

.card360__vra-export-input {
  width: 7.5rem;
}

.card360__vra-export-error {
  margin: 0;
  color: #c62828;
}

/* ── Прогноз урожайности (§7.4) ── */

.card360__yield-value {
  font-size: 1.4rem;
  font-weight: 600;
  margin: 0.2rem 0;
}

.card360__yield-range {
  font-size: 1rem;
  font-weight: 400;
  color: #616161;
}

.card360__plans {
  list-style: none;
  margin: 0 0 0.4rem;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.card360__state {
  color: #424242;
  padding: 1rem;
}

.card360__state--error {
  color: #b00020;
  font-weight: 600;
}
</style>
