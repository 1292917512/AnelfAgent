// @ts-check
/** Bounded samples for executor timing; never records tool arguments or chat text. */
import { monitorEventLoopDelay, performance } from 'node:perf_hooks'

/** @param {number[]} values */
function summary (values) {
  const sorted = [...values].sort((a, b) => a - b)
  /** @param {number} p */
  const percentile = p => sorted.length ? sorted[Math.ceil(sorted.length * p) - 1] : null
  return { samples: values.length, p50Ms: percentile(0.5), p95Ms: percentile(0.95), maxMs: sorted.at(-1) ?? null }
}

export class RuntimeMetrics {
  constructor () {
    this.startedAt = Date.now()
    /** @type {Map<string,{calls:number,errors:number,ms:number[]}>} */
    this.tools = new Map()
    /** @type {number[]} */
    this.paths = []
    this.pathCount = 0
    this.loop = monitorEventLoopDelay({ resolution: 20 })
    this.loop.enable()
  }

  /** @param {import('mineflayer').Bot} bot */
  attach (bot) {
    /** @param {import('mineflayer-pathfinder').PartiallyComputedPath} result */
    const update = result => {
      if (typeof result.time !== 'number' || !Number.isFinite(result.time)) return
      this.pathCount++
      this.paths.push(result.time)
      if (this.paths.length > 128) this.paths.shift()
    }
    bot.on('path_update', update)
    bot.once('end', () => { bot.removeListener('path_update', update) })
  }

  /** @template T @param {string} name @param {()=>Promise<T>} work @returns {Promise<T>} */
  async measure (name, work) {
    const row = this.tools.get(name) ?? { calls: 0, errors: 0, ms: [] }
    this.tools.set(name, row)
    const start = performance.now()
    row.calls++
    try { return await work() } catch (error) { row.errors++; throw error } finally {
      row.ms.push(Math.round((performance.now() - start) * 100) / 100)
      if (row.ms.length > 128) row.ms.shift()
    }
  }

  snapshot () {
    /** @param {number} value */
    const ms = value => Number.isFinite(value) ? Math.round(value / 1e4) / 100 : null
    return { startedAt: this.startedAt, window: 'last 128 completed samples per tool/path; event loop since startup',
      eventLoop: { meanMs: ms(this.loop.mean), p95Ms: ms(this.loop.percentile(95)), maxMs: ms(this.loop.max) },
      pathfinder: { updates: this.pathCount, ...summary(this.paths) },
      tools: Object.fromEntries([...this.tools].map(([name, row]) => [name, { calls: row.calls, errors: row.errors, ...summary(row.ms) }])) }
  }

  close () { this.loop.disable() }
}
