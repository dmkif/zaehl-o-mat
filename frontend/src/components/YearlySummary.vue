<template>
  <div data-testid="yearly">
    <div v-if="loading" class="text-sm text-gray-500">{{ $t('common.loading') }}</div>
    <div v-else-if="error" class="text-sm text-red-600 dark:text-red-400" role="alert">{{ $t('stats.load_error') }}</div>
    <table v-else-if="series.length" class="w-full text-sm">
      <thead>
        <tr class="text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700">
          <th class="pb-1 pr-3">{{ $t('stats.type') }}</th>
          <th class="pb-1 pr-3 text-right">{{ $t('stats.year_to_date') }}</th>
          <th class="pb-1 pr-3 text-right">{{ $t('stats.previous_period') }}</th>
          <th class="pb-1 pr-3 text-right">{{ $t('stats.delta') }}</th>
          <th class="pb-1 text-right">{{ $t('stats.cost') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="s in series" :key="s.meter_type" class="border-b border-gray-100 dark:border-gray-700" :data-testid="`year-${s.meter_type}`">
          <td class="py-1 pr-3">{{ $t(`meter_types.${s.meter_type}`) }}</td>
          <td class="py-1 pr-3 text-right font-mono">{{ amount(s.year_to_date, s.unit) }}</td>
          <td class="py-1 pr-3 text-right font-mono text-gray-500">{{ amount(s.previous_year_same_period, s.unit) }}</td>
          <td class="py-1 pr-3 text-right" :class="deltaColor(s.delta_pct)">{{ s.delta_pct != null ? formatTrend(s.delta_pct) : '–' }}</td>
          <td class="py-1 text-right font-mono">
            {{ formatCost(s.cost) }}
            <span v-if="s.cost != null && !s.cost_complete" class="text-xs text-gray-400" :title="$t('stats.cost_partial')">*</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { apiFetch } from '@/utils/api'
import { formatAmount, formatCost, formatTrend, trendClass } from '@/utils/consumptionStats'

const props = defineProps<{ meterId?: string; propertyId?: string }>()
const authStore = useAuthStore()
const loading = ref(true)
const error = ref(false)
const series = ref<any[]>([])

const query = computed(() => {
  const q = new URLSearchParams()
  if (props.meterId) q.set('meter_id', props.meterId)
  else if (props.propertyId) q.set('property_id', props.propertyId)
  return q.toString()
})

async function load() {
  loading.value = true
  error.value = false
  try {
    const res = await apiFetch(`/api/dashboard/yearly?${query.value}`, {
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

/** Missing consumption is a dash, never 0. */
function amount(v: number | null, unit: string): string {
  return v == null ? '–' : `${formatAmount(v)} ${unit}`
}

function deltaColor(pct: number | null) {
  const c = trendClass(pct)
  return c === 'increase' ? 'text-red-600 dark:text-red-400' : c === 'decrease' ? 'text-green-600 dark:text-green-400' : ''
}

onMounted(load)
watch(query, load)
</script>
