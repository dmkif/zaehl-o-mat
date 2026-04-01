<template>
  <div class="flex flex-col gap-3">
    <!-- Camera capture -->
    <div class="flex gap-2">
      <label class="flex-1 cursor-pointer px-4 py-2 border-2 border-dashed dark:border-gray-600 rounded-xl text-center text-sm text-gray-500 hover:border-brand-400 hover:text-brand-600 transition">
        📷 {{ $t('ocr.capture') }}
        <input type="file" accept="image/*" capture="environment" class="hidden" @change="onFileSelected" />
      </label>
      <label class="flex-1 cursor-pointer px-4 py-2 border-2 border-dashed dark:border-gray-600 rounded-xl text-center text-sm text-gray-500 hover:border-brand-400 hover:text-brand-600 transition">
        🖼️ {{ $t('ocr.upload') }}
        <input type="file" accept="image/*" class="hidden" @change="onFileSelected" />
      </label>
    </div>

    <!-- Preview + OCR result -->
    <div v-if="previewUrl" class="flex gap-3 items-start">
      <img :src="previewUrl" alt="preview" class="w-24 h-24 object-cover rounded-xl border dark:border-gray-700" />
      <div class="flex-1 flex flex-col gap-2">
        <div v-if="scanning" class="text-sm text-gray-500 animate-pulse">{{ $t('ocr.scanning') }}</div>
        <template v-else>
          <label class="text-xs text-gray-500">{{ $t('ocr.detected_value') }}</label>
          <input
            v-model="confirmedValue"
            type="text"
            inputmode="decimal"
            class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2 font-mono text-lg"
          />
        </template>
      </div>
    </div>

    <!-- Manual value (no image) -->
    <div v-else class="flex flex-col gap-2">
      <label class="text-xs text-gray-500">{{ $t('reading.value') }}</label>
      <input
        v-model="confirmedValue"
        type="text"
        inputmode="decimal"
        :placeholder="$t('reading.enter_value')"
        class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2 font-mono text-lg"
      />
    </div>

    <div class="flex flex-col gap-1">
      <label class="text-xs text-gray-500">{{ $t('reading.note') }}</label>
      <input v-model="note" type="text" class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2 text-sm" />
    </div>

    <button
      @click="saveReading"
      :disabled="!confirmedValue || saving"
      class="px-4 py-2 bg-brand-600 hover:bg-brand-700 disabled:opacity-40 text-white rounded-xl font-semibold transition"
    >
      {{ saving ? $t('common.saving') : $t('reading.save') }}
    </button>

    <p v-if="errorMsg" class="text-red-500 text-xs">{{ errorMsg }}</p>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{ meterId: string; propertyId: string }>()
const emit = defineEmits<{ (e: 'reading-added'): void }>()

const authStore = useAuthStore()
const previewUrl = ref<string | null>(null)
const imagePath = ref<string | null>(null)
const confirmedValue = ref('')
const note = ref('')
const scanning = ref(false)
const saving = ref(false)
const errorMsg = ref('')

async function onFileSelected(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  previewUrl.value = URL.createObjectURL(file)
  imagePath.value = null
  confirmedValue.value = ''
  scanning.value = true
  errorMsg.value = ''

  const form = new FormData()
  form.append('file', file)
  form.append('meter_id', props.meterId)

  try {
    const res = await fetch('/api/ocr/scan', {
      method: 'POST',
      headers: { Authorization: `Bearer ${authStore.token}` },
      body: form,
    })
    if (res.ok) {
      const data = await res.json()
      confirmedValue.value = data.detected_value ?? ''
      imagePath.value = data.image_path ?? null
    } else {
      errorMsg.value = 'OCR fehlgeschlagen'
    }
  } catch {
    errorMsg.value = 'Verbindungsfehler'
  } finally {
    scanning.value = false
  }
}

async function saveReading() {
  if (!confirmedValue.value) return
  saving.value = true
  errorMsg.value = ''
  try {
    const res = await fetch(
      `/api/properties/${props.propertyId}/meters/${props.meterId}/readings`,
      {
        method: 'POST',
        headers: { Authorization: `Bearer ${authStore.token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          value: parseFloat(confirmedValue.value.replace(',', '.')),
          source: 'manual',
          image_path: imagePath.value,
          note: note.value || null,
        }),
      }
    )
    if (res.ok) {
      confirmedValue.value = ''
      note.value = ''
      previewUrl.value = null
      imagePath.value = null
      emit('reading-added')
    } else {
      errorMsg.value = 'Speichern fehlgeschlagen'
    }
  } catch {
    errorMsg.value = 'Verbindungsfehler'
  } finally {
    saving.value = false
  }
}
</script>
