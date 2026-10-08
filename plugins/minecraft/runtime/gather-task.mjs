// @ts-check
/** Own gathering, verified inventory gains, return travel and optional explicit chest transfers. */
import { randomUUID } from 'node:crypto'
import { setImmediate as yieldFrame, setTimeout as delay } from 'node:timers/promises'
import { Vec3 } from 'vec3'
import safety from 'mineflayer/lib/anelf_mining/mining-safety.cjs'
import { ActionController } from '../bot/anelf-actions.mjs'
import { ToolError } from '../util/errors.js'
import { itemCount } from './anelf-production-plan.mjs'
import { inventoryClean } from './anelf-production-task.mjs'
import { SupplyTask } from './anelf-supply-task.mjs'
import { GatherNavigation, reachableFace } from './anelf-gather-navigation.mjs'

export const resources = /** @type {const} */ ({ stone: 'cobblestone', cobblestone: 'cobblestone',
  oak_log: 'oak_log', birch_log: 'birch_log', spruce_log: 'spruce_log', jungle_log: 'jungle_log',
  acacia_log: 'acacia_log', dark_oak_log: 'dark_oak_log', cherry_log: 'cherry_log', mangrove_log: 'mangrove_log' })
/** @typedef {import('mineflayer').Bot & {tool:import('mineflayer-tool').Tool}} Bot */
/** @typedef {{x:number,y:number,z:number}} Position */
/** @typedef {{block:keyof typeof resources,count:number,x:number,y:number,z:number,radius:number,
 * chest?:Position,withdraw:import('./anelf-supply-plan.mjs').Request[],deposit:boolean}} Order */
/** @typedef {'running'|'completed'|'blocked'|'cancelled'|'interrupted'} Phase */

export class GatherTask {
  /** @param {import('../context.js').ToolContext} ctx @param {Bot} bot @param {Order} order */
  constructor (ctx, bot, order) {
    if (!(ctx.locks instanceof ActionController)) throw new ToolError('INTERNAL', 'Gathering requires the installed action controller.')
    this.ctx = ctx; this.locks = ctx.locks; this.bot = bot; this.order = order
    this.id = randomUUID(); this.actionId = ''; this.origin = this.locks.requests.current() ?? null
    this.world = JSON.stringify([ctx.manager.statusReport().host, ctx.manager.statusReport().port, bot.username, bot.game.dimension])
    this.dimension = bot.game.dimension; this.entry = bot.entity.position.floored()
    this.item = resources[order.block]; this.before = itemCount(bot, this.item); this.available = this.before
    this.dug = 0; this.gained = 0; this.deposited = 0; this.returned = false; this.clean = true
    this.stage = 'planned'; this.reason = ''; this.returnReason = ''
    /** @type {Phase} */ this.phase = 'running'
    this.startedAt = Date.now()
    /** @type {number|null} */ this.finishedAt = null
    /** @type {ReturnType<SupplyTask['snapshot']>[]} */ this.supplies = []
    this.done = Promise.resolve()
  }

  snapshot () {
    return { id: this.id, actionId: this.actionId, origin: this.origin, world: this.world,
      phase: this.phase, stage: this.stage, reason: this.reason, returnReason: this.returnReason,
      active: this.finishedAt === null, block: this.order.block, item: this.item, requested: this.order.count,
      before: this.before, available: this.available, dug: this.dug, gained: this.gained,
      remaining: Math.max(0, this.order.count - this.gained), deposited: this.deposited, returned: this.returned,
      depositRequested: this.order.deposit, inventoryClean: this.clean,
      entry: { x: this.entry.x, y: this.entry.y, z: this.entry.z }, supplies: this.supplies,
      startedAt: this.startedAt, finishedAt: this.finishedAt }
  }

  /** Internal composition keeps the parent task id and sends no separate terminal event.
   * @param {boolean} [nested]
   */
  start (nested = false) {
    const handle = this.locks.begin('gather_resources')
    this.actionId = handle.actionId
    if (!nested) this.locks.linkTask(this.id, this.world)
    this.done = this.execute(handle, nested)
    return this.snapshot()
  }

  connected () { return this.ctx.manager.botOrNull() === this.bot && this.bot.game.dimension === this.dimension && this.bot.health > 0 }

  /** @param {AbortSignal} signal */
  check (signal) {
    signal.throwIfAborted()
    if (!this.connected()) throw new Error('GATHER_INTERRUPTED: Original world or living connection lost.')
    if (this.bot.health < 12 || this.bot.food < 10) throw new Error('GATHER_LOW_SUPPLIES: Health or food is too low for gathering.')
    if (Date.now() - this.startedAt > 120000) throw new Error('GATHER_TIMEOUT: Gathering time budget exhausted.')
  }

  /** @param {boolean} deposit */
  async supply (deposit) {
    if (!this.order.chest) return
    const task = new SupplyTask(this.ctx, this.bot, { ...this.order.chest,
      withdraw: deposit ? [] : this.order.withdraw,
      deposit: deposit ? [{ item: this.item, count: this.gained, keep: this.before }] : [],
    })
    task.start(true); await task.done
    const result = task.snapshot(); this.supplies.push(result)
    if (deposit) this.deposited = result.items.find(item => item.direction === 'deposit')?.deposited ?? 0
    if (result.phase !== 'completed') throw new Error(`GATHER_SUPPLY_FAILED: ${result.reason}`)
  }

  /** @param {GatherNavigation} navigation @param {Set<string>} [tried] */
  async candidate (navigation, tried = new Set()) {
    const center = new Vec3(this.order.x, this.order.y, this.order.z)
    const positions = this.bot.findBlocks({ matching: this.bot.registry.blocksByName[this.order.block].id,
      point: center, maxDistance: this.order.radius, count: 64 })
    for (const p of positions) {
      this.check(navigation.signal)
      if (p.y < this.entry.y || p.y > this.entry.y + 2) continue
      const block = this.bot.blockAt(p)
      if (!block || !safety.canHarvestBlock(this.bot, block)) continue
      const stands = []
      for (let x = -1; x <= 1; x++) for (let z = -1; z <= 1; z++) {
        const stand = new Vec3(p.x + x, this.entry.y, p.z + z)
        if (navigation.safe(stand)) stands.push(stand)
      }
      stands.sort((a, b) => a.distanceTo(this.bot.entity.position) - b.distanceTo(this.bot.entity.position))
      for (const stand of stands) {
        this.check(navigation.signal)
        if (tried.has(`${safety.key(p)}|${safety.key(stand)}`) || !reachableFace(this.bot, p, stand.offset(0.5, 0, 0.5))) continue
        if (await navigation.reachable(this.bot.entity.position, stand) && await navigation.reachable(stand, this.entry)) return { block, stand }
      }
    }
    throw new Error('GATHER_NO_TARGET: No permitted target with a level walking and return route; buried blocks require a mine task.')
  }

  /** Verify actual arrival sightlines; bounded alternatives handle off-center pathfinder arrival.
   * @param {GatherNavigation} navigation @param {AbortSignal} signal
   */
  async approach (navigation, signal) {
    const tried = new Set()
    for (let attempt = 0; attempt < 8; attempt++) {
      this.check(signal); this.stage = 'searching'
      const { block, stand } = await this.candidate(navigation, tried)
      tried.add(`${safety.key(block.position)}|${safety.key(stand)}`)
      this.stage = 'walking'; await navigation.walk(stand); this.check(signal)
      if (this.bot.blockAt(block.position)?.type !== block.type || !safety.canHarvestBlock(this.bot, block)) {
        throw new Error('GATHER_TARGET_CHANGED: Target changed or became unsafe before digging.')
      }
      if (reachableFace(this.bot, block.position, this.bot.entity.position)) return block
    }
    throw new Error('GATHER_NO_SIGHT: No visible harvest face after bounded safe standing attempts.')
  }

  /** @param {GatherNavigation} navigation @param {AbortSignal} signal */
  async collectOne (navigation, signal) {
    this.check(signal)
    if (this.bot.inventory.emptySlotCount() < 2) throw new Error('GATHER_FULL: Keep two inventory slots free.')
    const block = await this.approach(navigation, signal)
    await this.bot.tool.equipForBlock(block, { requireHarvest: true, getFromChest: false })
    this.check(signal)
    if (this.bot.blockAt(block.position)?.type !== block.type || !reachableFace(this.bot, block.position, this.bot.entity.position) || !safety.canHarvestBlock(this.bot, block)) {
      throw new Error('GATHER_TARGET_CHANGED: Target or sightline changed during tool selection; stopped before digging.')
    }
    const held = this.bot.heldItem
    if (!block.canHarvest(held?.type ?? null)) throw new Error('GATHER_TOOL: No suitable harvesting tool.')
    if (held) {
      const maximum = this.bot.registry.itemsByName[held.name]?.maxDurability
      if (maximum && maximum - held.durabilityUsed < 12) throw new Error('GATHER_TOOL: Tool durability too low; keep a reserve.')
      if (held.enchants.some(enchantment => enchantment.name === 'silk_touch') && this.order.block === 'stone') throw new Error('GATHER_TOOL: Silk touch would not produce the requested cobblestone.')
    }
    const before = itemCount(this.bot, this.item)
    this.stage = 'digging'
    const timer = setTimeout(() => this.bot.stopDigging(), 15000)
    try { await this.bot.dig(block, true, 'raycast') } finally { clearTimeout(timer) }
    if (this.bot.blockAt(block.position)?.type === block.type) throw new Error('GATHER_UNCONFIRMED: Server did not confirm block removal.')
    this.dug++
    try {
      this.check(signal); this.stage = 'collecting'
      const pickup = new Vec3(block.position.x, this.entry.y, block.position.z)
      if (navigation.safe(pickup)) await navigation.walk(pickup)
      const end = Date.now() + 4000
      while (itemCount(this.bot, this.item) <= before && Date.now() < end) {
        this.check(signal); await delay(100, undefined, { signal })
      }
    } finally {
      // A vanished entity or a resolved dig promise is not an inventory receipt.
      this.gained += Math.min(1, Math.max(0, itemCount(this.bot, this.item) - before))
      this.available = itemCount(this.bot, this.item)
    }
    if (this.available <= before) throw new Error('GATHER_NO_PICKUP: Block removed but no matching inventory gain; remaining excavation stopped.')
  }

  /** @param {ReturnType<ActionController['begin']>} handle @param {boolean} nested */
  async execute (handle, nested) {
    const navigation = new GatherNavigation(this.bot, this.entry, handle.signal)
    try {
      await yieldFrame(); this.check(handle.signal)
      if (!navigation.safe(this.entry)) throw new Error('GATHER_UNSAFE_ENTRY: Start on known level solid ground.')
      if (this.order.withdraw.length) { this.stage = 'resupplying'; await this.supply(false); this.check(handle.signal) }
      this.before = itemCount(this.bot, this.item)
      try {
        while (this.gained < this.order.count) await this.collectOne(navigation, handle.signal)
      } catch (error) {
        this.phase = 'blocked'; this.reason = error instanceof Error ? error.message : String(error)
      }
      handle.signal.throwIfAborted()
      if (!this.connected()) throw new Error('GATHER_INTERRUPTED: Connection changed before return.')
      this.stage = 'returning'
      try { await navigation.walk(this.entry); this.returned = true } catch (error) {
        this.returnReason = error instanceof Error ? error.message : String(error); throw error
      }
      handle.signal.throwIfAborted()
      if (this.phase === 'running' && this.order.deposit) { this.stage = 'depositing'; await this.supply(true) }
      if (this.phase === 'running') { this.phase = 'completed'; this.stage = 'done' }
    } catch (error) {
      this.phase = this.locks.action?.outcome === 'interrupted' || !this.connected() ? 'interrupted' : handle.signal.aborted ? 'cancelled' : 'blocked'
      this.reason ||= error instanceof Error ? error.message : String(error)
    } finally {
      this.bot.pathfinder.setGoal(null)
      this.available = itemCount(this.bot, this.item)
      if (handle.signal.aborted) this.phase = this.locks.action?.outcome === 'interrupted' ? 'interrupted' : 'cancelled'
      this.clean = inventoryClean(this.bot)
      if (!this.clean && this.phase === 'completed') { this.phase = 'blocked'; this.reason = 'GATHER_INVENTORY: Inventory cleanup was not confirmed.' }
      const owner = this.locks.action
      if (nested) handle.release()
      else handle.release(this.phase, this.reason)
      if (!nested && owner?.id === this.actionId) await owner.done
      this.finishedAt = Date.now()
      if (!nested) this.ctx.events.push('gather_progress', this.snapshot())
    }
  }
}
