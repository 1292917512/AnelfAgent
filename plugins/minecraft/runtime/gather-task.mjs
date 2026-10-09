// @ts-check
/** Own gathering, verified inventory gains, return travel and optional explicit chest transfers. */
import { randomUUID } from 'node:crypto'
import { setImmediate as yieldFrame, setTimeout as delay } from 'node:timers/promises'
import { Vec3 } from 'vec3'
import pathfinderPkg from 'mineflayer-pathfinder'
import safety from 'mineflayer/lib/anelf_mining/mining-safety.cjs'
import { ActionController } from '../bot/anelf-actions.mjs'
import { ToolError } from '../util/errors.js'
import { itemCount } from './anelf-production-plan.mjs'
import { inventoryClean } from './anelf-production-task.mjs'
import { SupplyTask } from './anelf-supply-task.mjs'
import { GatherNavigation, reachableFace } from './anelf-gather-navigation.mjs'

const { goals } = pathfinderPkg

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
    /** @type {Vec3|null} */ this.treeAnchor = null
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
  async candidate (navigation, tried = new Set(), allowed = null) {
    const center = new Vec3(this.order.x, this.order.y, this.order.z)
    const positions = this.bot.findBlocks({ matching: this.bot.registry.blocksByName[this.order.block].id,
      point: center, maxDistance: this.order.radius, count: 64 })
    for (const p of positions) {
      if (allowed && !allowed.has(safety.key(p))) continue
      this.check(navigation.signal)
      // A standing player is above a ground-level trunk log. Keep a bounded
      // lower band eligible so roots on a slope can be harvested without
      // treating the supporting floor as an excavation target.
      if (p.y < this.entry.y - 3 || (p.y > this.entry.y + 3 && !(allowed && allowed.has(safety.key(p))))) continue
      const block = this.bot.blockAt(p)
      if (!block || !safety.canHarvestBlock(this.bot, block)) continue
      const modes = p.y > this.entry.y + 2 ? ['scaffold'] : ['leaf', 'high', 'scaffold']
      for (const mode of modes) {
        const standY = mode === 'scaffold' ? this.entry.y + 1 : this.entry.y
        const stands = []
        for (let x = -1; x <= 1; x++) for (let z = -1; z <= 1; z++) {
          const stand = new Vec3(p.x + x, standY, p.z + z)
          if (navigation.safeRoutePoint(stand, mode)) stands.push(stand)
        }
        stands.sort((a, b) => a.distanceTo(this.bot.entity.position) - b.distanceTo(this.bot.entity.position))
        for (const stand of stands) {
          this.check(navigation.signal)
          if (tried.has(`${safety.key(p)}|${safety.key(stand)}|${mode}`)) continue
          if (await navigation.reachable(this.bot.entity.position, stand, mode) && await navigation.reachable(stand, this.entry, mode)) return { block, stand, mode }
        }
      }
    }
    throw new Error('GATHER_NO_TARGET: No permitted target with a level walking and return route; buried blocks require a mine task.')
  }

  /** Verify actual arrival sightlines; bounded alternatives handle off-center pathfinder arrival.
   * @param {GatherNavigation} navigation @param {AbortSignal} signal
   * @param {Set<string>|null} [allowed]
   */
  async approach (navigation, signal, allowed = null) {
    const tried = new Set()
    for (let attempt = 0; attempt < 8; attempt++) {
      this.check(signal); this.stage = 'searching'
      const { block, stand, mode } = await this.candidate(navigation, tried, allowed)
      tried.add(`${safety.key(block.position)}|${safety.key(stand)}|${mode}`)
      this.stage = 'walking'; await navigation.walk(stand, mode); this.check(signal)
      if (this.bot.blockAt(block.position)?.type !== block.type || !safety.canHarvestBlock(this.bot, block)) {
        throw new Error('GATHER_TARGET_CHANGED: Target changed or became unsafe before digging.')
      }
      if (reachableFace(this.bot, block.position, this.bot.entity.position)) return { block, mode }
      if (mode !== 'leaf' && await this.clearSightObstruction(navigation, block, mode, signal)) continue
    }
    throw new Error('GATHER_NO_SIGHT: No visible harvest face after bounded safe standing attempts.')
  }

  /** Remove one bounded, higher-than-feet obstruction that hides a target face. */
  async clearSightObstruction (navigation, target, mode, signal) {
    const height = 'eyeHeight' in this.bot.entity ? this.bot.entity.eyeHeight : null
    if (typeof height !== 'number' || !Number.isFinite(height) || height <= 0) return false
    const eye = this.bot.entity.position.offset(0, height, 0)
    const center = target.position.offset(0.5, 0.5, 0.5)
    for (const axis of /** @type {const} */ (['x', 'y', 'z'])) {
      const delta = eye[axis] - center[axis]
      if (Math.abs(delta) <= 0.5) continue
      const face = center.clone(); face[axis] += Math.sign(delta) * 0.499
      const direction = face.minus(eye)
      if (direction.norm() > 4) continue
      const hit = this.bot.world.raycast(eye, direction.normalize(), 4)
      if (!hit || !('position' in hit) || !(hit.position instanceof Vec3) || hit.position.equals(target.position)) continue
      const obstruction = this.bot.blockAt(hit.position)
      if (!obstruction || !navigation.breakable(hit.position, mode, this.bot.entity.position.y) || !safety.canHarvestBlock(this.bot, obstruction)) continue
      try {
        await this.bot.tool.equipForBlock(obstruction, { requireHarvest: true, getFromChest: false })
        this.check(signal)
        await this.bot.dig(obstruction, true, 'raycast')
      } catch {
        continue
      }
      if (this.bot.blockAt(obstruction.position)?.type === obstruction.type) continue
      return true
    }
    return false
  }

  /** @param {GatherNavigation} navigation @param {AbortSignal} signal */
  async collectOne (navigation, signal, allowed = null) {
    this.check(signal)
    if (this.bot.inventory.emptySlotCount() < 2) throw new Error('GATHER_FULL: Keep two inventory slots free.')
    const { block, mode } = await this.approach(navigation, signal, allowed)
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
    if (mode === 'scaffold') await navigation.cleanupScaffolding()
    try {
      this.check(signal); this.stage = 'collecting'
      const pickups = [
        new Vec3(block.position.x, this.entry.y, block.position.z),
        new Vec3(block.position.x + 1, this.entry.y, block.position.z),
        new Vec3(block.position.x - 1, this.entry.y, block.position.z),
        new Vec3(block.position.x, this.entry.y, block.position.z + 1),
        new Vec3(block.position.x, this.entry.y, block.position.z - 1),
      ]
      for (const pickup of pickups) {
        if (!navigation.safe(pickup)) continue
        try {
          await navigation.walk(pickup, mode === 'scaffold' ? 'scaffold' : 'leaf')
          break
        } catch { /* try the next safe side of a root or canopy log */ }
      }
      if (itemCount(this.bot, this.item) <= before && this.bot.pathfinder?.goto && this.bot.pathfinder?.movements) {
        const movement = this.bot.pathfinder.movements
        const priorCanDig = movement.canDig
        movement.canDig = false
        try {
          const motion = this.bot.pathfinder.goto(new goals.GoalNear(block.position.x, block.position.y, block.position.z, 1))
          await Promise.race([motion, delay(5000, undefined, { signal })])
        } catch { /* the bounded pickup fallback is best effort */ } finally {
          movement.canDig = priorCanDig
          this.bot.pathfinder.setGoal(null)
        }
      }
      const end = Date.now() + 6000
      while (itemCount(this.bot, this.item) <= before && Date.now() < end) {
        this.check(signal); await delay(100, undefined, { signal })
      }
    } finally {
      // A vanished entity or a resolved dig promise is not an inventory receipt.
      this.gained += Math.min(1, Math.max(0, itemCount(this.bot, this.item) - before))
      this.available = itemCount(this.bot, this.item)
    }
    if (this.available <= before) throw new Error('GATHER_NO_PICKUP: Block removed but no matching inventory gain; remaining excavation stopped.')
    return block.position.clone()
  }

  /**
   * Return the connected log component around the first harvested block.
   * Leaves are traversed only inside a small crown envelope, so a nearby
   * second tree is not silently treated as part of the target tree.
   */
  connectedTreeLogs (anchor) {
    const logName = this.item
    const pending = [anchor.clone()]
    const seen = new Set()
    const logs = new Map()
    const maxY = anchor.y + 8
    const maxHorizontal = 4
    while (pending.length && seen.size < 256) {
      const position = pending.shift()
      const key = safety.key(position)
      if (seen.has(key)) continue
      seen.add(key)
      if (position.y < this.entry.y - 3 || position.y > maxY ||
          Math.hypot(position.x - anchor.x, position.z - anchor.z) > maxHorizontal) continue
      const block = this.bot.blockAt(position)
      if (!block) continue
      const isLog = block.name === logName
      const isLeaf = typeof block.name === 'string' && /^(?:[a-z0-9]+)_leaves$/.test(block.name)
      if (!isLog && !isLeaf) continue
      if (isLog) logs.set(key, position.clone())
      for (const [dx, dy, dz] of [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]) {
        pending.push(position.offset(dx, dy, dz))
      }
    }
    return [...logs.values()]
  }

  /** Harvest every remaining log in the target tree, even when count is met. */
  async collectTree (navigation, signal, anchor) {
    this.treeAnchor = anchor.clone()
    for (let pass = 0; pass < 64; pass++) {
      this.check(signal)
      const targets = this.connectedTreeLogs(this.treeAnchor)
      if (!targets.length) return
      if (targets.some(position => position.y > this.entry.y + 3)) {
        throw new Error('GATHER_TREE_TOO_HIGH: Full tree cleanup needs a reachable scaffold above the bounded gathering height.')
      }
      const allowed = new Set(targets.map(position => safety.key(position)))
      const before = this.dug
      try {
        await this.collectOne(navigation, signal, allowed)
      } catch (error) {
        if (this.dug === before) throw error
      }
    }
    throw new Error('GATHER_TREE_LIMIT: Tree cleanup exceeded the bounded log budget.')
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
        if (this.order.block.endsWith('_log')) {
          const preview = await this.candidate(navigation)
          const previewLogs = this.connectedTreeLogs(preview.block.position)
          if (previewLogs.some(position => position.y > this.entry.y + 3)) {
            throw new Error('GATHER_TREE_TOO_HIGH: Full tree cleanup needs a reachable scaffold above the bounded gathering height.')
          }
          const first = await this.collectOne(
            navigation, handle.signal, new Set(previewLogs.map(position => safety.key(position))),
          )
          await this.collectTree(navigation, handle.signal, first)
        } else {
          while (this.gained < this.order.count) await this.collectOne(navigation, handle.signal)
        }
      } catch (error) {
        this.phase = 'blocked'; this.reason = error instanceof Error ? error.message : String(error)
      }
      handle.signal.throwIfAborted()
      if (!this.connected()) throw new Error('GATHER_INTERRUPTED: Connection changed before return.')
      this.stage = 'returning'
      try {
        const returnMode = this.bot.entity.position.floored().y > this.entry.y ? 'scaffold' : 'leaf'
        try {
          await navigation.walk(this.entry, returnMode)
        } catch (firstError) {
          if (returnMode !== 'leaf') throw firstError
          await navigation.walk(this.entry, 'scaffold')
        }
        await navigation.cleanupScaffolding()
        this.returned = true
      } catch (error) {
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
