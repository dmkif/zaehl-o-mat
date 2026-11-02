<template>
  <div v-if="meter.avg_daily_consumption_30d != null || meter.daily_consumption_last_interval != null" data-testid="trend-line">
    <div v-if="meter.unusually_high" class="inline-block text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-200 mt-1" data-testid="unusually-high">
      ⚠ {{ $t('stats.unusually_high') }}
    </div>
    <div v-if="meter.avg_daily_consumption_30d != null" class="text-sm text-gray-500 dark:text-gray-400">
      {{ $t('stats.last30') }}: {{ formatAmount(meter.avg_daily_consumption_30d) }} {{ meter.unit }}{{ $t('stats.per_day') }}
      <span v-if="meter.trend_30d_vs_365d_pct != null" :class="trendColor" data-testid="trend-pct">
        ({{ formatTrend(meter.trend_30d_vs_365d_pct) }} {{ $t('stats.vs_average') }})
      </span>
    </div>
    <div v-if="meter.daily_consumption_last_interval != null" class="text-sm text-gray-500 dark:text-gray-400">
      {{ $t('stats.since_last_reading') }}: {{ formatAmount(meter.daily_consumption_last_interval) }} {{ meter.unit }}{{ $t('stats.per_day') }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { formatAmount, formatTrend, trendClass } from '@/utils/consumptionStats'

const props = defineProps<{ meter: any }>()

const trendColor = computed(() => {
  switch (trendClass(props.meter.trend_30d_vs_365d_pct)) {
    case 'increase': return 'text-red-600 dark:text-red-400'
    case 'decrease': return 'text-green-600 dark:text-green-400'
    default: return ''
  }
})
</script>
