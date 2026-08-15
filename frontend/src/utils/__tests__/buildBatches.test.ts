import { describe, it, expect } from 'vitest'
import { buildBatches, BATCH_MAX_BYTES, BATCH_MAX_FILES } from '../buildBatches'

/** Create a minimal File stub with a given size in bytes. */
function makeFile(name: string, sizeBytes: number): File {
  const buf = new Uint8Array(sizeBytes)
  return new File([buf], name, { type: 'image/jpeg' })
}

const MB = 1024 * 1024

describe('buildBatches', () => {
  it('returns an empty array for an empty input', () => {
    expect(buildBatches([])).toEqual([])
  })

  it('puts a single file in a single batch', () => {
    const f = makeFile('a.jpg', 1 * MB)
    const batches = buildBatches([f])
    expect(batches).toHaveLength(1)
    expect(batches[0]).toEqual([f])
  })

  it('keeps multiple small files in one batch if they fit', () => {
    // 3 files × 1 MB = 3 MB — well under 10 MB and under 5-file cap
    const files = [
      makeFile('a.jpg', 1 * MB),
      makeFile('b.jpg', 1 * MB),
      makeFile('c.jpg', 1 * MB),
    ]
    const batches = buildBatches(files)
    expect(batches).toHaveLength(1)
    expect(batches[0]).toHaveLength(3)
  })

  it('splits files when cumulative size exceeds maxBytes', () => {
    // 6 MB + 6 MB = 12 MB > 10 MB limit → must split into 2 batches
    const f1 = makeFile('big1.jpg', 6 * MB)
    const f2 = makeFile('big2.jpg', 6 * MB)
    const batches = buildBatches([f1, f2])
    expect(batches).toHaveLength(2)
    expect(batches[0]).toEqual([f1])
    expect(batches[1]).toEqual([f2])
  })

  it('splits files when count reaches maxFiles', () => {
    // 6 tiny files, each 100 KB — size is fine but count cap (5) triggers a split
    const files = Array.from({ length: 6 }, (_, i) => makeFile(`f${i}.jpg`, 100 * 1024))
    const batches = buildBatches(files)
    expect(batches).toHaveLength(2)
    expect(batches[0]).toHaveLength(5)
    expect(batches[1]).toHaveLength(1)
  })

  it('places a single oversized file in its own batch (never dropped)', () => {
    // 12 MB exceeds the 10 MB limit, but it must still be included
    const big = makeFile('huge.jpg', 12 * MB)
    const small = makeFile('small.jpg', 1 * MB)
    const batches = buildBatches([big, small])
    expect(batches).toHaveLength(2)
    expect(batches[0]).toEqual([big])
    expect(batches[1]).toEqual([small])
  })

  it('respects custom maxBytes limit', () => {
    const f1 = makeFile('a.jpg', 3 * MB)
    const f2 = makeFile('b.jpg', 3 * MB)
    // custom limit: 4 MB → the second file pushes over it
    const batches = buildBatches([f1, f2], 4 * MB, 99)
    expect(batches).toHaveLength(2)
  })

  it('respects custom maxFiles limit', () => {
    const files = Array.from({ length: 4 }, (_, i) => makeFile(`f${i}.jpg`, 100))
    // custom limit: 2 files per batch
    const batches = buildBatches(files, BATCH_MAX_BYTES, 2)
    expect(batches).toHaveLength(2)
    expect(batches[0]).toHaveLength(2)
    expect(batches[1]).toHaveLength(2)
  })

  it('every batch stays within the default size and file limits', () => {
    // 20 random-ish files, mix of sizes
    const sizes = [8, 3, 5, 1, 9, 2, 6, 4, 7, 1, 8, 3, 5, 2, 9, 1, 4, 6, 3, 2]
    const files = sizes.map((s, i) => makeFile(`f${i}.jpg`, s * MB))
    const batches = buildBatches(files)

    for (const batch of batches) {
      const totalSize = batch.reduce((sum, f) => sum + f.size, 0)
      // A single oversized file may exceed the limit on its own — that is intentional.
      // For multi-file batches both constraints must hold.
      if (batch.length > 1) {
        expect(totalSize).toBeLessThanOrEqual(BATCH_MAX_BYTES)
        expect(batch.length).toBeLessThanOrEqual(BATCH_MAX_FILES)
      }
    }

    // All original files must appear exactly once across all batches
    const flat = batches.flat()
    expect(flat).toHaveLength(files.length)
    expect(new Set(flat)).toEqual(new Set(files))
  })

  it('produces the same total file count across all batches as the input', () => {
    const files = Array.from({ length: 13 }, (_, i) => makeFile(`f${i}.jpg`, 2 * MB))
    const batches = buildBatches(files)
    expect(batches.flat()).toHaveLength(files.length)
  })
})
