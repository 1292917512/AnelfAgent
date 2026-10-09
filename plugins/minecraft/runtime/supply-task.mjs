// @ts-check
/** Own a bounded chest session, verify every click and return the cursor before releasing control. */
import { randomUUID } from 'node:crypto'
import { setImmediate as yieldFrame } from 'node:timers/promises'
import { isDeepStrictEqual } from 'node:util'
import { Vec3 } from 'vec3'
import inventorySafety from 'mineflayer/lib/anelf_inventory.js'
import { ActionController } from '../bot/anelf-actions.mjs'
import { ToolError } from '../util/errors.js'
import { inventoryClean } from './anelf-production-task.mjs'
import { planSupplies, slotState, itemState, countSlots } from './anelf-supply-plan.mjs'

/** @typedef {import('mineflayer').Bot} Bot */
/** @typedef {import('prismarine-windows').Window} Window */
/** @typedef {import('../context.js').ToolContext} Context */
/** @typedef {import('./anelf-supply-plan.mjs').Request} Request */
/** @typedef {{x:number,y:number,z:number,deposit:Request[],withdraw:Request[]}} Order */
/** @typedef {'running'|'completed'|'blocked'|'cancelled'|'interrupted'} Phase */

/** Only explicit ordinary chests/barrels within direct reach are admitted; trapped chests have side effects.
 * @param {Bot} bot @param {{x:number,y:number,z:number}} position
 */
export function supplyContainer (bot, position) {
  const block = bot.blockAt(new Vec3(position.x, position.y, position.z))
  if (!block || !['chest', 'barrel'].includes(block.name)) throw new ToolError('INVALID_ARGS', 'Specify the exact coordinates of a loaded ordinary chest or barrel.')
  if (bot.entity.position.offset(0, 1.62, 0).distanceTo(block.position.offset(0.5, 0.5, 0.5)) > 4 || !bot.canSeeBlock(block)) {
    throw new ToolError('FORBIDDEN', 'Specified container must be visible and within 4 blocks; this task does not walk or choose a different chest.')
  }
  return block
}

export class SupplyTask {
  /** @param {Context} ctx @param {Bot} bot @param {Order} order */
  constructor (ctx, bot, order) {
    if (!(ctx.locks instanceof ActionController)) throw new ToolError('INTERNAL', 'Supply tasks require the installed action controller.')
    this.ctx = ctx; this.locks = ctx.locks; this.bot = bot; this.order = order
    this.id = randomUUID(); this.actionId = ''; this.origin = this.locks.requests.current() ?? null
    this.world = JSON.stringify([ctx.manager.statusReport().host, ctx.manager.statusReport().port, bot.username, bot.game.dimension])
    this.dimension = bot.game.dimension
    this.startedAt = Date.now()
    /** @type {number|null} */
    this.finishedAt = null
    /** @type {Phase} */
    this.phase = 'running'
    this.stage = 'planned'; this.reason = ''; this.failureCode = ''; this.clean = false; this.confirmed = false; this.stepsDone = 0
    /** @type {Window|null} */
    this.window = null
    /** @type {ReturnType<typeof planSupplies>|null} */
    this.plan = null
    /** @type {ReturnType<typeof slotState>|null} */
    this.after = null
    /** @type {[number,number]|null} */
    this.recoveryRange = null
    this.done = Promise.resolve()
  }

  snapshot () {
    const window = this.window, plan = this.plan, after = this.after
    const items = window && plan && after ? plan.requests.map(request => {
      const available = countSlots(after, request.item, window.inventoryStart, window.inventoryEnd)
      const containerDelta = countSlots(after, request.item, 0, window.inventoryStart) - countSlots(plan.initial, request.item, 0, window.inventoryStart)
      const inventoryDelta = available - request.before
      const verified = inventoryDelta === -containerDelta
      return { ...request, available, inventoryDelta, containerDelta, verified,
        deposited: verified && request.direction === 'deposit' ? Math.max(0, Math.min(request.amount, -inventoryDelta)) : 0,
        withdrawn: verified && request.direction === 'withdraw' ? Math.max(0, Math.min(request.amount, inventoryDelta)) : 0 }
    }) : []
    return { id: this.id, actionId: this.actionId, origin: this.origin, world: this.world,
      container: { x: this.order.x, y: this.order.y, z: this.order.z }, phase: this.phase, stage: this.stage,
      active: this.finishedAt === null, reason: this.reason, failureCode: this.failureCode, inventoryClean: this.clean, confirmed: this.confirmed,
      stepsDone: this.stepsDone, stepsTotal: plan?.moves.length ?? 0, items,
      startedAt: this.startedAt, finishedAt: this.finishedAt }
  }

  /** Nested transfers retain the parent's ownership and publish only through its final report.
   * @param {boolean} [nested]
   */
  start (nested = false) {
    const handle = this.locks.begin('manage_supplies')
    this.actionId = handle.actionId
    if (!nested) this.locks.linkTask(this.id, this.world)
    this.done = this.execute(handle, nested)
    return this.snapshot()
  }

  connected () {
    return this.ctx.manager.botOrNull() === this.bot && this.bot.game.dimension === this.dimension && this.bot.health > 0 &&
      this.locks.action?.outcome !== 'interrupted'
  }

  /** @param {ReturnType<ActionController['begin']>} handle @param {boolean} nested */
  async execute (handle, nested) {
    const deadline = AbortSignal.timeout(120000)
    const check = () => {
      handle.signal.throwIfAborted(); deadline.throwIfAborted()
      if (!this.connected()) throw new ToolError('CANCELLED', 'World, life or connection changed; supply work will not resume.')
      supplyContainer(this.bot, this.order)
    }
    try {
      await inventorySafety.runInventory(this.bot, async () => {
        try {
          await yieldFrame(); check()
          this.stage = 'opening'
          this.window = await this.bot.openContainer(supplyContainer(this.bot, this.order))
          check()
          await this.sync()
          if (this.window.selectedItem || ![27, 54].includes(this.window.inventoryStart) || this.window.inventoryEnd - this.window.inventoryStart !== 36) {
            throw new ToolError('FORBIDDEN', 'Unexpected container window or cursor state; nothing was moved.')
          }
          this.stage = 'planning'
          this.plan = planSupplies(this.bot, this.window, this.order.deposit, this.order.withdraw)
          const expected = this.plan.initial.map(item => item ? { ...item } : null)
          for (const move of this.plan.moves) {
            check()
            await this.sync(); check()
            if (!isDeepStrictEqual(slotState(this.bot, this.window), expected)) throw new ToolError('BUSY', 'Container or inventory changed after planning; remaining transfers stopped.')
            const source = expected[move.source]
            if (!source) throw new ToolError('INTERNAL', 'Planned source is missing.')
            this.stage = `${move.source < this.window.inventoryStart ? 'withdraw' : 'deposit'}:${move.item}`
            this.recoveryRange = move.source < this.window.inventoryStart ? [0, this.window.inventoryStart] : [this.window.inventoryStart, this.window.inventoryEnd]
            const cursor = { ...source }
            expected[move.source] = null
            await this.click(move.source, 0, expected, cursor)
            let remaining = move.count
            while (remaining > 0) {
              check()
              const dest = expected[move.destination]
              const capacity = cursor.max - (dest?.count ?? 0)
              const all = Math.min(cursor.count, capacity)
              const button = all <= remaining ? 0 : 1
              const amount = button === 0 ? all : 1
              expected[move.destination] = { ...cursor, count: (dest?.count ?? 0) + amount }
              cursor.count -= amount; remaining -= amount
              await this.click(move.destination, button, expected, cursor.count ? cursor : null)
            }
            if (cursor.count) {
              expected[move.source] = { ...cursor }
              await this.click(move.source, 0, expected, null)
            }
            this.recoveryRange = null
            this.stepsDone++
            this.after = slotState(this.bot, this.window)
          }
          check()
          this.phase = 'completed'
          this.stage = 'done'
        } finally {
          await this.recover()
        }
      })
    } catch (error) {
      this.phase = this.locks.action?.outcome === 'interrupted' ? 'interrupted' : handle.signal.aborted ? 'cancelled' : 'blocked'
      this.reason = error instanceof Error ? error.message : String(error)
      this.failureCode = error instanceof ToolError ? error.code : 'INTERNAL'
    } finally {
      if (handle.signal.aborted) this.phase = this.locks.action?.outcome === 'interrupted' ? 'interrupted' : 'cancelled'
      const items = this.snapshot().items
      this.confirmed = Boolean(this.plan && this.after && this.window) && items.length > 0 && items.every(item => item.verified)
      if (this.phase === 'completed' && (!this.clean || !this.confirmed || items.some(item => item.available !== item.target))) {
        this.phase = 'blocked'; this.reason = 'Final inventory, container accounting or cursor cleanup was not confirmed.'
        this.failureCode = 'INTERNAL'
      }
      const owner = this.locks.action
      if (nested) handle.release()
      else handle.release(this.phase, this.reason)
      if (!nested && owner?.id === this.actionId) await owner.done
      this.finishedAt = Date.now()
      if (!nested) this.ctx.events.push('supply_progress', this.snapshot())
    }
  }

  async sync () {
    const window = this.window
    if (!window || this.bot.currentWindow !== window || !this.connected()) throw new ToolError('NO_WINDOW', 'Supply container is no longer open in the original session.')
    await inventorySafety.syncInventory(this.bot, window)
    if (this.bot.currentWindow !== window || !this.connected()) throw new ToolError('NO_WINDOW', 'Container closed during server confirmation.')
  }

  /** @param {number} slot @param {number} button @param {ReturnType<typeof slotState>} expected @param {ReturnType<typeof itemState>} cursor */
  async click (slot, button, expected, cursor) {
    if (!this.window || this.bot.currentWindow !== this.window) throw new ToolError('NO_WINDOW', 'Container closed before click.')
    await this.bot.clickWindow(slot, button, 0)
    await this.sync()
    this.after = slotState(this.bot, this.window)
    if (!isDeepStrictEqual(this.after, expected) || !isDeepStrictEqual(itemState(this.bot, this.window.selectedItem), cursor)) {
      throw new ToolError('BUSY', 'Server did not confirm the exact transfer; stopped without retrying.')
    }
  }

  async recover () {
    const window = this.window
    if (!window) { this.clean = !this.bot.currentWindow && inventoryClean(this.bot); return }
    if (!this.connected() || this.bot.currentWindow !== window) throw new ToolError('NO_WINDOW', 'Container session lost; inventory cleanup is unconfirmed.')
    await this.sync()
    while (window.selectedItem) {
      if (!this.recoveryRange) throw new ToolError('BUSY', 'Unknown cursor ownership; inspect the inventory before further work.')
      const expected = slotState(this.bot, window), cursor = itemState(this.bot, window.selectedItem)
      if (!cursor) break
      const [start, end] = this.recoveryRange
      let slot = expected.findIndex((item, index) => index >= start && index < end && item?.key === cursor.key && item.count < item.max)
      if (slot < 0) slot = expected.findIndex((item, index) => index >= start && index < end && !item)
      if (slot < 0) throw new ToolError('INVENTORY_FULL_NO_CHEST', 'Source region has no compatible cursor recovery space; inspect the open window.')
      const destination = expected[slot]
      const amount = Math.min(cursor.count, cursor.max - (destination?.count ?? 0))
      expected[slot] = { ...cursor, count: (destination?.count ?? 0) + amount }
      cursor.count -= amount
      await this.click(slot, 0, expected, cursor.count ? cursor : null)
    }
    if (window.selectedItem) throw new ToolError('BUSY', 'Cursor recovery was not confirmed; window left open, nothing was deliberately dropped.')
    this.after = slotState(this.bot, window)
    await this.bot.closeWindow(window)
    await inventorySafety.syncInventory(this.bot, this.bot.inventory)
    this.clean = this.connected() && !this.bot.currentWindow && inventoryClean(this.bot)
    const expectedInventory = this.after.slice(window.inventoryStart, window.inventoryEnd)
    const actualInventory = slotState(this.bot, this.bot.inventory).slice(this.bot.inventory.inventoryStart, this.bot.inventory.inventoryEnd)
    if (!isDeepStrictEqual(expectedInventory, actualInventory)) {
      this.after = null
      throw new ToolError('BUSY', 'Inventory changed while closing the container; transfer totals are unconfirmed.')
    }
  }
}
