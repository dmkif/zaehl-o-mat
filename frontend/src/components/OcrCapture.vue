<template>
  <div class="flex flex-col gap-3">

    <!-- ── Stage: idle — file pickers + manual entry ── -->
    <template v-if="stage === 'idle'">
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
      <!-- Engine selector -->
      <div class="flex items-center gap-2">
        <label class="text-xs text-gray-500 whitespace-nowrap">{{ $t('ocr.engine_mode') }}</label>
        <select v-model="engine" class="flex-1 text-xs rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-2 py-1.5">
          <option value="auto">{{ $t('ocr.engine_auto') }}</option>
          <option value="ocr">{{ $t('ocr.engine_ocr') }}</option>
          <option value="llm">{{ $t('ocr.engine_llm') }}</option>
        </select>
      </div>
      <div class="flex flex-col gap-2">
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
    </template>

    <!-- ── Stage: crop — cropperjs v2 Web Components ── -->
    <template v-else-if="stage === 'crop'">
      <p class="text-xs text-gray-500">{{ $t('ocr.crop_hint') }}</p>
      <!-- cropper-canvas is the root Web Component; background adds a checkerboard -->
      <cropper-canvas
        background
        style="max-height: 320px; border-radius: 0.75rem; overflow: hidden;"
      >
        <cropper-image :src="originalUrl ?? ''" alt="" translatable></cropper-image>
        <cropper-selection
          ref="selectionEl"
          :initial-coverage="0.5"
          movable
          resizable
          keyboard
        >
          <!-- move handle covers the interior -->
          <cropper-handle action="move" theme-color="rgba(255,255,255,0.25)"></cropper-handle>
          <!-- eight edge/corner resize handles -->
          <cropper-handle action="n-resize"></cropper-handle>
          <cropper-handle action="e-resize"></cropper-handle>
          <cropper-handle action="s-resize"></cropper-handle>
          <cropper-handle action="w-resize"></cropper-handle>
          <cropper-handle action="ne-resize"></cropper-handle>
          <cropper-handle action="nw-resize"></cropper-handle>
          <cropper-handle action="se-resize"></cropper-handle>
          <cropper-handle action="sw-resize"></cropper-handle>
        </cropper-selection>
      </cropper-canvas>
      <div class="flex gap-2">
        <button @click="resetCrop" class="px-3 py-2 text-sm border rounded-xl dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition">
          {{ $t('ocr.crop_reset') }}
        </button>
        <button @click="startScan" class="flex-1 px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-xl font-semibold text-sm transition">
          {{ $t('ocr.scan') }}
        </button>
        <button @click="cancelCrop" class="px-3 py-2 text-sm border rounded-xl dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition">
          {{ $t('common.cancel') }}
        </button>
      </div>
    </template>

    <!-- ── Stage: scanning ── -->
    <template v-else-if="stage === 'scanning'">
      <div class="py-6 text-center text-sm text-gray-500 animate-pulse">{{ $t('ocr.scanning') }}</div>
    </template>

    <!-- ── Stage: result — preview + editable value ── -->
    <template v-else-if="stage === 'result'">
      <div class="flex gap-3 items-start">
        <img :src="previewUrl ?? undefined" alt="preview" class="w-24 h-24 object-cover rounded-xl border dark:border-gray-700 cursor-pointer" :title="$t('ocr.recrop')" @click="backToCrop" />
        <div class="flex-1 flex flex-col gap-2">
          <label class="text-xs text-gray-500">{{ $t('ocr.detected_value') }}</label>
          <div class="flex items-center gap-2">
            <input
              v-model="confirmedValue"
              type="text"
              inputmode="decimal"
              class="flex-1 rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2 font-mono text-lg"
            />
            <span v-if="serialMismatch" class="text-amber-500 text-xl" :title="serialWarningTitle">⚠️</span>
          </div>
          <div v-if="detectedSerial" class="text-xs text-gray-400 mt-0.5">
            {{ $t('ocr.detected_serial_label') }} <span class="font-mono">{{ detectedSerial }}</span>
            <span v-if="serialNumber && normalizeSerial(detectedSerial) === normalizeSerial(serialNumber)" class="text-green-500 ml-1">✓</span>
          </div>
          <button @click="backToCrop" class="text-xs text-left text-brand-400 hover:text-brand-600 transition">
            ✂️ {{ $t('ocr.recrop') }}
          </button>
        </div>
      </div>
      <div class="flex flex-col gap-1">
        <label class="text-xs text-gray-500">{{ $t('reading.note') }}</label>
        <input v-model="note" type="text" class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2 text-sm" />
      </div>
      <div class="flex gap-2">
        <button
          @click="saveReading"
          :disabled="!confirmedValue || saving"
          class="flex-1 px-4 py-2 bg-brand-600 hover:bg-brand-700 disabled:opacity-40 text-white rounded-xl font-semibold transition"
        >
          {{ saving ? $t('common.saving') : $t('reading.save') }}
        </button>
        <button @click="cancelCrop" class="px-4 py-2 border rounded-xl text-sm dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition">
          {{ $t('common.cancel') }}
        </button>
      </div>
    </template>

    <p v-if="errorMsg" class="text-red-500 text-xs">{{ errorMsg }}</p>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  CropperCanvas,
  CropperImage,
  CropperSelection,
  CropperHandle,
} from 'cropperjs'
import { useAuthStore } from '@/stores/auth'
import { apiFetch } from '@/utils/api'

// Register cropperjs v2 custom elements once
CropperCanvas.$define()
CropperImage.$define()
CropperSelection.$define()
CropperHandle.$define()

const props = defineProps<{ meterId: string; propertyId: string; serialNumber?: string }>()
const emit = defineEmits<{ (e: 'reading-added'): void }>()

const { t } = useI18n()
const authStore = useAuthStore()

type Stage = 'idle' | 'crop' | 'scanning' | 'result'
const stage = ref<Stage>('idle')

// Recognised engine preference — persists as long as the component is mounted
const engine = ref<'auto' | 'ocr' | 'llm'>('auto')

// Refs to v2 custom elements
const selectionEl = ref<CropperSelection | null>(null)
const originalUrl = ref<string | null>(null)

// Result state
const previewUrl = ref<string | null>(null)
const imagePath = ref<string | null>(null)
const confirmedValue = ref('')
const detectedSerial = ref<string | null>(null)
const note = ref('')
const saving = ref(false)
const errorMsg = ref('')

function normalizeSerial(s: string) {
  return s.replace(/[\s\-]/g, '').toUpperCase()
}

const serialMismatch = computed(() => {
  if (!confirmedValue.value) return false
  const storedSerial = props.serialNumber
  if (storedSerial && normalizeSerial(confirmedValue.value) === normalizeSerial(storedSerial)) return true
  if (storedSerial && detectedSerial.value && normalizeSerial(detectedSerial.value) !== normalizeSerial(storedSerial)) return true
  return false
})

const serialWarningTitle = computed(() => {
  const storedSerial = props.serialNumber
  if (storedSerial && normalizeSerial(confirmedValue.value) === normalizeSerial(storedSerial))
    return 'Erkannter Wert entspricht der gespeicherten Seriennummer – möglicherweise falscher Wert'
  if (storedSerial && detectedSerial.value && normalizeSerial(detectedSerial.value) !== normalizeSerial(storedSerial))
    return `Erkannte Seriennr. (${detectedSerial.value}) stimmt nicht mit gespeicherter Nr. (${storedSerial}) überein`
  return ''
})

// ── File selection ────────────────────────────────────────────────────────

function onFileSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return

  if (originalUrl.value) URL.revokeObjectURL(originalUrl.value)
  originalUrl.value = URL.createObjectURL(file)
  errorMsg.value = ''
  stage.value = 'crop'
}

function resetCrop() {
  selectionEl.value?.$reset()
}

function cancelCrop() {
  if (originalUrl.value) { URL.revokeObjectURL(originalUrl.value); originalUrl.value = null }
  if (previewUrl.value) { URL.revokeObjectURL(previewUrl.value); previewUrl.value = null }
  confirmedValue.value = ''
  detectedSerial.value = null
  imagePath.value = null
  errorMsg.value = ''
  stage.value = 'idle'
}

function backToCrop() {
  stage.value = 'crop'
}

// ── OCR scan ──────────────────────────────────────────────────────────────

async function startScan() {
  const sel = selectionEl.value
  if (!sel) return
  stage.value = 'scanning'
  errorMsg.value = ''

  // v2: CropperSelection.$toCanvas() renders the selected area to a canvas
  const canvas = await sel.$toCanvas({ width: 2400 })
  const blob: Blob = await new Promise((resolve, reject) =>
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error('toBlob failed'))),
      'image/jpeg',
      0.92,
    ),
  )

  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = URL.createObjectURL(blob)

  const form = new FormData()
  form.append('file', blob, 'crop.jpg')
  form.append('meter_id', props.meterId)
  form.append('engine', engine.value)
  form.append('already_cropped', 'true')

  try {
    const res = await apiFetch('/api/ocr/scan', {
      method: 'POST',
      headers: { Authorization: `Bearer ${authStore.token}` },
      body: form,
    })
    if (res.ok) {
      const data = await res.json()
      confirmedValue.value = data.detected_value ?? ''
      imagePath.value = data.image_path ?? null
      detectedSerial.value = data.detected_serial ?? null
    } else {
      errorMsg.value = t('ocr.error_failed')
    }
  } catch {
    errorMsg.value = t('ocr.error_network')
  }

  stage.value = 'result'
}

// ── Save reading ──────────────────────────────────────────────────────────

async function saveReading() {
  if (!confirmedValue.value) return
  saving.value = true
  errorMsg.value = ''
  try {
    const res = await apiFetch(
      `/api/properties/${props.propertyId}/meters/${props.meterId}/readings`,
      {
        method: 'POST',
        headers: { Authorization: `Bearer ${authStore.token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          value: parseFloat(confirmedValue.value.replace(',', '.')),
          source: imagePath.value ? 'ocr' : 'manual',
          image_path: imagePath.value,
          note: note.value || null,
        }),
      },
    )
    if (res.ok) {
      confirmedValue.value = ''
      note.value = ''
      if (previewUrl.value) { URL.revokeObjectURL(previewUrl.value); previewUrl.value = null }
      if (originalUrl.value) { URL.revokeObjectURL(originalUrl.value); originalUrl.value = null }
      stage.value = 'idle'
      emit('reading-added')
    } else {
      errorMsg.value = 'Speichern fehlgeschlagen'
    }
  } catch {
    errorMsg.value = t('ocr.error_network')
  } finally {
    saving.value = false
  }
}

onBeforeUnmount(() => {
  if (originalUrl.value) URL.revokeObjectURL(originalUrl.value)
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
})
</script>
