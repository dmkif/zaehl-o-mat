<template>
  <div data-testid="monthly">
    <div v-if="loading" class="text-sm text-gray-500">{{ $t('common.loading') }}</div>
    <div v-else-if="error" class="text-sm text-red-600 dark:text-red-400" role="alert">{{ $t('stats.load_error') }}</div>
    <template v-else>
      <label v-if="series.length" class="flex items-center gap-2 text-xs text-gray-500 mb-2">
        <input v-model="showPrevious" type="checkbox" />
        {{ $t('stats.show_previous_year') }}
      </label>
      <div v-for="s in series" :key="s.meter_type" class="mb-6" :data-testid="`series-${s.meter_type}`">
        <h3 v-if="series.length > 1" class="font-medium mb-1">{{ $t(`meter_types.${s.meter_type}`) }} ({{ s.unit }})</h3>
        <apexchart type="bar" height="220" :options="chartOptions(s.unit)" :series="chartSeries(s)" />
        <table class="w-full text-sm mt-2">
          <thead>
            <tr class="text-gray-500 dark:text-gray-400 border-b dark:border-gray-700">
              <th class="text-left py-1">{{ $t('stats.month') }}</th>
              <th class="text-right py-1">{{ $t('stats.consumption') }}</th>
              <th class="text-right py-1">{{ $t('stats.previous_year') }}</th>
              <th class="text-right py-1">{{ $t('stats.delta') }}</th>
              <th v-if="hasCost(s)" class="text-right py-1">{{ $t('stats.cost') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in [...s.months].reverse()" :key="row.month" class="border-b dark:border-gray-700/50" :data-testid="`row-${row.month}`">
              <td class="py-1">{{ monthLabel(row.month) }}</td>
              <td class="py-1 text-right font-mono">
                <span v-if="row.value == null" class="text-gray-400">{{ $t('stats.no_data') }}</span>
                <template v-else>
                  {{ formatAmount(row.value) }} {{ s.unit }}
                  <span v-if="row.state === 'partial'" class="text-xs text-gray-400" :title="$t('stats.partial')">*</span>
                  <span v-if="row.estimated" class="text-xs text-gray-400" :title="$t('stats.estimated')">~</span>
                </template>
              </td>
              <td class="py-1 text-right font-mono text-gray-500">
                {{ row.previous_year?.value != null ? formatAmount(row.previous_year.value) : '–' }}
              </td>
              <td class="py-1 text-right" :class="deltaColor(row.delta_pct)">{{ row.delta_pct != null ? formatTrend(row.delta_pct) : '–' }}</td>
              <td v-if="hasCost(s)" class="py-1 text-right font-mono text-gray-500">{{ formatCost(row.cost) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@/stores/auth'
import { apiFetch } from '@/utils/api'
import { chartValue, formatAmount, formatCost, formatTrend, monthLabel, trendClass } from '@/utils/consumptionStats'

const props = defineProps<{ meterId?: string; propertyId?: string; months?: number }>()

const { t } = useI18n()
const authStore = useAuthStore()
const loading = ref(true)
const error = ref(false)
const series = ref<any[]>([])
const showPrevious = ref(true)

const query = computed(() => {
  const q = new URLSearchParams()
  if (props.meterId) q.set('meter_id', props.meterId)
  else if (props.propertyId) q.set('property_id', props.propertyId)
  q.set('months', String(props.months ?? 12))
  return q.toString()
})

async function load() {
  loading.value = true
  error.value = false
  try {
    const res = await apiFetch(`/api/dashboard/monthly?${query.value}`, {
      headers: { Authorization: `Bearer ${authStore.token}` },
    })
    if (res.ok) series.value = (await res.json()).series ?? []
    else error.value = true
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}

/** The cost column only appears when at least one month has a known price. */
function hasCost(s: any): boolean {
  return s.months.some((m: any) => m.cost != null)
}

function deltaColor(pct: number | null) {
  const c = trendClass(pct)
  return c === 'increase' ? 'text-red-600 dark:text-red-400' : c === 'decrease' ? 'text-green-600 dark:text-green-400' : ''
}

function chartSeries(s: any) {
  const current = { name: t('stats.consumption'), data: s.months.map((m: any) => ({ x: monthLabel(m.month), y: chartValue(m.value) })) }
  if (!showPrevious.value) return [current]
  const previous = {
    name: t('stats.previous_year'),
    data: s.months.map((m: any) => ({ x: monthLabel(m.month), y: chartValue(m.previous_year?.value ?? null) })),
  }
  return [previous, current]
}

function chartOptions(unit: string) {
  return {
    chart: { toolbar: { show: false }, background: 'transparent', animations: { enabled: false } },
    theme: { mode: document.documentElement.classList.contains('dark') ? 'dark' : 'light' },
    dataLabels: { enabled: false },
    yaxis: { labels: { formatter: (v: number) => `${formatAmount(v)} ${unit}` } },
    colors: ['#9ca3af', '#3b82f6'],
    legend: { show: showPrevious.value },
  }
}

onMounted(load)
watch(query, load)
</script>
