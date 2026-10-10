// @ts-check
/** Execute one preflighted recipe plan while retaining action ownership through inventory cleanup. */
import { randomUUID } from 'node:crypto'
import { setImmediate as yieldFrame } from 'node:timers/promises'
import { Vec3 } from 'vec3'
import inventorySafety from 'mineflayer/lib/anelf_inventory.js'
import { ActionController } from '../bot/anelf-actions.mjs'
import { ToolError } from '../util/errors.js'
import { workbenchPlacementHints } from './anelf-placement-hints.mjs'
import { itemCount } from './anelf-production-plan.mjs'

/** @typedef {import('mineflayer').Bot} Bot */
/** @typedef {import('prismarine-block').Block} Block */
/** @typedef {import('../context.js').ToolContext} Context */
/** @typedef {ReturnType<import('./anelf-production-plan.mjs').planProduction>} Plan */
/** @typedef {'running'|'completed'|'blocked'|'cancelled'|'interrupted'} Phase */

/** @param {Bot} bot */
export function inventoryClean (bot) {
  const window = bot.currentWindow ?? bot.inventory
  const size = window.type === 'minecraft:inventory' ? 4 : window.type === 'minecraft:crafting' ? 9 : -1
  return size >= 0 && !window.selectedItem && !window.slots.slice(0, size + 1).some(Boolean)
}

/** Only already visible, directly reachable workbenches are reused; this task never excavates a route.
 * @param {Bot} bot @param {Block} table
 */
function usableTable (bot, table) {
  return table.name === 'crafting_table' && bot.entity.position.offset(0, 1.62, 0).distanceTo(table.position.offset(0.5, 0.5, 0.5)) <= 4 && bot.canSeeBlock(table)
}

/** @param {Bot} bot @returns {Block|null} */
export function findTable (bot) {
  for (const position of bot.findBlocks({ matching: bot.registry.blocksByName.crafting_table.id, maxDistance: 4, count: 32 })) {
    const table = bot.blockAt(position)
    if (table && usableTable(bot, table)) return table
  }
  return null
}

/** @param {Bot} bot @returns {Map<string,number>} */
function inventory (bot) { return new Map(bot.inventory.items().map(item => [item.name, itemCount(bot, item.name)])) }

/** Check recovery room and facility placement before any gathering or crafting.
 * @param {Bot} bot @param {Plan} plan
 */
export function validateProductionSpace (bot, plan) {
  if (!inventoryClean(bot)) throw new ToolError('BUSY', 'Close other windows and recover the cursor/crafting grid before production.')
  if (plan.steps.length && bot.inventory.emptySlotCount() < 2) throw new ToolError('INVENTORY_FULL_NO_CHEST', 'Keep at least two empty inventory slots for intermediate output and safe recovery; nothing was consumed.')
  if (plan.steps.some(step => step.kind === 'place_table') && !workbenchPlacementHints(bot).length) {
    throw new ToolError('FORBIDDEN', 'No safe workbench space within reach; nothing was consumed. Move to an open, supported position.')
  }
}

export class ProductionTask {
  /** @param {Context} ctx @param {Bot} bot @param {Plan} plan @param {Block|null} table */
  constructor (ctx, bot, plan, table) {
    if (!(ctx.locks instanceof ActionController)) throw new ToolError('INTERNAL', 'Production requires the installed action controller.')
    this.ctx = ctx
    this.locks = ctx.locks
    this.bot = bot
    this.plan = plan
    this.table = table
    this.tableReused = Boolean(table)
    this.tablePlaced = false
    this.id = randomUUID()
    this.actionId = ''
    this.origin = this.locks.requests.current() ?? null
    this.world = JSON.stringify([ctx.manager.statusReport().host, ctx.manager.statusReport().port, bot.username, bot.game.dimension])
    this.dimension = bot.game.dimension
    this.before = inventory(bot)
    this.after = this.before
    /** @type {Phase} */
    this.phase = 'running'
    this.stage = 'planned'
    this.reason = ''
    this.stepsDone = 0
    this.created = 0
    this.clean = true
    this.startedAt = Date.now()
    /** @type {number|null} */
    this.finishedAt = null
    this.done = Promise.resolve()
  }

  snapshot () {
    const names = new Set([...this.before.keys(), ...this.after.keys()])
    return { id: this.id, actionId: this.actionId, origin: this.origin, world: this.world,
      phase: this.phase, stage: this.stage, reason: this.reason, active: this.finishedAt === null,
      item: this.plan.item, mode: this.plan.mode, requested: this.plan.count, before: this.plan.before,
      required: this.plan.required, created: this.created, available: this.after.get(this.plan.item) ?? 0,
      reused: this.plan.mode === 'ensure' ? Math.min(this.plan.before, this.plan.count) : 0,
      stepsDone: this.stepsDone, stepsTotal: this.plan.steps.length, inventoryClean: this.finishedAt === null ? inventoryClean(this.bot) : this.clean,
      inventoryChanges: Array.from(names, name => ({ name, count: (this.after.get(name) ?? 0) - (this.before.get(name) ?? 0) })).filter(item => item.count !== 0),
      table: this.table ? { position: this.table.position, reused: this.tableReused, placed: this.tablePlaced } : null,
      startedAt: this.startedAt, finishedAt: this.finishedAt }
  }

  /** Nested work publishes through its parent, retaining ownership until all stages finish.
   * @param {boolean} [nested]
   */
  start (nested = false) {
    const handle = this.locks.begin('prepare_item')
    this.actionId = handle.actionId
    if (!nested) this.locks.linkTask(this.id, this.world)
    this.done = this.execute(handle, nested)
    return this.snapshot()
  }

  /** @param {ReturnType<ActionController['begin']>} handle @param {boolean} nested */
  async execute (handle, nested) {
    const budget = new AbortController()
    const timer = setTimeout(() => budget.abort(new ToolError('TIMEOUT', 'Production time budget reached; inventory cleanup is still required.')), 120000)
    const signal = AbortSignal.any([handle.signal, budget.signal])
    const check = () => {
      signal.throwIfAborted()
      if (this.ctx.manager.botOrNull() !== this.bot || this.bot.game.dimension !== this.dimension || this.bot.health <= 0) {
        throw new ToolError('CANCELLED', 'Connection, dimension or life state changed; production will not resume.')
      }
    }
    try {
      // Admission returns before any crafting starts; the child handle keeps the owner alive.
      await yieldFrame()
      check()
      inventorySafety.bindCraftAction(this.bot, signal)
      for (const step of this.plan.steps) {
        check()
        if (!inventoryClean(this.bot)) throw new ToolError('BUSY', 'Cursor or crafting grid is not empty; no dependent step started.')
        if (step.kind === 'place_table') {
          this.stage = 'placing_table'
          await this.placeTable(check)
        } else {
          this.stage = `crafting:${step.item}`
          if (step.recipe.requiresTable) {
            const actual = this.table && this.bot.blockAt(this.table.position)
            if (!actual || !usableTable(this.bot, actual)) throw new ToolError('NOT_FOUND', 'Workbench is no longer reachable; no craft was started.')
            this.table = actual
          }
          for (const input of step.recipe.delta.filter(d => d.count < 0)) {
            if (this.bot.inventory.count(input.id, input.metadata) < -input.count * step.operations) {
              throw new ToolError('MISSING_MATERIALS', 'Inventory changed after planning; no dependent recipe was retried.')
            }
          }
          const before = itemCount(this.bot, step.item)
          try {
            await this.bot.craft(step.recipe, step.operations, step.recipe.requiresTable ? this.table ?? undefined : undefined)
          } finally {
            // A cancelled batch may have already delivered some outputs before restoring its remaining inputs.
            if (step.item === this.plan.item) this.created += Math.max(0, Math.min(
              step.recipe.result.count * step.operations, itemCount(this.bot, step.item) - before))
          }
          const gained = itemCount(this.bot, step.item) - before
          if (gained < step.recipe.result.count * step.operations) throw new ToolError('INTERNAL', `Server inventory did not confirm ${step.item}; no success was reported.`)
        }
        this.stepsDone++
        this.after = inventory(this.bot)
      }
      check()
      if (itemCount(this.bot, this.plan.item) < this.plan.required) throw new ToolError('INTERNAL', 'Requested inventory amount was not confirmed.')
      this.phase = 'completed'
      this.stage = 'done'
    } catch (error) {
      this.phase = this.locks.action?.outcome === 'interrupted' ? 'interrupted' : handle.signal.aborted ? 'cancelled' : 'blocked'
      this.reason = error instanceof Error ? error.message : String(error)
    } finally {
      clearTimeout(timer)
      try {
        await inventorySafety.waitForCraft(this.bot)
        this.after = inventory(this.bot)
        this.clean = this.ctx.manager.botOrNull() === this.bot && this.bot.health > 0 && inventoryClean(this.bot)
      } catch (error) {
        this.clean = false
        this.reason += ` Cleanup observation failed: ${error instanceof Error ? error.message : String(error)}`
      }
      inventorySafety.bindCraftAction(this.bot, null)
      if (handle.signal.aborted) {
        this.phase = this.locks.action?.outcome === 'interrupted' ? 'interrupted' : 'cancelled'
        if (!this.reason) this.reason = 'Stopped before final confirmation; previous work will not resume.'
      }
      if (!this.clean) {
        if (this.phase === 'completed') this.phase = 'blocked'
        this.reason += ' Inventory cleanup is not confirmed; inspect cursor/grid before further work.'
      }
      const owner = this.locks.action
      if (nested) handle.release()
      else handle.release(this.phase, this.reason)
      if (!nested && owner?.id === this.actionId) await owner.done
      this.finishedAt = Date.now()
      if (!nested) this.ctx.events.push('production_progress', this.snapshot())
    }
  }

  /** @param {()=>void} check */
  async placeTable (check) {
    const existing = findTable(this.bot)
    if (existing) { this.table = existing; this.tableReused = true; return }
    const hint = workbenchPlacementHints(this.bot)[0]
    if (!hint) throw new ToolError('FORBIDDEN', 'No visible supported empty workbench position outside bodies within reach.')
    const reference = this.bot.blockAt(new Vec3(hint.referenceX, hint.referenceY, hint.referenceZ))
    if (!reference) throw new ToolError('NOT_FOUND', 'Workbench support is no longer loaded.')
    await this.bot.equip(this.bot.registry.itemsByName.crafting_table.id, 'hand')
    check()
    if (!workbenchPlacementHints(this.bot).some(candidate => candidate.referenceX === hint.referenceX && candidate.referenceY === hint.referenceY && candidate.referenceZ === hint.referenceZ)) {
      throw new ToolError('FORBIDDEN', 'Workbench space changed during equipment selection.')
    }
    await this.bot.placeBlock(reference, new Vec3(0, 1, 0))
    const table = this.bot.blockAt(reference.position.offset(0, 1, 0))
    if (!table || table.name !== 'crafting_table') throw new ToolError('INTERNAL', 'Server did not confirm workbench placement; no retry was attempted.')
    this.table = table
    this.tablePlaced = true
  }
}
