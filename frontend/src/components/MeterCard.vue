<template>
  <div class="bg-white dark:bg-gray-800 rounded-xl shadow p-4">
    <div class="flex items-center justify-between mb-2">
      <span class="font-semibold">{{ meter.meter_name }}</span>
      <MeterTypeIcon :type="meter.meter_type" />
    </div>
    <div class="text-2xl font-bold font-mono">
      {{ meter.latest_value ?? '–' }}
      <span class="text-sm font-normal text-gray-500 dark:text-gray-400 ml-1">{{ meter.unit }}</span>
    </div>
    <div v-if="meter.latest_read_at" class="text-xs text-gray-400 mt-1">
      {{ formatDate(meter.latest_read_at) }}
    </div>
  </div>
</template>

<script setup lang="ts">
import MeterTypeIcon from './MeterTypeIcon.vue'

defineProps<{ meter: any }>()

function formatDate(iso: string) {
  return new Date(iso).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
}
</script>
