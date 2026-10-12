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
  dirt: 'dirt',
  oak_log: 'oak_log', birch_log: 'birch_log', spruce_log: 'spruce_log', jungle_log: 'jungle_log',
  acacia_log: 'acacia_log', dark_oak_log: 'dark_oak_log', cherry_log: 'cherry_log', mangrove_log: 'mangrove_log' })
/** @typedef {import('mineflayer').Bot & {tool:import('mineflayer-tool').Tool}} Bot */
/** @typedef {{x:number,y:number,z:number}} Position */
/** @typedef {{block:keyof typeof resources,count:number,x:number,y:number,z:number,radius:number,
 * mode?:'count'|'tree',chest?:Position,withdraw:import('./anelf-supply-plan.mjs').Request[],deposit:boolean}} Order */
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
    // A dirt order harvests every block that digs into the dirt item: the visible
    // surface is mostly grass blocks (and podzol, mycelium, dirt paths), all of
    // which drop the requested dirt item without silk touch.
    this.surfaceSoil = order.block === 'dirt'
    this.targetNames = new Set(this.surfaceSoil
      ? ['dirt', 'grass_block', 'podzol', 'mycelium', 'dirt_path'].filter(name => Boolean(bot.registry.blocksByName[name]))
      : [order.block])
    this.targetIds = [...this.targetNames].map(name => bot.registry.blocksByName[name].id)
    this.dug = 0; this.gained = 0; this.deposited = 0; this.returned = false; this.clean = true
    this.dirtReserveReady = false
    this.stage = 'planned'; this.reason = ''; this.returnReason = ''
    /** @type {Set<string>} */
    this.triedApproaches = new Set()
    /** @type {Phase} */ this.phase = 'running'
    this.startedAt = Date.now()
    /** @type {number|null} */ this.finishedAt = null
    /** @type {ReturnType<SupplyTask['snapshot']>[]} */ this.supplies = []
    this.done = Promise.resolve()
  }

  snapshot () {
    return { id: this.id, actionId: this.actionId, origin: this.origin, world: this.world,
      phase: this.phase, stage: this.stage, reason: this.reason, returnReason: this.returnReason,
      active: this.finishedAt === null, mode: this.order.mode ?? 'count', block: this.order.block, item: this.item, requested: this.order.count,
      before: this.before, available: this.available, dug: this.dug, gained: this.gained,
      remaining: this.order.mode === 'tree' ? null : Math.max(0, this.order.count - this.gained), deposited: this.deposited, returned: this.returned,
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
    if (Date.now() - this.startedAt > 240000) throw new Error('GATHER_TIMEOUT: Gathering time budget exhausted.')
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

  /** @param {GatherNavigation} navigation @param {Set<string>} [tried] @param {Map<string,Vec3>|null} [allowed] */
  async candidate (navigation, tried = new Set(), allowed = null) {
    const center = new Vec3(this.order.x, this.order.y, this.order.z)
    let positions = allowed ? [...allowed.values()] : this.bot.findBlocks({ matching: this.targetIds,
      point: center, maxDistance: this.order.radius, count: this.surfaceSoil ? 512 : 64 })
    if (!allowed && this.surfaceSoil) {
      // Layer-by-layer clearing: candidates ordered top-down, and within the
      // active layer the block closest to the bot comes next, so the clearing
      // sweeps outward from where it stands instead of hopping across the
      // field. The scan reruns after every removal, so the current top layer
      // keeps the work until it is fully cleared and only then releases the
      // layer below, with no separate layer state machine to maintain.
      const feet = this.bot.entity.position
      positions = [...positions].sort((a, b) => b.y - a.y || a.distanceTo(feet) - b.distanceTo(feet))
    }
    for (const p of positions) {
      if (allowed && !allowed.has(safety.key(p))) continue
      this.check(navigation.signal)
      // A standing player is above a ground-level trunk log, so logs keep a
      // bounded lower band eligible for roots on a slope without treating the
      // supporting floor as an excavation target. Surface soil is the floor
      // itself: it may dig the block underfoot, and the layer-by-layer order
      // then descends only after the current level is fully cleared.
      const minY = this.surfaceSoil ? Math.floor(this.bot.entity.position.y) - 1 : this.entry.y - 3
      if (p.y < minY || (p.y > this.entry.y + 8 && !(allowed && allowed.has(safety.key(p))))) continue
      const block = this.bot.blockAt(p)
      if (!block || !this.targetNames.has(block.name) || !safety.canHarvestBlock(this.bot, block, this.surfaceSoil)) continue
      const modes = p.y > this.entry.y + 2 ? ['scaffold', 'high'] : ['leaf', 'high', 'scaffold']
      for (const mode of modes) {
        // Without reserve material in the inventory a scaffold route can
        // never be built; skip the mode instead of paying for a doomed
        // path calculation.
        if (mode === 'scaffold' && !navigation.scaffoldingIds().length) continue
        const feetY = Math.floor(this.bot.entity.position.y)
        const standYs = mode === 'scaffold'
          ? [Math.min(this.entry.y + 8, Math.max(this.entry.y + 1, p.y))]
          : [feetY - 1, feetY, feetY + 1]
        const stands = []
        for (const standY of standYs) for (let x = -1; x <= 1; x++) for (let z = -1; z <= 1; z++) {
          const stand = new Vec3(p.x + x, standY, p.z + z)
          if (navigation.safeRoutePoint(stand, mode)) stands.push(stand)
        }
        stands.sort((a, b) => a.distanceTo(this.bot.entity.position) - b.distanceTo(this.bot.entity.position))
        for (const stand of stands) {
          this.check(navigation.signal)
          if (tried.has(`${safety.key(p)}|${safety.key(stand)}|${mode}`)) continue
          // A stand with no exposed harvest face can never pass the
          // post-arrival sightline check; reject it before paying for the
          // route search (and before arming the scaffold reserve).
          if (!reachableFace(this.bot, p, stand)) continue
          // Only the outbound route is a precondition. Clearing foliage or
          // building a temporary support can make the return route available;
          // it is verified after the actual harvest instead of rejecting the
          // tree before any safe work begins.
          if (mode === 'scaffold' && !this.surfaceSoil && !this.dirtReserveReady) {
            await this.ensureDirtReserve(navigation.signal)
            this.dirtReserveReady = true
          }
          if (await navigation.reachable(this.bot.entity.position, stand, mode)) return { block, stand, mode }
        }
      }
    }
    throw new Error('GATHER_NO_TARGET: No permitted target with a level walking and return route; buried blocks require a mine task.')
  }

  /** Verify actual arrival sightlines; bounded alternatives handle off-center pathfinder arrival.
   * @param {GatherNavigation} navigation @param {AbortSignal} signal
   * @param {Map<string,Vec3>|null} [allowed]
   */
  async approach (navigation, signal, allowed = null) {
    const tried = this.triedApproaches
    // Whether any leg was abandoned to a server pull-back. If the bounded
    // rotations all fail after one, the whole neighborhood shares the terrain
    // desync and the task must surface GATHER_POSITION_FIGHT so the worker
    // relocates instead of rescanning the same poisoned area.
    let fought = false
    for (let attempt = 0; attempt < 8; attempt++) {
      this.check(signal); this.stage = 'searching'
      const { block, stand, mode } = await this.candidate(navigation, tried, allowed)
      tried.add(`${safety.key(block.position)}|${safety.key(stand)}|${mode}`)
      this.stage = 'walking'
      let arrived = false
      try {
        await navigation.walk(stand, mode)
        arrived = true
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        // A stalled, refused, or server-pulled-back leg is this stand's
        // failure, not the task's: rotate to the next candidate stand instead
        // of burning a bounded task recovery round on the same blocked
        // approach. A position fight means the route crossed a cell the
        // server disagrees with (terrain desync), so retrying the identical
        // stand can only repeat the pull-back.
        if (!/GATHER_(?:TRAVEL_TIMEOUT|NOT_ARRIVED|NO_ROUTE|POSITION_FIGHT)/.test(message)) throw error
        if (message.includes('POSITION_FIGHT')) fought = true
      }
      this.check(signal)
      if (!arrived) continue
      if (this.bot.blockAt(block.position)?.type !== block.type || !safety.canHarvestBlock(this.bot, block, this.surfaceSoil)) {
        throw new Error('GATHER_TARGET_CHANGED: Target changed or became unsafe before digging.')
      }
      if (reachableFace(this.bot, block.position, this.bot.entity.position)) return { block, mode }
      if (mode !== 'leaf' && await this.clearSightObstruction(navigation, block, mode, signal)) continue
    }
    if (fought) throw new Error('GATHER_POSITION_FIGHT: The server keeps re-syncing the bot without displacement.')
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

  /** @param {GatherNavigation} navigation @param {AbortSignal} signal @param {Map<string,Vec3>|null} [allowed] */
  async collectOne (navigation, signal, allowed = null) {
    this.check(signal)
    if (this.bot.inventory.emptySlotCount() < 2) throw new Error('GATHER_FULL: Keep two inventory slots free.')
    const { block, mode } = await this.approach(navigation, signal, allowed)
    await this.bot.tool.equipForBlock(block, { requireHarvest: true, getFromChest: false })
    this.check(signal)
    if (this.bot.blockAt(block.position)?.type !== block.type || !reachableFace(this.bot, block.position, this.bot.entity.position) || !safety.canHarvestBlock(this.bot, block, this.surfaceSoil)) {
      throw new Error('GATHER_TARGET_CHANGED: Target or sightline changed during tool selection; stopped before digging.')
    }
    const held = this.bot.heldItem
    if (!block.canHarvest(held?.type ?? null)) throw new Error('GATHER_TOOL: No suitable harvesting tool.')
    if (held) {
      const maximum = this.bot.registry.itemsByName[held.name]?.maxDurability
      if (maximum && maximum - held.durabilityUsed < 12) throw new Error('GATHER_TOOL: Tool durability too low; keep a reserve.')
      if (held.enchants.some(enchantment => enchantment.name === 'silk_touch') && (this.order.block === 'stone' || this.surfaceSoil)) throw new Error('GATHER_TOOL: Silk touch would not produce the requested drop.')
    }
    const before = itemCount(this.bot, this.item)
    this.stage = 'digging'
    const timer = setTimeout(() => this.bot.stopDigging(), 15000)
    try {
      // A strip order descends by digging its own standing floor; the strict
      // temporary-descent gate (grounded, standing on the block, solid landing
      // below) revalidates everything the prechecks assumed.
      if (this.surfaceSoil && safety.isOwnSupport(this.bot, block.position)) await safety.descendTemporarySupport(this.bot, block, signal)
      else await this.bot.dig(block, true, 'raycast')
    } finally { clearTimeout(timer) }
    if (this.bot.blockAt(block.position)?.type === block.type) throw new Error('GATHER_UNCONFIRMED: Server did not confirm block removal.')
    this.dug++
    try {
      this.check(signal); this.stage = 'collecting'
      const pickups = []
      const feetY = Math.floor(this.bot.entity.position.y)
      for (const pickupY of [feetY, feetY - 1, feetY + 1]) {
        for (const [dx, dz] of [[0, 0], [1, 0], [-1, 0], [0, 1], [0, -1]]) {
          pickups.push({ point: new Vec3(block.position.x + dx, pickupY, block.position.z + dz),
            route: pickupY < this.entry.y ? 'high' : mode === 'scaffold' ? 'scaffold' : 'leaf' })
        }
      }
      for (const { point: pickup, route } of pickups) {
        if (!navigation.safeRoutePoint(pickup, route)) continue
        try {
          await navigation.walk(pickup, route)
          break
        } catch { /* try the next safe side of a root or canopy log */ }
      }
      if (itemCount(this.bot, this.item) <= before && this.bot.pathfinder?.goto && this.bot.pathfinder?.movements) {
        const movement = this.bot.pathfinder.movements
        const priorCanDig = movement.canDig
        movement.canDig = false
        let motion
        try {
          motion = this.bot.pathfinder.goto(new goals.GoalNear(block.position.x, block.position.y, block.position.z, 1))
          await Promise.race([motion, delay(5000, undefined, { signal })])
        } catch { /* the bounded pickup fallback is best effort */ } finally {
          this.bot.pathfinder.setGoal(null)
          if (motion) await motion.catch(() => {})
          movement.canDig = priorCanDig
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
   * Only adjacent logs are traversed; shared leaves never join separate trunks.
   */
  treeLogTargets (anchor) {
    const logName = this.item
    const pending = [anchor.clone()]
    const seen = new Set()
    const logs = new Map()
    const maxY = anchor.y + 12
    const maxHorizontal = 4
    while (pending.length && seen.size < 2048) {
      const position = pending.shift()
      const key = safety.key(position)
      if (seen.has(key)) continue
      seen.add(key)
      const block = this.bot.blockAt(position)
      if (!block) continue
      if (block.name !== logName) continue
      if (position.y < this.entry.y - 3 || position.y > maxY ||
          Math.hypot(position.x - anchor.x, position.z - anchor.z) > maxHorizontal) {
        throw new Error('GATHER_TREE_LIMIT: Connected logs exceed the bounded tree envelope; nothing was dug.')
      }
      logs.set(key, position.clone())
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
        if (dx || dy || dz) pending.push(position.offset(dx, dy, dz))
      }
    }
    if (pending.length) throw new Error('GATHER_TREE_LIMIT: Tree scan budget exhausted; nothing was dug.')
    return [...logs.values()]
  }

  /** Harvest the preflighted log component without discovering another tree mid-task. */
  async collectTree (navigation, signal, initialTargets) {
    const known = new Map(initialTargets.map(position => [safety.key(position), position.clone()]))
    for (let pass = 0; pass < 64; pass++) {
      this.check(signal)
      const targets = [...known.values()].filter(position => this.bot.blockAt(position)?.name === this.item)
      if (!targets.length) return
      if (this.dug >= this.order.count) throw new Error('GATHER_TREE_LIMIT: Authorized log budget exhausted.')
      if (targets.some(position => position.y > this.entry.y + 8)) {
        throw new Error('GATHER_TREE_TOO_HIGH: Full tree cleanup needs a reachable scaffold above the bounded gathering height.')
      }
      const allowed = new Map(targets.map(position => [safety.key(position), position]))
      await this.collectOne(navigation, signal, allowed)
    }
    throw new Error('GATHER_TREE_LIMIT: Tree cleanup exceeded the bounded log budget.')
  }

  /** Keep the routine temporary-support reserve on dirt before a tree climb. */
  async ensureDirtReserve (signal) {
    const reserve = 64
    const available = itemCount(this.bot, 'dirt')
    if (available >= reserve) return
    const task = new GatherTask(this.ctx, this.bot, {
      block: 'dirt', count: reserve - available, x: this.entry.x, y: this.entry.y, z: this.entry.z,
      radius: 8, mode: 'count', withdraw: [], deposit: false,
    })
    task.start(true)
    await task.done
    signal.throwIfAborted()
    if (task.phase !== 'completed' || itemCount(this.bot, 'dirt') < reserve) {
      throw new Error(`GATHER_SCAFFOLD_RESERVE: Need at least ${reserve} dirt blocks before climbing a tree.`)
    }
  }

  /** @param {ReturnType<ActionController['begin']>} handle @param {boolean} nested */
  async execute (handle, nested) {
    const navigation = new GatherNavigation(this.bot, this.entry, handle.signal, this.world)
    try {
      await yieldFrame(); this.check(handle.signal)
      if (!navigation.safe(this.entry)) throw new Error('GATHER_UNSAFE_ENTRY: Start on known level solid ground.')
      await navigation.restoreScaffolds()
      if (navigation.scaffolds.size) {
        this.stage = 'cleaning_scaffolds'
        await navigation.cleanupScaffolding()
        this.stage = 'planned'
      }
      if (this.order.withdraw.length) { this.stage = 'resupplying'; await this.supply(false); this.check(handle.signal) }
      this.before = itemCount(this.bot, this.item)
      try {
        if (this.order.mode === 'tree') {
          const preview = await this.candidate(navigation)
          const previewLogs = this.treeLogTargets(preview.block.position)
          if (previewLogs.length > this.order.count) {
            throw new Error('GATHER_TREE_LIMIT: Complete tree exceeds the authorized log budget; nothing was dug.')
          }
          if (previewLogs.some(position => position.y > this.entry.y + 8)) {
            throw new Error('GATHER_TREE_TOO_HIGH: Full tree cleanup needs a reachable scaffold above the bounded gathering height.')
          }
          await this.collectOne(
            navigation, handle.signal, new Map(previewLogs.map(position => [safety.key(position), position])),
          )
          await this.collectTree(navigation, handle.signal, previewLogs)
        } else {
          let recoveryAttempts = 0
          while (this.gained < this.order.count) {
            try {
              await this.collectOne(navigation, handle.signal)
              recoveryAttempts = 0
            } catch (error) {
              const message = error instanceof Error ? error.message : String(error)
              const recoverable = /GATHER_(?:NO_TARGET|NO_SIGHT|NO_PICKUP|TARGET_CHANGED|TRAVEL_TIMEOUT|NOT_ARRIVED)/.test(message)
              if (!recoverable || recoveryAttempts >= 2) throw error
              recoveryAttempts++
              this.stage = 'recovering'
              this.reason = `Retrying after bounded gathering failure (${recoveryAttempts}/2): ${message}`
              await delay(250, undefined, { signal: handle.signal })
            }
          }
        }
      } catch (error) {
        this.phase = 'blocked'; this.reason = error instanceof Error ? error.message : String(error)
      }
      handle.signal.throwIfAborted()
      if (!this.connected()) throw new Error('GATHER_INTERRUPTED: Connection changed before return.')
      this.stage = 'returning'
      try {
        if (!nested && this.order.block !== 'dirt' && this.bot.entity.position.floored().y > this.entry.y && !this.dirtReserveReady) {
          this.stage = 'reserving_dirt'
          await this.ensureDirtReserve(handle.signal)
          this.dirtReserveReady = true
        }
        const preferred = this.bot.entity.position.floored().y > this.entry.y ? 'scaffold' : 'leaf'
        const returnModes = [...new Set([preferred, 'high', 'scaffold', 'leaf'])]
        let returned = false
        let lastError = null
        for (const mode of returnModes) {
          try {
            await navigation.walk(this.entry, mode)
            returned = true
            break
          } catch (error) { lastError = error }
        }
        if (!returned) throw lastError ?? new Error('GATHER_RETURN_NO_ROUTE: No safe return route.')
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
