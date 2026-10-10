// @ts-check
/** One owner composes deficit planning, explicitly authorized gathering, return and verified production. */
import { randomUUID } from 'node:crypto'
import { setImmediate as yieldFrame } from 'node:timers/promises'
import inventorySafety from 'mineflayer/lib/anelf_inventory.js'
import { ActionController } from '../bot/anelf-actions.mjs'
import { ToolError } from '../util/errors.js'
import { planProduction, itemCount } from './anelf-production-plan.mjs'
import { findTable, inventoryClean, validateProductionSpace, ProductionTask } from './anelf-production-task.mjs'
import { GatherTask } from './anelf-gather-task.mjs'
import { planPreparation } from './anelf-preparation-plan.mjs'

/** @typedef {import('./anelf-preparation-plan.mjs').Order} Order */
/** @typedef {import('mineflayer').Bot & {tool:import('mineflayer-tool').Tool}} Bot */
/** @typedef {'running'|'completed'|'blocked'|'cancelled'|'interrupted'} Phase */

export class PreparationTask {
  /** @param {import('../context.js').ToolContext} ctx @param {Bot} bot @param {Order} order */
  constructor (ctx, bot, order) {
    if (!(ctx.locks instanceof ActionController)) throw new ToolError('INTERNAL', 'Preparation requires the installed action controller.')
    this.ctx = ctx; this.locks = ctx.locks; this.bot = bot; this.order = order
    this.id = randomUUID(); this.actionId = ''; this.origin = this.locks.requests.current() ?? null
    this.world = JSON.stringify([ctx.manager.statusReport().host, ctx.manager.statusReport().port, bot.username, bot.game.dimension])
    this.dimension = bot.game.dimension; this.entry = bot.entity.position.floored()
    this.before = itemCount(bot, order.item); this.available = this.before
    this.required = order.mode === 'craft' ? this.before + order.count : order.count
    this.stage = 'planning'; this.reason = ''; this.needed = 0; this.clean = true
    /** @type {Phase} */ this.phase = 'running'
    /** @type {GatherTask|null} */ this.gathering = null
    /** @type {ProductionTask|null} */ this.production = null
    this.startedAt = Date.now()
    /** @type {number|null} */ this.finishedAt = null
    this.done = Promise.resolve()
  }

  snapshot () {
    const production = this.production?.snapshot(), gathering = this.gathering?.snapshot()
    const stage = production?.active ? `production:${production.stage}` : gathering?.active ? `gathering:${gathering.stage}` : this.stage
    return { id: this.id, actionId: this.actionId, origin: this.origin, world: this.world,
      phase: this.phase, stage, reason: this.reason, active: this.finishedAt === null,
      item: this.order.item, mode: this.order.mode, requested: this.order.count, before: this.before,
      required: this.required, created: production?.created ?? 0, available: production?.available ?? this.available,
      reused: production?.reused ?? (this.order.mode === 'ensure' ? Math.min(this.before, this.order.count) : 0),
      stepsDone: production?.stepsDone ?? 0, stepsTotal: production?.stepsTotal ?? 0,
      table: production?.table ?? null, inventoryClean: this.finishedAt === null ? inventoryClean(this.bot) : this.clean,
      plannedGather: this.needed, gathering: gathering ?? null,
      startedAt: this.startedAt, finishedAt: this.finishedAt }
  }

  start () {
    const handle = this.locks.begin('prepare_item')
    this.actionId = handle.actionId; this.locks.linkTask(this.id, this.world)
    this.done = this.execute(handle)
    return this.snapshot()
  }

  /** @param {AbortSignal} signal */
  check (signal) {
    signal.throwIfAborted()
    if (this.ctx.manager.botOrNull() !== this.bot || this.bot.game.dimension !== this.dimension || this.bot.health <= 0) {
      throw new ToolError('CANCELLED', 'Original living connection or world changed; no dependent preparation stage will start.')
    }
    if (Date.now() - this.startedAt > 270000) throw new ToolError('TIMEOUT', 'Preparation budget exhausted; no further gathering or production will start.')
  }

  /** @param {ReturnType<ActionController['begin']>} handle */
  async execute (handle) {
    try {
      await yieldFrame(); this.check(handle.signal)
      const preview = await planPreparation(this.bot, this.order, Boolean(findTable(this.bot)), () => this.check(handle.signal))
      this.check(handle.signal)
      validateProductionSpace(this.bot, preview.plan)
      this.needed = preview.amount
      if (this.needed) {
        this.stage = 'gathering'
        this.gathering = new GatherTask(this.ctx, this.bot, { ...this.order.gather, count: this.needed, withdraw: [], deposit: false })
        this.gathering.start(true); await this.gathering.done
        this.check(handle.signal)
        const gathered = this.gathering.snapshot()
        if (gathered.phase !== 'completed' || gathered.gained < this.needed || !gathered.returned || !gathered.inventoryClean) {
          throw new ToolError('MISSING_MATERIALS', `Gathering or return was not confirmed; no production started. ${gathered.reason} ${gathered.returnReason}`)
        }
      }
      this.check(handle.signal)
      if (!this.bot.entity.position.floored().equals(this.entry)) throw new ToolError('FORBIDDEN', 'Not at the original preparation position; no craft was started.')
      this.stage = 'replanning'
      const table = findTable(this.bot)
      const plan = planProduction(this.bot, this.order.item, this.order.count, this.order.mode, Boolean(table))
      validateProductionSpace(this.bot, plan)
      this.check(handle.signal)
      this.stage = 'production'
      this.production = new ProductionTask(this.ctx, this.bot, plan, table)
      this.production.start(true); await this.production.done
      this.check(handle.signal)
      const made = this.production.snapshot()
      if (made.phase !== 'completed' || !made.inventoryClean || made.available < this.required ||
          (this.order.mode === 'craft' && made.created < this.order.count)) {
        throw new ToolError('INTERNAL', `Production result was not confirmed. ${made.reason}`)
      }
      this.phase = 'completed'; this.stage = 'done'
    } catch (error) {
      this.phase = this.locks.action?.outcome === 'interrupted' ? 'interrupted' : handle.signal.aborted ? 'cancelled' : 'blocked'
      this.reason = error instanceof Error ? error.message : String(error)
    } finally {
      await inventorySafety.waitForCraft(this.bot)
      this.available = itemCount(this.bot, this.order.item)
      this.clean = this.ctx.manager.botOrNull() === this.bot && this.bot.health > 0 && inventoryClean(this.bot)
      if (handle.signal.aborted) this.phase = this.locks.action?.outcome === 'interrupted' ? 'interrupted' : 'cancelled'
      if (!this.clean && this.phase === 'completed') { this.phase = 'blocked'; this.reason = 'Inventory cleanup was not confirmed.' }
      const owner = this.locks.action
      handle.release(this.phase, this.reason)
      if (owner?.id === this.actionId) await owner.done
      this.finishedAt = Date.now()
      this.ctx.events.push('production_progress', this.snapshot())
    }
  }
}
