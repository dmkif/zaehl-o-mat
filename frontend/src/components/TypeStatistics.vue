<template>
  <table class="w-full text-sm" data-testid="type-statistics">
    <thead>
      <tr class="text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700">
        <th class="pb-1 pr-3">{{ $t('stats.type') }}</th>
        <th class="pb-1 pr-3 text-right">{{ $t('stats.yearly') }}</th>
        <th class="pb-1 pr-3 text-right">{{ $t('stats.last30') }}</th>
        <th class="pb-1 text-right">{{ $t('stats.delta') }}</th>
      </tr>
    </thead>
    <tbody>
      <template v-for="(row, type) in types" :key="type">
        <tr class="border-b border-gray-100 dark:border-gray-700" :data-testid="`stat-${type}`">
          <td class="py-1 pr-3">
            {{ $t(`meter_types.${type}`) }}
            <span v-if="overall && row.properties_used" class="text-xs text-gray-400">({{ $t('stats.properties_used', { n: row.properties_used }) }})</span>
          </td>
          <td class="py-1 pr-3 text-right font-mono">{{ amount(row.yearly, row.unit) }}</td>
          <td class="py-1 pr-3 text-right font-mono">{{ amount(row.recent_30d, row.unit) }}</td>
          <td class="py-1 text-right" :class="deltaColor(row.trend_pct)">{{ row.trend_pct != null ? formatTrend(row.trend_pct) : '–' }}</td>
        </tr>
        <tr v-if="row.mean_per_property" class="text-gray-500 dark:text-gray-400" :data-testid="`mean-${type}`">
          <td class="pb-1 pr-3 pl-3 text-xs">{{ $t('stats.mean_per_property') }}</td>
          <td class="pb-1 pr-3 text-right font-mono text-xs">{{ amount(row.mean_per_property.yearly, row.unit) }}</td>
          <td class="pb-1 pr-3 text-right font-mono text-xs">{{ amount(row.mean_per_property.recent_30d, row.unit) }}</td>
          <td />
        </tr>
      </template>
    </tbody>
  </table>
</template>

<script setup lang="ts">
import { formatAmount, formatTrend, trendClass } from '@/utils/consumptionStats'

defineProps<{ types: Record<string, any>; overall?: boolean }>()

/** A missing rate is shown as a dash, never as 0. */
function amount(v: number | null | undefined, unit: string): string {
  return v == null ? '–' : `${formatAmount(v)} ${unit}/Tag`
}

function deltaColor(pct: number | null) {
  const c = trendClass(pct)
  return c === 'increase' ? 'text-red-600 dark:text-red-400' : c === 'decrease' ? 'text-green-600 dark:text-green-400' : ''
}
</script>
