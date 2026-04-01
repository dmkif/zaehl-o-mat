<template>
  <span :title="type">{{ icon }}</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ type: string }>()

const icon = computed(() => {
  const map: Record<string, string> = {
    water: '💧',
    electricity: '⚡',
    heating_oil: '🛢️',
    gas: '🔥',
    heat: '♨️',
  }
  return map[props.type] ?? '📊'
})
</script>
