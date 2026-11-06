<script setup lang="ts">
// E-18 «Техника и задания» (MVP-состав 08 §1; сценарий SC-04): парк техники
// (API-19 GET /machines, read-only в MVP) и задания смены (ENT-MACHINE-JOB):
// черновики из телематики подтверждаются человеком — API-20 POST confirm.
import { onMounted, ref } from 'vue'
import { confirmMachineJob, getMachineJobs, getMachines } from '@/api'
import type { Machine, MachineJob, MachineStatus } from '@/api/types'

const machines = ref<Machine[]>([])
const jobs = ref<MachineJob[]>([])
const loading = ref(true)
const failed = ref(false)
const busy = ref(false)

const TYPE_LABELS: Record<Machine['machine_type'], string> = {
  tractor: 'трактор',
  combine: 'комбайн',
  sprayer: 'опрыскиватель',
  spreader: 'разбрасыватель',
  seeder: 'сеялка',
  truck: 'автомобиль',
  loader: 'погрузчик',
  other: 'прочее',
}

const STATUS_LABELS: Record<MachineStatus, string> = {
  active: 'в работе',
  maintenance: 'ТО/ремонт',
  retired: 'выведена',
}

const JOB_STATUS_LABELS: Record<MachineJob['status'], string> = {
  draft: 'черновик (телематика)',
  confirmed: 'подтверждено',
  rejected: 'отклонено',
}

async function load() {
  loading.value = true
  failed.value = false
  try {
    const [ms, js] = await Promise.all([getMachines(), getMachineJobs()])
    machines.value = ms.items
    jobs.value = js.items
  } catch {
    failed.value = true // тост уже показан
  } finally {
    loading.value = false
  }
}

async function confirm(jobId: string) {
  busy.value = true
  try {
    await confirmMachineJob(jobId)
    await load()
  } catch {
    // тост уже показан
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="mch">
    <header class="mch__header">
      <h1 class="mch__title">
        Техника и задания
      </h1>
      <p class="mch__sub">
        Парк хозяйства и задания смены. Черновики из телематики подтверждаются
        человеком (SC-04); правка парка — через API-19 на этапе v1.
      </p>
    </header>

    <p
      v-if="loading"
      class="mch__state"
    >
      Загрузка…
    </p>
    <p
      v-else-if="failed"
      class="mch__state mch__state--error"
    >
      Не удалось загрузить данные по технике
    </p>

    <template v-else>
      <h2 class="mch__subtitle">
        Парк техники
      </h2>
      <div class="mch__table-wrap">
        <table class="mch__table">
          <thead>
            <tr>
              <th scope="col">
                Модель
              </th>
              <th scope="col">
                Тип
              </th>
              <th scope="col">
                Госномер
              </th>
              <th scope="col">
                Захват, м
              </th>
              <th scope="col">
                Бак, л
              </th>
              <th scope="col">
                Наработка, мото-ч
              </th>
              <th scope="col">
                Статус
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="m in machines"
              :key="m.machine_id"
            >
              <td>{{ m.model }}</td>
              <td>{{ TYPE_LABELS[m.machine_type] }}</td>
              <td>{{ m.gov_number ?? '—' }}</td>
              <td class="mch__num">
                {{ m.width_m !== null ? m.width_m.toLocaleString('ru-RU') : '—' }}
              </td>
              <td class="mch__num">
                {{ m.tank_capacity_l !== null ? m.tank_capacity_l.toLocaleString('ru-RU') : '—' }}
              </td>
              <td class="mch__num">
                {{ m.engine_hours.toLocaleString('ru-RU') }}
              </td>
              <td>
                <span
                  class="mch__badge"
                  :class="`mch__badge--${m.status}`"
                >{{ STATUS_LABELS[m.status] }}</span>
              </td>
            </tr>
            <tr v-if="machines.length === 0">
              <td
                colspan="7"
                class="mch__state"
              >
                Техника не найдена
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2 class="mch__subtitle">
        Задания смены
      </h2>
      <div class="mch__table-wrap">
        <table class="mch__table">
          <thead>
            <tr>
              <th scope="col">
                Дата
              </th>
              <th scope="col">
                Машина
              </th>
              <th scope="col">
                Площадь, га
              </th>
              <th scope="col">
                Топливо, л
              </th>
              <th scope="col">
                Длительность, ч
              </th>
              <th scope="col">
                Источник
              </th>
              <th scope="col">
                Статус
              </th>
              <th scope="col">
                Действие
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="j in jobs"
              :key="j.job_id"
              :class="{ 'mch__row--draft': j.status === 'draft' }"
            >
              <td>{{ j.performed_at }}</td>
              <td>{{ j.machine_model }}</td>
              <td class="mch__num">
                {{ j.area_ha.toLocaleString('ru-RU') }}
              </td>
              <td class="mch__num">
                {{ j.fuel_l !== null ? j.fuel_l.toLocaleString('ru-RU') : '—' }}
              </td>
              <td class="mch__num">
                {{ j.duration_h !== null ? j.duration_h.toLocaleString('ru-RU') : '—' }}
              </td>
              <td>{{ j.source === 'telemetry' ? 'телематика' : 'вручную' }}</td>
              <td>{{ JOB_STATUS_LABELS[j.status] }}</td>
              <td>
                <button
                  v-if="j.status === 'draft'"
                  type="button"
                  class="mch__confirm"
                  :disabled="busy"
                  @click="confirm(j.job_id)"
                >
                  Подтвердить
                </button>
                <span
                  v-else
                  class="mch__muted"
                >—</span>
              </td>
            </tr>
            <tr v-if="jobs.length === 0">
              <td
                colspan="9"
                class="mch__state"
              >
                Заданий не найдено
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>

<style scoped>
.mch {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  max-width: 88rem;
}

.mch__title {
  font-size: 1.35rem;
  margin: 0;
}

.mch__sub {
  margin: 0.25rem 0 0;
  color: #555;
  max-width: 48rem;
}

.mch__state {
  color: #555;
}

.mch__state--error {
  color: #b3261e;
}

.mch__subtitle {
  font-size: 1.1rem;
  margin: 0.5rem 0 0;
}

.mch__table-wrap {
  overflow-x: auto;
}

.mch__table {
  border-collapse: collapse;
  width: 100%;
  background: #fff;
}

.mch__table th,
.mch__table td {
  border: 1px solid #d7e0d8;
  padding: 0.45rem 0.6rem;
  text-align: left;
}

.mch__table th {
  background: #eef4ef;
}

.mch__row--draft {
  background: #fff8e1;
}

.mch__num {
  font-variant-numeric: tabular-nums;
}

.mch__badge {
  border-radius: 999px;
  padding: 0.15rem 0.6rem;
  font-size: 0.9rem;
  font-weight: 600;
}

.mch__badge--active {
  background: #e2f2e4;
  color: #1b5e20;
}

.mch__badge--maintenance {
  background: #fff3e0;
  color: #a15c00;
}

.mch__badge--retired {
  background: #f5f5f5;
  color: #666;
}

.mch__confirm {
  min-height: 40px;
  background: #2e7d32;
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 0 0.8rem;
  font-weight: 600;
  cursor: pointer;
}

.mch__muted {
  color: #777;
}
</style>
