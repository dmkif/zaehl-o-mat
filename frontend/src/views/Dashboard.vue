<template>
  <div>
    <h1 class="text-2xl font-bold mb-6">{{ $t('dashboard.title') }}</h1>

    <div v-if="loading" class="text-gray-500 dark:text-gray-400">{{ $t('common.loading') }}</div>

    <template v-else>
      <!-- Stats row -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <StatCard :label="$t('dashboard.properties')" :value="summary?.property_count" />
        <StatCard :label="$t('dashboard.meters')" :value="summary?.active_meter_count" />
        <StatCard
          :label="$t('dashboard.oil_price')"
          :value="summary?.oil_market_price?.price_per_100l ? `${summary.oil_market_price.price_per_100l} € / 100L` : '–'"
          :sub="summary?.oil_market_price?.date ? new Date(summary.oil_market_price.date + 'T00:00:00').toLocaleDateString('de-DE') : undefined"
        >
          <template #suffix>
            <span
              v-if="summary?.oil_market_price?.buy_signal"
              :title="buySignalTooltip"
              class="ml-2 text-xl cursor-help"
            >{{ buySignalEmoji }}</span>
          </template>
        </StatCard>
      </div>

      <!-- Meter cards -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <MeterCard
          v-for="m in summary?.meters"
          :key="m.meter_id"
          :meter="m"
        >
          <template #extra>
            <div v-if="m.avg_daily_consumption_365d != null" class="text-sm text-gray-500 dark:text-gray-400 mt-1">
              ∅ {{ formatConsumption(m.avg_daily_consumption_365d) }} {{ m.unit }}/Tag
            </div>
          </template>
        </MeterCard>
      </div>

      <!-- Heizöltank section per property -->
      <template v-for="(days, propId) in summary?.oil_reichweite" :key="propId">
        <div v-if="days != null" class="mt-6 p-4 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
          <h2 class="text-lg font-semibold mb-3">⛽ Heizöltank</h2>
          <p class="text-sm text-gray-600 dark:text-gray-300">
            Reichweite: <span class="font-semibold">{{ days }} Tage</span>
          </p>
          <div class="mt-2 w-full bg-gray-200 dark:bg-gray-700 rounded-full h-3">
            <div
              class="h-3 rounded-full transition-all"
              :class="days > 60 ? 'bg-green-500' : days > 20 ? 'bg-yellow-500' : 'bg-red-500'"
              :style="{ width: `${Math.min(100, (days / 365) * 100).toFixed(1)}%` }"
            />
          </div>
        </div>
      </template>

      <!-- Verbrauch nach Zählerart section per property -->
      <template v-for="(agg, propId) in typeAggregates" :key="propId">
        <div v-if="agg?.aggregates?.length" class="mt-6 p-4 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
          <h2 class="text-lg font-semibold mb-3">Verbrauch nach Zählerart (letzte 365 Tage)</h2>
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700">
                <th class="pb-1 pr-4">Zählertyp</th>
                <th class="pb-1 pr-4">Verbrauch</th>
                <th class="pb-1">Einheit</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in agg.aggregates" :key="row.meter_type" class="border-b border-gray-100 dark:border-gray-700 last:border-0">
                <td class="py-1 pr-4">{{ $t(`meter_types.${row.meter_type}`) || row.meter_type }}</td>
                <td class="py-1 pr-4 font-mono">{{ formatConsumption(row.total_consumption) }}</td>
                <td class="py-1">{{ row.unit }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <!-- Heizöl Preisverlauf + Prognose -->
      <div
        v-if="oilPriceHistory && oilPriceHistory.history.length >= 2"
        class="mt-6 p-4 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800"
      >
        <h2 class="text-lg font-semibold mb-3">📈 Heizöl Preisverlauf &amp; Prognose (90 Tage)</h2>
        <VueApexCharts
          type="line"
          height="280"
          :options="oilChartOptions"
          :series="oilChartSeries"
        />
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { apiFetch } from '@/utils/api'
import StatCard from '@/components/StatCard.vue'
import MeterCard from '@/components/MeterCard.vue'
import VueApexCharts from 'vue3-apexcharts'

const authStore = useAuthStore()
const loading = ref(true)
const summary = ref<any>(null)
const typeAggregates = ref<Record<string, any>>({})
const oilPriceHistory = ref<{ history: Array<{date: string; price: number}>; forecast: Array<{date: string; price: number}> } | null>(null)

// ── Buy signal ────────────────────────────────────────────────────────────────

const buySignalEmoji = computed(() => {
  const signal = summary.value?.oil_market_price?.buy_signal?.signal
  if (signal === 'buy') return '🟢'
  if (signal === 'avoid') return '🔴'
  if (signal === 'wait') return '🟡'
  return '⚫'
})

const buySignalEmoji_label: Record<string, string> = {
  buy: 'Kaufen',
  avoid: 'Zu teuer',
  wait: 'Abwarten',
  insufficient_data: 'Keine Daten',
}

const buySignalTooltip = computed(() => {
  const bs = summary.value?.oil_market_price?.buy_signal
  if (!bs) return ''
  const label = buySignalEmoji_label[bs.signal] ?? bs.signal
  const parts = [label]
  if (bs.pct_vs_avg_90d != null) parts.push(`∅90T: ${bs.pct_vs_avg_90d > 0 ? '+' : ''}${bs.pct_vs_avg_90d}%`)
  if (bs.pct_vs_seasonal != null) parts.push(`Saison: ${bs.pct_vs_seasonal > 0 ? '+' : ''}${bs.pct_vs_seasonal}%`)
  parts.push(`Konfidenz: ${bs.confidence}`)
  return parts.join(' | ')
})

// ── Oil price chart ───────────────────────────────────────────────────────────

const oilChartSeries = computed(() => [
  {
    name: 'Marktpreis',
    data: (oilPriceHistory.value?.history ?? []).map(h => ({
      x: new Date(h.date + 'T00:00:00').getTime(),
      y: h.price,
    })),
  },
  {
    name: 'Prognose',
    data: (oilPriceHistory.value?.forecast ?? []).map(f => ({
      x: new Date(f.date + 'T00:00:00').getTime(),
      y: f.price,
    })),
  },
])

const oilChartOptions = computed(() => ({
  chart: { id: 'oil-price', toolbar: { show: false }, animations: { enabled: false } },
  xaxis: { type: 'datetime' as const, labels: { datetimeFormatter: { day: 'dd.MM.' } } },
  yaxis: { labels: { formatter: (v: number) => `${v.toFixed(0)} €` }, title: { text: '€ / 100L' } },
  stroke: { width: [2, 2], dashArray: [0, 6] },
  colors: ['#3b82f6', '#f59e0b'],
  tooltip: { x: { format: 'dd.MM.yyyy' } },
  legend: { show: true },
  grid: { borderColor: '#e5e7eb' },
}))

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatConsumption(val: number): string {
  if (val >= 1000) return val.toLocaleString('de-DE', { maximumFractionDigits: 0 })
  if (val >= 10) return val.toLocaleString('de-DE', { maximumFractionDigits: 1 })
  return val.toLocaleString('de-DE', { maximumFractionDigits: 3 })
}

// ── Data loading ──────────────────────────────────────────────────────────────

async function fetchTypeAggregates(propId: string) {
  const res = await apiFetch(`/api/dashboard/property/${propId}/type-aggregates`, {
    headers: { Authorization: `Bearer ${authStore.token}` },
  })
  if (res.ok) {
    typeAggregates.value[propId] = await res.json()
  }
}

onMounted(async () => {
  try {
    const res = await apiFetch('/api/dashboard/summary', {
      headers: { Authorization: `Bearer ${authStore.token}` },
    })
    if (res.ok) {
      summary.value = await res.json()
      // Fetch type aggregates for each unique property
      const propIds = [...new Set(summary.value?.meters?.map((m: any) => m.property_id) ?? [])] as string[]
      await Promise.all(propIds.map(fetchTypeAggregates))
    }
    const histRes = await apiFetch('/api/dashboard/oil-price-history?days=90', {
      headers: { Authorization: `Bearer ${authStore.token}` },
    })
    if (histRes.ok) {
      oilPriceHistory.value = await histRes.json()
    }
  } finally {
    loading.value = false
  }
})
</script>
