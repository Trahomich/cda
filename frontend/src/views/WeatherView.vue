<script setup lang="ts">
// E-10 «Погода и погодные окна» (docs/07-ux §2, сценарий SC-03; фидбек user
// #391, задача #134): текущая погода (API-07), прогноз 7 суток (API-29),
// история погоды с диапазонами 90 дней/сезон/год (API-57 §7.8), GDD с 1 января
// (API-58 §7.8) и погодные окна только по активным планам поля
// (RULE-AGRO-09, /weather/windows only_planned — API-59 §7.8).
import { computed, onMounted, ref, watch } from 'vue'
import FieldCombobox from '@/components/FieldCombobox.vue'
import GddChart from '@/components/GddChart.vue'
import WeatherHistoryChart from '@/components/WeatherHistoryChart.vue'
import {
  getFields,
  getFieldWeather,
  getWeatherForecast,
  getWeatherGdd,
  getWeatherHistory,
  getWeatherWindowsPlanned,
} from '@/api'
import type {
  Field,
  FieldWeather,
  WeatherForecast,
  WeatherGdd,
  WeatherHistory,
  WeatherWindowItem,
  WindowOperationType,
} from '@/api/types'

const LS_FIELD = 'cda.weather.field'
const LS_RECENT = 'cda.weather.recent'

const fields = ref<Field[]>([])
const fieldId = ref('')
const recent = ref<string[]>([])
const weather = ref<FieldWeather | null>(null)
const forecast = ref<WeatherForecast | null>(null)
const windows = ref<WeatherWindowItem[]>([])
const windowsHint = ref<string | null>(null)
const loading = ref(true)
const failed = ref(false)

/** Диапазоны истории: 90 дней / сезон / год (фидбек #391 п.2). */
type RangeKind = '90d' | 'season' | 'year'
const RANGES: Array<{ value: RangeKind; label: string }> = [
  { value: '90d', label: '90 дней' },
  { value: 'season', label: 'Сезон' },
  { value: 'year', label: 'Год' },
]
const range = ref<RangeKind>('90d')
const rangeYear = ref(new Date().getFullYear())
const CURRENT_YEAR = new Date().getFullYear()
const years = computed(() => {
  const out: number[] = []
  for (let y = CURRENT_YEAR; y >= 2023; y--) out.push(y)
  return out
})

/** История и GDD: до деплоя бэкенда (#132) — нейтральное «скоро». */
const historyDays = ref<WeatherHistory['items']>([])
const historyState = ref<'loading' | 'ok' | 'soon'>('loading')
const gdd = ref<WeatherGdd | null>(null)
const gddState = ref<'loading' | 'ok' | 'soon'>('loading')

/** Подписи типов операций (RULE-AGRO-09, 03 §3.3). */
const OP_LABELS: Record<WindowOperationType, string> = {
  spraying: 'Опрыскивание (СЗР)',
  fertilizing: 'Внесение удобрений (жидкие)',
  seeding: 'Посев (ранние яровые)',
  harvesting: 'Уборка',
}

function opLabel(t: WindowOperationType): string {
  return OP_LABELS[t] ?? t
}

const selectedField = computed(() => fields.value.find((f) => f.field_id === fieldId.value) ?? null)

function rangeDates(kind: RangeKind, year: number): { from: string; to: string; label: string } {
  const iso = (d: Date) => d.toISOString().slice(0, 10)
  if (kind === '90d') {
    const to = new Date()
    const from = new Date(to.getTime() - 89 * 86_400_000)
    return { from: iso(from), to: iso(to), label: '90 дней' }
  }
  if (kind === 'season') {
    return { from: `${year}-04-01`, to: `${year}-09-30`, label: `сезон ${year}` }
  }
  return { from: `${year}-01-01`, to: `${year}-12-31`, label: `${year} год` }
}

const rangeLabel = computed(() => rangeDates(range.value, rangeYear.value).label)

async function loadHistory() {
  if (!fieldId.value) return
  historyState.value = 'loading'
  const { from, to } = rangeDates(range.value, rangeYear.value)
  try {
    const h = await getWeatherHistory(fieldId.value, from, to)
    historyDays.value = h.items
    historyState.value = h.items.length ? 'ok' : 'soon'
  } catch {
    historyDays.value = []
    historyState.value = 'soon'
  }
}

async function loadGdd() {
  if (!fieldId.value) return
  gddState.value = 'loading'
  try {
    gdd.value = await getWeatherGdd(fieldId.value, CURRENT_YEAR)
    gddState.value = gdd.value.items.length ? 'ok' : 'soon'
  } catch {
    gdd.value = null
    gddState.value = 'soon'
  }
}

async function load() {
  if (!fieldId.value) return
  loading.value = true
  failed.value = false
  windows.value = []
  windowsHint.value = null
  try {
    const [w, fc] = await Promise.all([
      getFieldWeather(fieldId.value),
      getWeatherForecast(fieldId.value, 7),
    ])
    weather.value = w
    forecast.value = fc
    await loadWindows()
    // История/GDD грузим параллельно с остальным экраном (не блокируем его).
    void loadHistory()
    void loadGdd()
  } catch {
    failed.value = true // тост уже показан
  } finally {
    loading.value = false
  }
}

/** Окна по планам (API-59, §7.8): до деплоя #133 списочной формы на бэкенде
 *  нет — блок деградирует нейтральным «скоро» (#416), без тоста (silent). */
const windowsState = ref<'loading' | 'ok' | 'soon'>('loading')

async function loadWindows() {
  if (!fieldId.value) return
  windowsState.value = 'loading'
  windowsHint.value = null
  try {
    const win = await getWeatherWindowsPlanned(fieldId.value)
    windows.value = win.items
    windowsHint.value = win.hint
    windowsState.value = 'ok'
  } catch {
    windows.value = []
    windowsState.value = 'soon'
  }
}

/** Запоминание выбранного поля и «недавних» (фидбек #391 п.1). */
function rememberField(id: string) {
  localStorage.setItem(LS_FIELD, id)
  const prev = recent.value.filter((x) => x !== id)
  recent.value = [id, ...prev].slice(0, 5)
  localStorage.setItem(LS_RECENT, JSON.stringify(recent.value))
}

function onFieldSelect(id: string) {
  fieldId.value = id
}

const RU_DATE = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', weekday: 'short' })

function fmtDate(iso: string): string {
  return RU_DATE.format(new Date(iso))
}

onMounted(async () => {
  try {
    recent.value = JSON.parse(localStorage.getItem(LS_RECENT) ?? '[]') as string[]
  } catch {
    recent.value = []
  }
  try {
    const page = await getFields({ status: 'all' })
    fields.value = page.items.filter((f) => f.status === 'active')
    const saved = localStorage.getItem(LS_FIELD)
    fieldId.value =
      saved && fields.value.some((f) => f.field_id === saved)
        ? saved
        : (fields.value[0]?.field_id ?? '')
    // Смена fieldId запускает загрузку (watch ниже); без полей — не грузим.
    if (!fieldId.value) loading.value = false
  } catch {
    failed.value = true
    loading.value = false
  }
})

watch(fieldId, (id) => {
  if (!id) return
  rememberField(id)
  void load()
})
watch([range, rangeYear], () => void loadHistory())
</script>

<template>
  <section class="wx">
    <header class="wx__header">
      <h1 class="wx__title">
        Погода и погодные окна
      </h1>
      <span class="wx__spacer" />
      <FieldCombobox
        :fields="fields"
        :model-value="fieldId || null"
        :recent="recent"
        aria-label="Поле"
        @update:model-value="onFieldSelect"
      />
    </header>

    <!-- Карточка выбранного поля: культура сезона и площадь (#391 п.1). -->
    <div
      v-if="selectedField"
      class="wx__field-card"
    >
      <span class="wx__field-name">{{ selectedField.name }}</span>
      <span class="wx__field-meta">
        {{
          selectedField.current_cycle
            ? `${selectedField.current_cycle.year}: ${selectedField.current_cycle.culture_name}`
            : 'культура сезона не задана'
        }}
      </span>
      <span class="wx__field-meta">
        {{ selectedField.area_ga.toLocaleString('ru-RU') }} га
      </span>
    </div>

    <p
      v-if="loading"
      class="wx__state"
    >
      Загрузка погоды…
    </p>
    <p
      v-else-if="failed"
      class="wx__state wx__state--error"
    >
      Не удалось загрузить погодные данные
    </p>

    <template v-else-if="weather && forecast">
      <p
        v-if="forecast.stale"
        class="wx__stale"
      >
        Источник погоды недоступен — показан последний сохранённый прогноз (данные устарели)
      </p>

      <div class="wx__now">
        <div class="wx__now-card">
          <span class="wx__now-caption">Сейчас{{ selectedField ? ` · ${selectedField.name}` : '' }}</span>
          <span class="wx__now-temp">{{ weather.now.temp_c.toLocaleString('ru-RU') }} °C</span>
          <dl class="wx__now-list">
            <div>
              <dt>Ветер</dt>
              <dd>{{ weather.now.wind_ms.toLocaleString('ru-RU') }} м/с</dd>
            </div>
            <div>
              <dt>Осадки (факт)</dt>
              <dd>{{ weather.now.precip_mm.toLocaleString('ru-RU') }} мм</dd>
            </div>
            <div>
              <dt>Влажность</dt>
              <dd>{{ weather.now.humidity_pct }} %</dd>
            </div>
          </dl>
        </div>
        <div class="wx__now-card">
          <span class="wx__now-caption">Итоги 7 суток</span>
          <dl class="wx__now-list">
            <div>
              <dt>Осадки</dt>
              <dd>{{ weather.summary.precip_7d_mm.toLocaleString('ru-RU') }} мм</dd>
            </div>
            <div>
              <dt>Сумма эффективных температур</dt>
              <dd>{{ weather.summary.gdd.toLocaleString('ru-RU') }} °C·сут</dd>
            </div>
            <div>
              <dt>GDD с 1 января</dt>
              <dd>{{ gddState === 'ok' && gdd ? `${gdd.total.toLocaleString('ru-RU')} °C·сут` : '—' }}</dd>
            </div>
          </dl>
        </div>
      </div>

      <h2 class="wx__subtitle">
        История погоды
      </h2>
      <div class="wx__hist">
        <div
          class="wx__ranges"
          role="tablist"
          aria-label="Диапазон истории"
        >
          <button
            v-for="r in RANGES"
            :key="r.value"
            type="button"
            class="wx__range"
            :class="{ 'wx__range--active': range === r.value }"
            role="tab"
            :aria-selected="range === r.value"
            @click="range = r.value"
          >
            {{ r.label }}
          </button>
          <select
            v-if="range !== '90d'"
            v-model.number="rangeYear"
            class="wx__year"
            aria-label="Год"
          >
            <option
              v-for="y in years"
              :key="y"
              :value="y"
            >
              {{ y }}
            </option>
          </select>
        </div>
        <p
          v-if="historyState === 'loading'"
          class="wx__state"
        >
          Загрузка истории…
        </p>
        <p
          v-else-if="historyState === 'soon'"
          class="wx__state wx__state--soon"
        >
          История погоды за {{ rangeLabel }} скоро появится — сервис погоды обновляется
        </p>
        <WeatherHistoryChart
          v-else
          :days="historyDays"
        />
      </div>

      <h2 class="wx__subtitle">
        Эффективные температуры (GDD) с 1 января {{ CURRENT_YEAR }}
      </h2>
      <p
        v-if="gddState === 'loading'"
        class="wx__state"
      >
        Загрузка GDD…
      </p>
      <p
        v-else-if="gddState === 'soon'"
        class="wx__state wx__state--soon"
      >
        Накопительная кривая GDD скоро появится — сервис погоды обновляется
      </p>
      <template v-else-if="gdd">
        <p class="wx__gdd-total">
          Накоплено на {{ gdd.items[gdd.items.length - 1]?.date }}:
          <strong>{{ gdd.total.toLocaleString('ru-RU') }} °C·сут</strong>
          (база {{ gdd.base_c }} °C)
        </p>
        <GddChart
          :items="gdd.items"
          :norm="gdd.norm_cum"
        />
      </template>

      <h2 class="wx__subtitle">
        Прогноз, {{ forecast.days.length }} сут.
      </h2>
      <div class="wx__table-wrap">
        <table class="wx__table">
          <thead>
            <tr>
              <th scope="col">
                Дата
              </th>
              <th scope="col">
                Температура, °C
              </th>
              <th scope="col">
                Осадки, мм
              </th>
              <th scope="col">
                Вероятность, %
              </th>
              <th scope="col">
                Ветер, м/с
              </th>
              <th scope="col">
                Влажность, %
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="d in forecast.days"
              :key="d.date"
            >
              <td>{{ fmtDate(d.date) }}</td>
              <td class="wx__num">
                {{ d.temp_min_c.toLocaleString('ru-RU') }} … {{ d.temp_max_c.toLocaleString('ru-RU') }}
              </td>
              <td class="wx__num">
                {{ d.precip_mm.toLocaleString('ru-RU') }}
              </td>
              <td class="wx__num">
                {{ d.precip_prob_pct != null ? d.precip_prob_pct.toLocaleString('ru-RU') : '—' }}
              </td>
              <td class="wx__num">
                {{ d.wind_ms.toLocaleString('ru-RU') }}
              </td>
              <td class="wx__num">
                {{ d.humidity_pct }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2 class="wx__subtitle">
        Погодные окна по планам поля (RULE-AGRO-09)
      </h2>
      <p
        v-if="windowsState !== 'ok'"
        class="wx__state wx__state--soon"
      >
        {{ windowsState === 'loading' ? 'Загрузка окон…' : 'Погодные окна по планам поля скоро появятся — сервис погоды обновляется' }}
      </p>
      <p
        v-else-if="windowsHint"
        class="wx__state wx__state--soon"
      >
        На этом поле нет активных планов операций — погодные окна не оцениваются
      </p>
      <div
        v-else
        class="wx__windows"
      >
        <div
          v-for="t in windows"
          :key="t.operation_type"
          class="wx__window"
        >
          <div class="wx__window-head">
            <span class="wx__window-title">{{ opLabel(t.operation_type) }}</span>
            <span
              class="wx__badge"
              :class="t.nearest_window_date ? 'wx__badge--ok' : 'wx__badge--bad'"
            >
              {{ t.nearest_window_date
                ? `окно ${fmtDate(t.nearest_window_date)}`
                : 'окна нет в горизонте 7 сут.' }}
            </span>
          </div>
          <ul class="wx__days">
            <li
              v-for="d in t.days"
              :key="d.date"
              class="wx__day"
              :class="d.is_window ? 'wx__day--ok' : 'wx__day--bad'"
              :title="d.summary"
            >
              {{ fmtDate(d.date) }}
            </li>
          </ul>
          <p class="wx__window-note">
            <template v-if="t.nearest_window_date">
              {{ t.days.find((d) => d.date === t.nearest_window_date)?.summary }}
            </template>
            <template v-else>
              Ни одни сутки не подходят — в системе создаётся предупреждение (ENT-ALERT)
            </template>
          </p>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.wx {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  max-width: 72rem;
}

.wx__header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.wx__title {
  font-size: 1.35rem;
  margin: 0;
}

.wx__spacer {
  flex: 1;
}

.wx__field-card {
  display: flex;
  align-items: baseline;
  gap: 1rem;
  flex-wrap: wrap;
  border: 1px solid #c8d6c9;
  border-radius: 10px;
  padding: 0.6rem 1rem;
  background: #f7faf7;
}

.wx__field-name {
  font-weight: 700;
  font-size: 1.1rem;
}

.wx__field-meta {
  color: #55685a;
}

.wx__state {
  color: #555;
}

.wx__state--error {
  color: #b3261e;
}

/* нейтральное «скоро» (#391): не выглядит ошибкой */
.wx__state--soon {
  background: #f1f4f1;
  border: 1px dashed #b9c9bb;
  border-radius: 8px;
  padding: 0.5rem 0.75rem;
}

.wx__stale {
  background: #fff3e0;
  border: 1px solid #e6a23c;
  border-radius: 8px;
  padding: 0.5rem 0.75rem;
  margin: 0;
}

.wx__now {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
  gap: 1rem;
}

.wx__now-card {
  border: 1px solid #c8d6c9;
  border-radius: 10px;
  padding: 0.75rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.wx__now-caption {
  font-weight: 600;
  color: #2e7d32;
}

.wx__now-temp {
  font-size: 2rem;
  font-weight: 700;
}

.wx__now-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
  gap: 0.4rem 1rem;
  margin: 0;
}

.wx__now-list dt {
  font-size: 0.85rem;
  color: #666;
}

.wx__now-list dd {
  margin: 0;
  font-weight: 600;
}

.wx__subtitle {
  font-size: 1.1rem;
  margin: 0.5rem 0 0;
}

.wx__ranges {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin: 0.4rem 0 0.6rem;
  flex-wrap: wrap;
}

.wx__range {
  border: 1px solid #9db59e;
  border-radius: 999px;
  background: #fff;
  padding: 0.3rem 0.9rem;
  min-height: 38px;
  font-size: 0.95rem;
  cursor: pointer;
}

.wx__range--active {
  background: #2e7d32;
  border-color: #2e7d32;
  color: #fff;
  font-weight: 600;
}

.wx__year {
  min-height: 38px;
  font-size: 0.95rem;
  border: 1px solid #9db59e;
  border-radius: 8px;
}

.wx__gdd-total {
  margin: 0.4rem 0;
}

.wx__table-wrap {
  overflow-x: auto;
}

.wx__table {
  border-collapse: collapse;
  width: 100%;
  background: #fff;
}

.wx__table th,
.wx__table td {
  border: 1px solid #d7e0d8;
  padding: 0.45rem 0.6rem;
  text-align: left;
}

.wx__table th {
  background: #eef4ef;
}

.wx__num {
  font-variant-numeric: tabular-nums;
}

.wx__windows {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr));
  gap: 1rem;
}

.wx__window {
  border: 1px solid #c8d6c9;
  border-radius: 10px;
  padding: 0.75rem 1rem;
}

.wx__window-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.wx__window-title {
  font-weight: 600;
}

.wx__badge {
  border-radius: 999px;
  padding: 0.15rem 0.6rem;
  font-size: 0.9rem;
  font-weight: 600;
}

.wx__badge--ok {
  background: #e2f2e4;
  color: #1b5e20;
}

.wx__badge--bad {
  background: #fdecea;
  color: #b3261e;
}

.wx__days {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  list-style: none;
  padding: 0;
  margin: 0.6rem 0;
}

.wx__day {
  border-radius: 6px;
  padding: 0.2rem 0.45rem;
  font-size: 0.9rem;
  border: 1px solid transparent;
}

.wx__day--ok {
  background: #e2f2e4;
  color: #1b5e20;
  border-color: #a5d6a7;
}

.wx__day--bad {
  background: #f5f5f5;
  color: #888;
}

.wx__window-note {
  margin: 0;
  font-size: 0.9rem;
  color: #555;
}
</style>
