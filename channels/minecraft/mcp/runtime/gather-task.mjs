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
import { GatherNavigation, isLeafBlockName, reachableFace, scaffoldHeight } from './anelf-gather-navigation.mjs'

const { goals } = pathfinderPkg

export const resources = /** @type {const} */ ({ stone: 'cobblestone', cobblestone: 'cobblestone',
  dirt: 'dirt',
  oak_log: 'oak_log', birch_log: 'birch_log', spruce_log: 'spruce_log', jungle_log: 'jungle_log',
  acacia_log: 'acacia_log', dark_oak_log: 'dark_oak_log', cherry_log: 'cherry_log', mangrove_log: 'mangrove_log',
  pale_oak_log: 'pale_oak_log' })

/** Surface blocks that all dig into the dirt item without silk touch. */
const soilNames = ['dirt', 'grass_block', 'podzol', 'mycelium', 'dirt_path']
const soilSet = new Set(soilNames)

// The scaffolding reserve floor for mixed orders: below it the current chunk's
// top soil layer takes absolute priority over trees, because climbing a tree
// needs dirt to stand on. Climbing's own 64-dirt hard reserve stays independent.
const tier1DirtFloor = 10

/** Trunk attachments that hide dig faces — vines and jungle cocoa pods —
 * get stripped before the dig, not hopped. */
const isVineName = name => name === 'vine' || name === 'cocoa' ||
  name.endsWith('vines') || name.endsWith('vines_plant')

/**
 * Natural trunk log names known to the connected registry. Stripped trunks are
 * player-made in practice and the legacy oak "log" block is not a *_log name.
 * @param {import('mineflayer').Bot} bot
 */
export function trunkLogNames (bot) {
  return Object.keys(bot.registry?.blocksByName ?? {})
    .filter(name => name.endsWith('_log') && !name.startsWith('stripped_'))
}
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
    this.mixed = order.block === 'auto'
    this.item = resources[order.block] ?? null; this.before = this.item ? itemCount(bot, this.item) : 0
    this.available = this.before
    // A dirt order harvests every block that digs into the dirt item: the visible
    // surface is mostly grass blocks (and podzol, mycelium, dirt paths), all of
    // which drop the requested dirt item without silk touch.
    this.surfaceSoil = order.block === 'dirt'
    // An auto order mixes the surface soil family with every natural trunk log
    // in this registry, so a new wood species needs no code change here.
    this.logNames = new Set(this.mixed ? trunkLogNames(bot) : [])
    this.targetNames = new Set((this.mixed ? [...soilNames, ...this.logNames]
      : this.surfaceSoil ? soilNames : [order.block])
      .filter(name => Boolean(bot.registry.blocksByName[name])))
    this.targetIds = [...this.targetNames].map(name => bot.registry.blocksByName[name].id)
    // Optional chunk-clamped work face: single-resource orders stay inside the
    // chunk column; mixed orders clamp only their top-priority soil tier.
    this.chunk = order.chunk ?? null
    // Dirt-quota orders: mixed work that never digs soil this order.
    this.logsOnly = order.logsOnly ?? false
    this.dug = 0; this.gained = 0; this.deposited = 0; this.returned = false; this.clean = true
    /** @type {Map<string,{gained:number,available:number}>} */
    this.itemGains = new Map()
    /** Digs below the bot whose item fell out of reach; collected after the return. @type {{position:Vec3,item:string}[]} */
    this.deferredDrops = []
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
      active: this.finishedAt === null, mode: this.order.mode ?? 'count', block: this.order.block,
      item: this.item ?? this.order.block, requested: this.order.count, chunk: this.chunk,
      before: this.before, available: this.available, dug: this.dug, gained: this.gained,
      items: [...this.itemGains.entries()].map(([item, gain]) => ({ item, gained: gain.gained, available: gain.available })),
      remaining: this.order.mode === 'tree' ? null : Math.max(0, this.order.count - this.gained), deposited: this.deposited, returned: this.returned,
      depositRequested: this.order.deposit, inventoryClean: this.clean,
      entry: { x: this.entry.x, y: this.entry.y, z: this.entry.z }, supplies: this.supplies,
      startedAt: this.startedAt, finishedAt: this.finishedAt }
  }

  /** Inventory item dropped by a target block; mixed orders resolve per block. */
  itemFor (blockName) { return this.mixed ? (soilSet.has(blockName) ? 'dirt' : blockName) : this.item }

  /** Surface-soil targets follow the dirt order rules (own support, descent). */
  soilHarvest (blockName) { return this.surfaceSoil || (this.mixed && soilSet.has(blockName)) }

  /** @param {string} item @param {number} delta @param {number} available */
  recordGain (item, delta, available) {
    const entry = this.itemGains.get(item) ?? { gained: 0, available: 0 }
    entry.gained += delta; entry.available = available
    this.itemGains.set(item, entry)
  }

  /** Current stock of what this order produces; mixed sums every touched item. */
  totalAvailable () {
    if (this.item) return itemCount(this.bot, this.item)
    let total = 0
    for (const item of this.itemGains.keys()) total += itemCount(this.bot, item)
    return total
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

  /** Chunk-clamped orders keep every dig inside one 16×16 chunk column. */
  inChunk (position) {
    if (!this.chunk) return true
    return Math.floor(position.x / 16) === this.chunk.x && Math.floor(position.z / 16) === this.chunk.z
  }

  /**
   * Mixed orders keep a dirt scaffolding floor: below it the chunk's top soil
   * layer outranks every tree until the backpack recovers.
   */
  dirtFloorUnmet () {
    return itemCount(this.bot, 'dirt') < tier1DirtFloor
  }

  /**
   * The chunk's current top soil layer: its highest eligible soil blocks, the
   * priority target while the scaffolding reserve floor is unmet.
   * @param {Vec3[]} positions
   * @returns {Vec3[]}
   */
  topSoilLayer (positions) {
    const soil = positions.filter(p => {
      if (!this.inChunk(p)) return false
      const block = this.bot.blockAt(p)
      return block != null && soilSet.has(block.name)
    })
    if (!soil.length) return []
    const top = Math.max(...soil.map(p => p.y))
    return soil.filter(p => p.y === top)
  }

  /** @param {GatherNavigation} navigation @param {Set<string>} [tried] @param {Map<string,Vec3>|null} [allowed] @param {Set<string>|null} [excluded] */
  async candidate (navigation, tried = new Set(), allowed = null, excluded = null) {
    const center = new Vec3(this.order.x, this.order.y, this.order.z)
    // Work faces: chunk-clamped soil is searched across its whole standard
    // 16×16 chunk column from the chunk center — never a bot-centered radius.
    // Trees keep a radius around the order center so trunks standing just
    // outside the chunk remain visible next to the dirt being dug.
    let positions = allowed ? [...allowed.values()] : []
    if (!allowed) {
      const idsOf = names => names.map(name => this.bot.registry.blocksByName[name].id)
      if (this.chunk && this.mixed) {
        const chunkCenter = new Vec3(this.chunk.x * 16 + 8, this.order.y, this.chunk.z * 16 + 8)
        positions = [
          ...this.bot.findBlocks({ matching: idsOf([...this.targetNames].filter(name => soilSet.has(name))),
            point: chunkCenter, maxDistance: 14, count: 512 }),
          ...this.bot.findBlocks({ matching: idsOf([...this.logNames]),
            point: center, maxDistance: this.order.radius, count: 64 }),
        ]
      } else if (this.chunk) {
        positions = this.bot.findBlocks({ matching: this.targetIds,
          point: new Vec3(this.chunk.x * 16 + 8, this.order.y, this.chunk.z * 16 + 8),
          maxDistance: 14, count: this.surfaceSoil ? 512 : 64 })
      } else {
        positions = this.bot.findBlocks({ matching: this.targetIds, point: center,
          maxDistance: this.order.radius, count: this.surfaceSoil || this.mixed ? 512 : 64 })
      }
    }
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
    // Mixed orders pick by distance to the bot's current feet, so the sweep
    // follows the bot across the face instead of zigzagging around the fixed
    // order center.
    if (!allowed && this.mixed) {
      const feet = this.bot.entity.position
      positions = [...positions].sort((a, b) => a.distanceTo(feet) - b.distanceTo(feet))
    }
    // Single-resource chunk orders clamp the whole authorized face to the
    // chunk column, producing chunk-aligned square clearing.
    if (!allowed && this.chunk && !this.mixed) positions = positions.filter(p => this.inChunk(p))
    if (!allowed && this.mixed && this.logsOnly) {
      // Dirt-quota orders never touch soil; the reserve floor is implied met.
      positions = positions.filter(p => {
        const block = this.bot.blockAt(p)
        return block == null || !soilSet.has(block.name)
      })
    } else if (!allowed && this.mixed && this.chunk) {
      const layer = this.topSoilLayer(positions)
      if (layer.length && this.dirtFloorUnmet()) {
        try {
          return await this.scan(navigation, layer, tried, allowed, excluded)
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          if (!message.includes('GATHER_NO_TARGET')) throw error
          // The chunk's top soil layer has no reachable candidate right now:
          // fall through to the ordinary scan. Soil outside the chunk is
          // never eligible; the worker relocates ring by ring instead.
        }
      } else {
        // A trunk a few blocks away preempts further soil: trees standing
        // right next to the sweep are harvested before it moves on.
        const feet = this.bot.entity.position
        const adjacent = positions.filter(p => {
          const block = this.bot.blockAt(p)
          return block != null && this.logNames.has(block.name) && p.distanceTo(feet) <= 6
        }).sort((a, b) => a.distanceTo(feet) - b.distanceTo(feet))
        if (adjacent.length) {
          try {
            return await this.scan(navigation, adjacent, tried, allowed, excluded)
          } catch (error) {
            const message = error instanceof Error ? error.message : String(error)
            if (!message.includes('GATHER_NO_TARGET')) throw error
            // The adjacent trunk has no reachable stand right now; continue
            // with the ordinary soil layer instead of stalling.
          }
        }
        // Dirt always digs the chunk's current top layer — square and layer
        // by layer — and nothing outside the chunk: an exhausted or barren
        // chunk ends the order so the worker walks to the next dirt chunk.
        const topLayer = new Set(layer.map(p => safety.key(p)))
        positions = positions.filter(p => {
          const block = this.bot.blockAt(p)
          return block == null || !soilSet.has(block.name) || topLayer.has(safety.key(p))
        })
      }
    }
    return this.scan(navigation, positions, tried, allowed, excluded)
  }

  /** @param {GatherNavigation} navigation @param {Vec3[]} positions @param {Set<string>} tried @param {Map<string,Vec3>|null} allowed @param {Set<string>|null} excluded */
  async scan (navigation, positions, tried, allowed, excluded) {
    for (const p of positions) {
      if (allowed && !allowed.has(safety.key(p))) continue
      if (excluded && excluded.has(safety.key(p))) continue
      this.check(navigation.signal)
      const block = this.bot.blockAt(p)
      if (!block || !this.targetNames.has(block.name)) continue
      const soil = this.soilHarvest(block.name)
      // A standing player is above a ground-level trunk log, so logs keep a
      // bounded lower band eligible for roots on a slope without treating the
      // supporting floor as an excavation target. Surface soil is the floor
      // itself: it may dig the block underfoot, and the layer-by-layer order
      // then descends only after the current level is fully cleared.
      const minY = soil ? Math.floor(this.bot.entity.position.y) - 1 : this.entry.y - 3
      if (p.y < minY || (p.y > this.entry.y + scaffoldHeight && !(allowed && allowed.has(safety.key(p))))) continue
      if (!safety.canHarvestBlock(this.bot, block, soil)) continue
      const modes = p.y > this.entry.y + 2 ? ['scaffold', 'high'] : ['leaf', 'high', 'scaffold']
      for (const mode of modes) {
        // Without reserve material in the inventory a scaffold route can
        // never be built; skip the mode instead of paying for a doomed
        // path calculation.
        if (mode === 'scaffold' && !navigation.scaffoldingIds().length) continue
        const feetY = Math.floor(this.bot.entity.position.y)
        const standYs = mode === 'scaffold'
          ? [Math.min(this.entry.y + scaffoldHeight, Math.max(this.entry.y + 1, p.y))]
          : [feetY - 1, feetY, feetY + 1]
        const stands = []
        for (let standY of standYs) for (let x = -1; x <= 1; x++) for (let z = -1; z <= 1; z++) {
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
          // Only a climb launched from ground level needs the full 64-dirt
          // reserve; a bot already elevated is mid-climb on placed supports
          // and continues with whatever it carries.
          const elevated = Math.floor(this.bot.entity.position.y) > this.entry.y + 2
          if (mode === 'scaffold' && !allowed && !this.surfaceSoil && !this.dirtReserveReady && !elevated) {
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
   * @param {Set<string>|null} [excluded]
   */
  async approach (navigation, signal, allowed = null, excluded = null) {
    const tried = this.triedApproaches
    // Whether any leg was abandoned to a server pull-back. If the bounded
    // rotations all fail after one, the whole neighborhood shares the terrain
    // desync and the task must surface GATHER_POSITION_FIGHT so the worker
    // relocates instead of rescanning the same poisoned area.
    let fought = false
    for (let attempt = 0; attempt < 8; attempt++) {
      this.check(signal); this.stage = 'searching'
      const { block, stand, mode } = await this.candidate(navigation, tried, allowed, excluded)
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
      if (this.bot.blockAt(block.position)?.type !== block.type || !safety.canHarvestBlock(this.bot, block, this.soilHarvest(block.name))) {
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
      // Canopy wrapping the harvest target itself is clearable even outside
      // the walking bands: the trunk cannot be dug through its own leaves.
      const wrapping = (isLeafBlockName(obstruction.name) || isVineName(obstruction.name)) &&
        hit.position.distanceTo(target.position) <= 2
      if (!obstruction || !(navigation.breakable(hit.position, mode, this.bot.entity.position.y) || wrapping) ||
          !safety.canHarvestBlock(this.bot, obstruction)) continue
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

  /**
   * Floating-log sweep: harvest leftovers hang in the air inside the work
   * chunk (partial harvests of earlier rounds). A component whose lowest log
   * has no support below is litter — it is collected whole before the
   * ordinary tiers, because cleanup is free wood. Grounded components are
   * intact trees and keep their normal priority.
   * @param {GatherNavigation} navigation @param {AbortSignal} signal
   * @param {Set<string>} excluded @param {() => boolean} budgetLeft
   */
  async sweepFloatingLogs (navigation, signal, excluded, budgetLeft) {
    if (!this.logNames.size) return
    const ids = [...this.logNames].map(name => this.bot.registry.blocksByName[name].id).filter(Number.isInteger)
    if (!ids.length) return
    const positions = this.bot.findBlocks({ matching: ids,
      point: new Vec3(this.chunk.x * 16 + 8, this.order.y, this.chunk.z * 16 + 8), maxDistance: 14, count: 64 })
    const visited = new Set()
    for (const position of positions) {
      this.check(signal)
      if (!this.inChunk(position) || visited.has(safety.key(position)) || excluded.has(safety.key(position))) continue
      const block = this.bot.blockAt(position)
      if (!block || !this.logNames.has(block.name)) continue
      const component = this.treeLogTargets(position, block.name)
      for (const key of component.scanned) visited.add(key)
      if (component.error) continue
      const logs = [...component.logs.values()]
      if (!logs.length) continue
      const lowest = logs.reduce((low, p) => (p.y < low.y ? p : low))
      const support = this.bot.blockAt(lowest.offset(0, -1, 0))
      if (support && support.boundingBox !== 'empty') continue
      const remaining = this.order.count - this.gained
      const tooBig = logs.length > remaining || logs.some(p => p.y > this.entry.y + scaffoldHeight)
      if (tooBig) {
        for (const key of component.scanned) excluded.add(key)
        this.reason = `GATHER_TREE_LIMIT: Floating component of ${logs.length} logs does not fit the budget ${remaining}; skipped whole.`
        continue
      }
      const dugBefore = this.dug
      const harvest = async () => {
        await this.collectOne(navigation, signal, component.logs)
        await this.collectTree(navigation, signal, component.logs, block.name)
      }
      try {
        await harvest()
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        const skippable = /GATHER_(?:NO_TARGET|NO_SIGHT)|GATHER_SCAFFOLD_RESERVE|Goal was changed|Digging aborted/.test(message) &&
          (message.includes('GATHER_SCAFFOLD_RESERVE') ? this.dug === dugBefore : true)
        if (!skippable) throw error
        // Hanging litter must not be abandoned on a transient miss: retry the
        // whole component once (the bot carries dirt precisely to pillar up
        // to it) before leaving it for the next order's sweep.
        try {
          await delay(250, undefined, { signal })
          await harvest()
        } catch (retryError) {
          const retryMessage = retryError instanceof Error ? retryError.message : String(retryError)
          if (this.dug !== dugBefore) throw retryError
          this.reason = `GATHER_FLOAT_STUCK: Hanging component at ${lowest.toString()} not harvestable (${retryMessage.slice(0, 60)}); left for the next sweep.`
          for (const key of component.scanned) excluded.add(key)
        }
      }
      if (!budgetLeft()) return
    }
  }

  /** Dismantled scaffolds drop their dirt around the pillar base: register
   * every visible drop so the post-return pass vacuums them instead of
   * walking away from reclaimed material. Reclaim entries are picked up
   * without crediting the order — scaffold dirt is the bot's own returned
   * material, never order output. */
  recordScaffoldDrops () {
    for (const entity of Object.values(this.bot.entities ?? {})) {
      if (entity.name !== 'item' || entity.isValid === false || !entity.position) continue
      if (entity.position.distanceTo(this.entry) > 8) continue
      const position = entity.position.clone().floored()
      if (this.deferredDrops.some(miss => miss.position.equals(position))) continue
      this.deferredDrops.push({ position, item: 'dirt', reclaimed: true })
    }
    if (this.deferredDrops.length > 24) this.deferredDrops.splice(0, this.deferredDrops.length - 24)
  }

  /** Ground-item sweep after the return: the visible item entities are the
   * record — anything lying near the work face gets walked over and vacuumed,
   * whether or not its drop was ever booked. Ledger entries keep their
   * per-item crediting for the order's count; unbooked finds and reclaimed
   * scaffold dirt are picked up as pure bonus without inflating progress.
   * @param {GatherNavigation} navigation @param {AbortSignal} signal */
  async collectDeferredDrops (navigation, signal) {
    const booked = this.deferredDrops.splice(0, 24)
    this.deferredDrops.length = 0
    const visit = new Map(booked.map(miss => [`${miss.position}`, miss]))
    for (const entity of Object.values(this.bot.entities ?? {})) {
      if (entity.name !== 'item' || entity.isValid === false || !entity.position) continue
      if (entity.position.distanceTo(this.entry) > 12) continue
      const position = entity.position.floored()
      if (!visit.has(`${position}`)) visit.set(`${position}`, { position, item: null })
    }
    for (const miss of [...visit.values()].slice(0, 24)) {
      this.check(signal)
      const stand = miss.position.floored()
      if (!navigation.safeRoutePoint(stand, 'leaf')) continue
      const before = miss.item != null && !miss.reclaimed ? itemCount(this.bot, miss.item) : 0
      try { await navigation.walk(stand, 'leaf') } catch { /* best effort */ }
      if (miss.item == null || miss.reclaimed) {
        // Pure vacuum and scaffold reclaim: walked over and picked up without
        // crediting the order.
        await delay(300, undefined, { signal })
        continue
      }
      const end = Date.now() + 2000
      while (itemCount(this.bot, miss.item) <= before && Date.now() < end) {
        this.check(signal); await delay(100, undefined, { signal })
      }
      const available = itemCount(this.bot, miss.item)
      const gained = Math.max(0, available - before)
      this.gained += gained
      this.available = this.totalAvailable()
      this.recordGain(miss.item, gained, available)
    }
  }

  /** The physical dig: a strip order descends by digging its own standing
   * floor; the strict temporary-descent gate (grounded, standing on the block,
   * solid landing below) revalidates everything the prechecks assumed.
   * @param {import('mineflayer').Block} block @param {boolean} soil @param {AbortSignal} signal */
  async digBlock (block, soil, signal) {
    if (soil && safety.isOwnSupport(this.bot, block.position)) await safety.descendTemporarySupport(this.bot, block, signal)
    else {
      await this.aimAtNearestFace(block, signal)
      await this.bot.dig(block, false)
    }
  }

  /** mineflayer's raycast dig aim drifts onto block edges or neighbours when
   * standing on fresh scaffolds; aim explicitly at a face center so the dig
   * packet targets the right block. Faces are verified by sight ray — the
   * geometrically nearest but occluded face is exactly what makes the first
   * dig fail and forces a slow stand rotation.
   * @param {import('mineflayer').Block} block @param {AbortSignal} signal */
  async aimAtNearestFace (block, signal) {
    const height = 'eyeHeight' in this.bot.entity ? this.bot.entity.eyeHeight : null
    if (typeof height !== 'number' || !Number.isFinite(height) || height <= 0) return
    const feet = this.bot.entity.position
    const eye = feet.offset(0, height, 0)
    const center = block.position.offset(0.5, 0.5, 0.5)
    const faces = []
    for (const axis of /** @type {const} */ (['x', 'y', 'z'])) {
      const delta = eye[axis] - center[axis]
      if (Math.abs(delta) <= 0.5) continue
      const face = center.clone()
      face[axis] += Math.sign(delta) * 0.499
      faces.push(face)
    }
    faces.sort((a, b) => a.distanceTo(eye) - b.distanceTo(eye))
    const visible = faces.find(face => face.distanceTo(eye) <= 4.5 && reachableFace(this.bot, block.position, feet))
    const aim = visible ?? faces[0]
    if (aim != null && aim.distanceTo(eye) <= 4.5) {
      try { await this.bot.lookAt(aim, true) } catch { /* aim is best effort */ }
      signal.throwIfAborted()
    }
  }

  /** mineflayer refuses digs whose face it cannot see (canopy or vines between
   * eye and target even though target selection hops them): clear one bounded
   * obstruction and retry once; still-blind digs surface as a recoverable
   * GATHER_NO_SIGHT so the stand rotation finds another angle.
   * @param {GatherNavigation} navigation @param {import('mineflayer').Block} block
   * @param {boolean} soil @param {string} mode @param {AbortSignal} signal */
  async digWithSightRetry (navigation, block, soil, mode, signal) {
    try {
      await this.digBlock(block, soil, signal)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      if (!message.includes('Block not in view')) throw error
      if (!await this.clearSightObstruction(navigation, block, mode, signal)) {
        throw new Error('GATHER_NO_SIGHT: No visible harvest face at the dig site.')
      }
      await this.digBlock(block, soil, signal)
    }
  }

  /** Strip the vines attached to a target's faces so the dig aims at real wood. */
  async clearVineWrap (block, signal) {
    for (const [dx, dy, dz] of [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]) {
      const cover = this.bot.blockAt(block.position.offset(dx, dy, dz))
      if (!cover || !isVineName(cover.name)) continue
      this.check(signal)
      try { await this.bot.dig(cover, true, 'raycast') } catch { /* vines are optional covers */ }
    }
  }

  /** @param {GatherNavigation} navigation @param {AbortSignal} signal @param {Map<string,Vec3>|null} [allowed] @param {Set<string>|null} [excluded] */
  async collectOne (navigation, signal, allowed = null, excluded = null) {
    this.check(signal)
    if (this.bot.inventory.emptySlotCount() < 2) throw new Error('GATHER_FULL: Keep two inventory slots free.')
    const { block, mode } = await this.approach(navigation, signal, allowed, excluded)
    await this.clearVineWrap(block, signal)
    const soil = this.soilHarvest(block.name)
    const item = this.itemFor(block.name)
    await this.bot.tool.equipForBlock(block, { requireHarvest: true, getFromChest: false })
    this.check(signal)
    if (this.bot.blockAt(block.position)?.type !== block.type || !reachableFace(this.bot, block.position, this.bot.entity.position) || !safety.canHarvestBlock(this.bot, block, soil)) {
      throw new Error('GATHER_TARGET_CHANGED: Target or sightline changed during tool selection; stopped before digging.')
    }
    const held = this.bot.heldItem
    if (!block.canHarvest(held?.type ?? null)) throw new Error('GATHER_TOOL: No suitable harvesting tool.')
    if (held) {
      const maximum = this.bot.registry.itemsByName[held.name]?.maxDurability
      if (maximum && maximum - held.durabilityUsed < 12) throw new Error('GATHER_TOOL: Tool durability too low; keep a reserve.')
      if (held.enchants.some(enchantment => enchantment.name === 'silk_touch') && (item === 'cobblestone' || item === 'dirt')) throw new Error('GATHER_TOOL: Silk touch would not produce the requested drop.')
    }
    const before = itemCount(this.bot, item)
    this.stage = 'digging'
    const timer = setTimeout(() => this.bot.stopDigging(), 15000)
    try {
      await this.digWithSightRetry(navigation, block, soil, mode, signal)
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
      if (itemCount(this.bot, item) <= before && this.bot.pathfinder?.goto && this.bot.pathfinder?.movements) {
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
      const elevated = Math.floor(this.bot.entity.position.y) > block.position.y + 1
      const end = Date.now() + (elevated ? 1500 : 6000)
      while (itemCount(this.bot, item) <= before && Date.now() < end) {
        this.check(signal); await delay(100, undefined, { signal })
      }
    } finally {
      // A vanished entity or a resolved dig promise is not an inventory receipt.
      const available = itemCount(this.bot, item)
      const delta = Math.min(1, Math.max(0, available - before))
      this.gained += delta
      this.available = this.totalAvailable()
      this.recordGain(item, delta, available)
    }
    if (itemCount(this.bot, item) <= before) {
      // A dig below the bot — or on a slope where the item rolls away —
      // drops its item out of reach: defer the pickup to the post-return
      // pass instead of failing the harvest whenever the drop is visible.
      const dropped = Object.values(this.bot.entities ?? {}).find(entity =>
        entity.name === 'item' && entity.isValid !== false && entity.position &&
        entity.position.distanceTo(block.position) <= 6)
      if (dropped || Math.floor(this.bot.entity.position.y) > block.position.y + 1) {
        this.deferredDrops.push({
          position: (dropped ? dropped.position.clone() : block.position.clone()).floored(),
          item,
        })
        if (this.deferredDrops.length > 16) this.deferredDrops.shift()
        return block.position.clone()
      }
      throw new Error('GATHER_NO_PICKUP: Block removed but no matching inventory gain; remaining excavation stopped.')
    }
    return block.position.clone()
  }

  /**
   * Scan the connected log component around a trunk anchor. Only adjacent
   * logs are traversed; shared leaves never join separate trunks. The scan
   * always runs to its bounds so a caller that rejects the tree can exclude
   * the whole component; envelope or scan-budget breaches come back as an
   * error string, with every discovered log still in `scanned`.
   * @param {Vec3} anchor @param {string} logName
   * @returns {{logs: Map<string,Vec3>, scanned: Set<string>, error: string}}
   */
  treeLogTargets (anchor, logName) {
    const pending = [anchor.clone()]
    const seen = new Set()
    const logs = new Map()
    const scanned = new Set()
    let error = ''
    const maxY = anchor.y + 16
    const maxHorizontal = 4
    while (pending.length && seen.size < 2048) {
      const position = pending.shift()
      const key = safety.key(position)
      if (seen.has(key)) continue
      seen.add(key)
      const block = this.bot.blockAt(position)
      if (!block) continue
      if (block.name !== logName) continue
      scanned.add(key)
      if (position.y < this.entry.y - 3 || position.y > maxY ||
          Math.hypot(position.x - anchor.x, position.z - anchor.z) > maxHorizontal) {
        error ||= 'GATHER_TREE_LIMIT: Connected logs exceed the bounded tree envelope; nothing was dug.'
      } else {
        logs.set(key, position.clone())
      }
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
        if (dx || dy || dz) pending.push(position.offset(dx, dy, dz))
      }
    }
    if (pending.length && seen.size >= 2048) {
      error ||= 'GATHER_TREE_LIMIT: Tree scan budget exhausted; nothing was dug.'
    }
    return { logs, scanned, error }
  }

  /**
   * Harvest the preflighted log component without discovering another tree mid-task.
   * @param {GatherNavigation} navigation @param {AbortSignal} signal
   * @param {Map<string,Vec3>} initialTargets @param {string} logName
   */
  async collectTree (navigation, signal, initialTargets, logName) {
    const known = new Map([...initialTargets.entries()].map(([key, position]) => [key, position.clone()]))
    for (let pass = 0; pass < 64; pass++) {
      this.check(signal)
      const targets = [...known.values()].filter(position => this.bot.blockAt(position)?.name === logName)
      if (!targets.length) return
      if (this.gained >= this.order.count) throw new Error('GATHER_TREE_LIMIT: Authorized log budget exhausted.')
      if (targets.some(position => position.y > this.entry.y + scaffoldHeight)) {
        throw new Error('GATHER_TREE_TOO_HIGH: Full tree cleanup needs a reachable scaffold above the bounded gathering height.')
      }
      const allowed = new Map(targets.map(position => [safety.key(position), position]))
      await this.collectOne(navigation, signal, allowed)
    }
    throw new Error('GATHER_TREE_LIMIT: Tree cleanup exceeded the bounded log budget.')
  }

  /**
   * Nearest-first mixed harvesting: soil blocks are dug one by one, log
   * targets are harvested as whole connected trees. A tree that cannot be
   * finished inside the remaining budget or the height cap is excluded whole
   * instead of started, so no floating trunk can be left behind.
   * @param {GatherNavigation} navigation @param {AbortSignal} signal
   */
  async collectMixed (navigation, signal) {
    const excluded = new Set()
    if (this.chunk) {
      await this.sweepFloatingLogs(navigation, signal, excluded, () => this.gained < this.order.count)
    }
    let recoveryAttempts = 0
    while (this.gained < this.order.count) {
      this.check(signal)
      let found
      try {
        found = await this.candidate(navigation, new Set(), null, excluded)
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        const recoverable = /GATHER_(?:NO_TARGET|NO_SIGHT|NO_PICKUP|TARGET_CHANGED|TRAVEL_TIMEOUT|NOT_ARRIVED)|MINING_PROTECTED_FLOOR|Goal was changed|Digging aborted/.test(message)
        if (!recoverable || recoveryAttempts >= 2) throw error
        recoveryAttempts++
        this.stage = 'recovering'
        this.reason = `Retrying after bounded gathering failure (${recoveryAttempts}/2): ${message}`
        await delay(250, undefined, { signal })
        continue
      }
      if (this.logNames.has(found.block.name)) {
        const component = this.treeLogTargets(found.block.position, found.block.name)
        const remaining = this.order.count - this.gained
        if (component.error || component.logs.size > remaining ||
            [...component.logs.values()].some(position => position.y > this.entry.y + scaffoldHeight)) {
          for (const key of component.scanned) excluded.add(key)
          this.stage = 'excluding_tree'
          this.reason = component.error ||
            `GATHER_TREE_LIMIT: Tree of ${component.logs.size} logs does not fit the remaining budget ${remaining}; skipped whole.`
          continue
        }
        const dugBefore = this.dug
        try {
          await this.collectOne(navigation, signal, component.logs)
          await this.collectTree(navigation, signal, component.logs, found.block.name)
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          // Climbing without the 64-dirt reserve aborts before the first dig:
          // skip this tree whole and keep filling the order with soil until
          // the reserve recovers. A partially harvested tree never takes this
          // path — once started, a component must be finished or the task
          // honestly reports blocked (no floating logs).
          if (message.includes('GATHER_SCAFFOLD_RESERVE') && this.dug === dugBefore) {
            for (const key of component.logs.keys()) excluded.add(key)
            this.stage = 'excluding_tree'
            this.reason = `${message}; skipped until the dirt reserve recovers.`
            recoveryAttempts = 0
            continue
          }
          throw error
        }
        recoveryAttempts = 0
        continue
      }
      try {
        await this.collectOne(navigation, signal, null, excluded)
        recoveryAttempts = 0
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        const recoverable = /GATHER_(?:NO_TARGET|NO_SIGHT|NO_PICKUP|TARGET_CHANGED|TRAVEL_TIMEOUT|NOT_ARRIVED)|MINING_PROTECTED_FLOOR|Goal was changed|Digging aborted/.test(message)
        if (!recoverable || recoveryAttempts >= 2) throw error
        recoveryAttempts++
        this.stage = 'recovering'
        this.reason = `Retrying after bounded gathering failure (${recoveryAttempts}/2): ${message}`
        await delay(250, undefined, { signal })
      }
    }
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
    // Chunk-clamped orders anchor stand bounds on the chunk center so corner
    // targets stay reachable from any edge of the square.
    const bounds = this.chunk
      ? new Vec3(this.chunk.x * 16 + 8, this.entry.y, this.chunk.z * 16 + 8)
      : null
    const navigation = new GatherNavigation(this.bot, this.entry, handle.signal, this.world, bounds)
    try {
      await yieldFrame(); this.check(handle.signal)
      if (!navigation.safe(this.entry)) throw new Error('GATHER_UNSAFE_ENTRY: Start on known level solid ground.')
      await navigation.restoreScaffolds()
      if (navigation.scaffolds.size) {
        this.stage = 'cleaning_scaffolds'
        try {
          await navigation.cleanupScaffolding()
        } catch (error) {
          // A stale support record without a safe approach right now must not
          // veto the whole order: the record stays persisted for a later
          // attempt, and digging the support block away clears it as well.
          console.error('[anelf] startup scaffold cleanup deferred:',
            String(error?.message ?? error))
        }
        this.stage = 'planned'
      }
      if (this.order.withdraw.length) { this.stage = 'resupplying'; await this.supply(false); this.check(handle.signal) }
      if (this.item) this.before = itemCount(this.bot, this.item)
      try {
        if (this.order.mode === 'tree') {
          const preview = await this.candidate(navigation)
          const component = this.treeLogTargets(preview.block.position, preview.block.name)
          if (component.error) {
            throw new Error(component.error)
          }
          if (component.logs.size > this.order.count) {
            throw new Error('GATHER_TREE_LIMIT: Complete tree exceeds the authorized log budget; nothing was dug.')
          }
          if ([...component.logs.values()].some(position => position.y > this.entry.y + scaffoldHeight)) {
            throw new Error('GATHER_TREE_TOO_HIGH: Full tree cleanup needs a reachable scaffold above the bounded gathering height.')
          }
          await this.collectOne(navigation, handle.signal, component.logs)
          await this.collectTree(navigation, handle.signal, component.logs, preview.block.name)
        } else if (this.mixed) {
          await this.collectMixed(navigation, handle.signal)
        } else {
          let recoveryAttempts = 0
          while (this.gained < this.order.count) {
            try {
              await this.collectOne(navigation, handle.signal)
              recoveryAttempts = 0
            } catch (error) {
              const message = error instanceof Error ? error.message : String(error)
              const recoverable = /GATHER_(?:NO_TARGET|NO_SIGHT|NO_PICKUP|TARGET_CHANGED|TRAVEL_TIMEOUT|NOT_ARRIVED)|MINING_PROTECTED_FLOOR|Goal was changed|Digging aborted/.test(message)
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
        try {
          await navigation.cleanupScaffolding()
        } catch (error) {
          // Same stale-record policy as startup cleanup: a record without a
          // safe approach right now stays persisted for a later task instead
          // of failing an otherwise completed order.
          console.error('[anelf] return scaffold cleanup deferred:',
            String(error?.message ?? error))
        }
        this.recordScaffoldDrops()
        this.returned = true
        await this.collectDeferredDrops(navigation, handle.signal)
        // The pickup sweep walks to ground items up to 12 blocks away: return
        // to the entry when a route exists, otherwise report the honest
        // position instead of claiming the return.
        if (this.bot.entity.position.distanceTo(this.entry) > 2.5) {
          try { await navigation.walk(this.entry, 'leaf') } catch { /* best effort */ }
          if (this.bot.entity.position.distanceTo(this.entry) > 2.5) this.returned = false
        }
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
      this.available = this.totalAvailable()
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
