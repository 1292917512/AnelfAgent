// @ts-check
/** Incremental excavation with a walkable, protected route back to the entrance. */
const { randomUUID } = require('node:crypto')
const { setTimeout: sleep, setImmediate: yieldFrame } = require('node:timers/promises')
const { Vec3 } = require('vec3')
const { Movements, goals } = require('mineflayer-pathfinder')
const { MiningError, key, protectFloor, restrictMovements } = require('./mining-safety.cjs')

/** @typedef {import('mineflayer').Bot & {tool: import('mineflayer-tool').Tool}} Bot */
/** @typedef {import('./mining-store.cjs').MineRecord} MineRecord */
/** @typedef {import('./mining-store.cjs').MiningStore} MiningStore */
/** @typedef {{locks:{begin:(name:string,onCancel:()=>void)=>{signal:AbortSignal,actionId?:string,origin?:MineRecord['origin'],release:(outcome?:MineRecord['phase'],reason?:string)=>void}},
 * events:{push:(type:string,data:unknown)=>unknown}}} Context */
/** @typedef {{x:number,y:number,z:number}} Position */
/** @typedef {{direction:MineRecord['direction'],depth:number,length:number,item:string,count:number,minY?:number}} MineOptions */
const natural = new Set(['stone', 'deepslate', 'dirt', 'grass_block', 'granite', 'diorite', 'andesite', 'tuff',
  'calcite', 'coal_ore', 'iron_ore', 'copper_ore', 'gold_ore', 'redstone_ore', 'lapis_ore', 'diamond_ore',
  'emerald_ore', 'deepslate_coal_ore', 'deepslate_iron_ore', 'deepslate_copper_ore', 'deepslate_gold_ore',
  'deepslate_redstone_ore', 'deepslate_lapis_ore', 'deepslate_diamond_ore', 'deepslate_emerald_ore'])
const air = new Set(['air', 'cave_air', 'void_air', 'torch', 'wall_torch'])
const hazards = new Set(['water', 'lava', 'sand', 'red_sand', 'gravel', 'powder_snow', 'fire', 'soul_fire'])
const directions = { north: [0, -1], south: [0, 1], east: [1, 0], west: [-1, 0] }

/** @param {Position} p */
function vec (p) { return new Vec3(p.x, p.y, p.z) }
/** @param {Position} p @returns {Position} */
function plain (p) { return { x: p.x, y: p.y, z: p.z } }
/** Lowest permitted feet/block Y, leaving a buffer above the dimension's bottom.
 * @param {Bot} bot @param {number} [requested] @returns {number}
 */
function minimumMiningY (bot, requested) {
  // Mineflayer supplies minY from dimension data; its GameState type omits it.
  const bottom = 'minY' in bot.game ? bot.game.minY : undefined
  if (typeof bottom !== 'number' || !Number.isSafeInteger(bottom)) {
    throw new MiningError('MINING_UNKNOWN_BOUNDS', 'World minimum Y is unknown; do not excavate until dimension data is available.')
  }
  return Math.max(bottom + 10, requested ?? -Infinity)
}
/** @param {MineRecord} record @returns {Position[]} */
function plan (record) {
  const [dx, dz] = directions[record.direction]
  return Array.from({ length: record.depth + record.length + 1 }, (_, i) => ({
    x: record.entry.x + dx * i, y: record.entry.y - Math.min(i, record.depth), z: record.entry.z + dz * i
  }))
}
/** @param {Bot} bot @param {string} item */
function inventoryCount (bot, item) {
  return bot.inventory.items().filter(i => i.name === item).reduce((total, i) => total + i.count, 0)
}
/** @param {Bot} bot @param {MineOptions} options @param {string} world @returns {MineRecord} */
function newRecord (bot, options, world) {
  const entry = plain(bot.entity.position.floored())
  const minY = minimumMiningY(bot, options.minY)
  if (entry.y < minY) throw new MiningError('MINING_MIN_Y', `Entrance Y=${entry.y} is below the mining limit Y=${minY}; move higher before starting a mine.`)
  const depth = Math.min(options.depth, entry.y - minY)
  if (depth + options.length === 0) throw new MiningError('MINING_MIN_Y', `Already at mining limit Y=${minY}; no downward excavation is allowed. Request a horizontal passage or return.`)
  return { version: 1, id: randomUUID(), world, entry, route: [entry], ...options,
    depth, requestedDepth: options.depth, minY,
    protectedFloors: [], baseline: inventoryCount(bot, options.item), gained: 0, dug: 0, steps: 0,
    phase: 'running', returned: false, reason: '', updatedAt: new Date().toISOString() }
}

class MineTask {
  /** @param {Context} ctx @param {Bot} bot @param {MineRecord} record @param {MiningStore} store */
  constructor (ctx, bot, record, store) {
    this.ctx = ctx
    this.bot = bot
    this.record = record
    this.store = store
    this.controller = new AbortController()
    this.dimension = bot.game.dimension
    this.started = Date.now()
    this.done = Promise.resolve()
    this.active = false
    this.returnRequested = false
    this.pauseRequested = false
    this.lastProgress = 0
    this.validateRecord()
    this.protectRoute()
  }

  validateRecord () {
    const expected = plan(this.record)
    if (this.record.steps !== this.record.route.length - 1 || this.record.route.some((p, i) => !expected[i] || key(p) !== key(expected[i]))) {
      throw new MiningError('MINING_CHECKPOINT_INVALID', 'Mine checkpoint does not match its corridor plan.')
    }
  }

  protectRoute () {
    for (const p of this.record.protectedFloors) protectFloor(this.bot, p)
    for (const p of this.record.route) protectFloor(this.bot, vec(p).offset(0, -1, 0))
  }

  check () {
    this.controller.signal.throwIfAborted()
    if (this.bot.game.dimension !== this.dimension || this.bot.health <= 0) {
      throw new MiningError('MINING_INTERRUPTED', 'The bot died or changed dimension; excavation has stopped.')
    }
    if (Date.now() - this.started > 10 * 60 * 1000) {
      throw new MiningError('MINING_TIMEOUT', 'Mining time budget reached; no further excavation is allowed.')
    }
  }

  stopMotion () {
    this.bot.pathfinder.setGoal(null)
    this.bot.stopDigging()
    this.bot.clearControlStates()
  }

  /** @param {string} reason */
  cancel (reason) {
    if (!this.active) return
    this.controller.abort(new MiningError('MINING_CANCELLED', reason))
    this.stopMotion()
  }

  pause () {
    if (!this.active) return
    this.pauseRequested = true
    this.record.phase = 'pausing'
    this.record.reason = 'Pause requested; finishing the current safe corridor step.'
    this.ctx.events.push('mine_progress', this.snapshot())
  }

  checkPause () {
    this.check()
    if (this.pauseRequested) throw new MiningError('MINING_PAUSED', 'Paused at a verified corridor checkpoint; explicit resume is required.')
  }

  /** @param {boolean} [returnOnly] */
  start (returnOnly = false) {
    if (this.active) throw new MiningError('MINING_BUSY', 'A mine task is already running.')
    this.active = true
    this.done = this.execute(returnOnly).catch(error => {
      this.record.phase = 'blocked'
      this.record.reason = `Checkpoint or cleanup failed: ${error instanceof Error ? error.message : String(error)}`
      this.ctx.events.push('mine_progress', this.snapshot())
    }).finally(() => { this.active = false })
    return this.snapshot()
  }

  snapshot () {
    return { ...this.record, route: undefined, protectedFloors: undefined, active: this.active,
      position: plain(this.bot.entity.position), returnRequested: this.returnRequested }
  }

  async checkpoint () {
    this.record.gained = Math.max(0, inventoryCount(this.bot, this.record.item) - this.record.baseline)
    this.record.updatedAt = new Date().toISOString()
    await this.store.save(this.record)
    if (Date.now() - this.lastProgress >= 3000 || !['running', 'returning'].includes(this.record.phase)) {
      this.ctx.events.push('mine_progress', this.snapshot())
      this.lastProgress = Date.now()
    }
  }

  /** @param {Position} p @returns {import('prismarine-block').Block} */
  block (p) {
    const block = this.bot.blockAt(vec(p))
    if (!block) throw new MiningError('MINING_UNLOADED', `Terrain at ${key(p)} is not loaded; do not excavate blind.`)
    return block
  }

  /** @param {Position} p */
  inspectColumn (p) {
    const floor = this.block(vec(p).offset(0, -1, 0))
    if (hazards.has(floor.name) || floor.boundingBox !== 'block' || floor.shapes.length !== 1 ||
        floor.shapes[0].some((v, i) => v !== [0, 0, 0, 1, 1, 1][i])) {
      throw new MiningError('MINING_UNSAFE_FLOOR', `No stable full-block floor at ${floor.position}; stop before the drop.`)
    }
    for (let y = 0; y < 4; y++) {
      const at = vec(p).offset(0, y, 0)
      const block = this.block(at)
      if (hazards.has(block.name)) throw new MiningError('MINING_HAZARD', `Hazard ${block.name} at ${at}.`)
      if (y < 3 && !air.has(block.name) && !natural.has(block.name)) {
        throw new MiningError('MINING_PROTECTED_BLOCK', `Unexpected ${block.name} at ${at}; do not excavate buildings or containers.`)
      }
      if (y < 3) {
        for (const [dx, dz] of Object.values(directions)) {
          const neighbor = this.block(at.offset(dx, 0, dz))
          if (neighbor.name === 'water' || neighbor.name === 'lava') {
            throw new MiningError('MINING_HAZARD', `${neighbor.name} beside the planned opening at ${neighbor.position}.`)
          }
        }
      }
    }
  }

  /** @param {Position[]} route */
  validateRoute (route) {
    for (const p of route) {
      this.inspectColumn(p)
      for (let y = 0; y < 3; y++) {
        if (!air.has(this.block(vec(p).offset(0, y, 0)).name)) {
          throw new MiningError('MINING_RETURN_BLOCKED', `Return passage obstructed at ${key(p)}. Excavation stopped.`)
        }
      }
    }
  }

  /** @param {Position[]} route */
  movements (route) {
    const m = restrictMovements(new Movements(this.bot))
    // Restrict the search to this corridor, including head/floor checks made by Movements.
    m.exclusionAreasStep.push(block => route.some(p => p.x === block.position.x && p.z === block.position.z &&
      block.position.y >= p.y - 1 && block.position.y <= p.y + 3) ? 0 : 100)
    return m
  }

  /** @param {Position} from @param {Position} to @param {Position[]} route */
  async verifyPath (from, to, route) {
    this.validateRoute(route)
    const search = this.bot.pathfinder.getPathFromTo(this.movements(route), vec(from), new goals.GoalBlock(to.x, to.y, to.z), {
      timeout: 1500, tickTimeout: 10, searchRadius: 80, optimizePath: false
    })
    for (const { result } of search) {
      this.check()
      // Runtime yields partial searches; the upstream declaration omits this status.
      if (String(result.status) === 'partial') { await yieldFrame(); continue }
      if (result.status !== 'success' || result.path.some(step => step.toBreak.length || step.toPlace.length)) {
        throw new MiningError('MINING_NO_RETURN_PATH', `No verified walking route from ${key(from)} to ${key(to)} (${result.status}); digging and scaffolding are forbidden for return travel.`)
      }
      return
    }
    throw new MiningError('MINING_NO_RETURN_PATH', 'Path search did not finish successfully.')
  }

  /** @template T @param {() => Promise<T>} work @returns {Promise<T>} */
  async action (work) {
    this.check()
    const timer = setTimeout(() => this.cancel('Mining action timed out; movement stopped. Inspect the mine before resuming.'), 20000)
    try {
      const value = await work()
      this.check()
      return value
    } finally { clearTimeout(timer) }
  }

  /** @param {Position} destination @param {Position[]} route */
  async walk (destination, route) {
    await this.verifyPath(this.bot.entity.position, destination, route)
    this.bot.pathfinder.setMovements(this.movements(route))
    await this.action(() => this.bot.pathfinder.goto(new goals.GoalBlock(destination.x, destination.y, destination.z)))
    if (key(this.bot.entity.position) !== key(destination)) {
      throw new MiningError('MINING_NOT_ARRIVED', 'The server position does not match the requested mine step.')
    }
  }

  resources () {
    if (this.bot.health < 12 || this.bot.food < 10) throw new MiningError('MINING_LOW_SUPPLIES', 'Health or food is low; return before further excavation.')
    if (this.bot.inventory.emptySlotCount() < 2) throw new MiningError('MINING_FULL', 'Keep two inventory slots free; return to unload.')
    const reserve = this.bot.inventory.items().filter(i => ['cobblestone', 'dirt', 'cobbled_deepslate'].includes(i.name) || i.name.endsWith('_planks')).reduce((n, i) => n + i.count, 0)
    if (reserve < 8) throw new MiningError('MINING_LOW_SUPPLIES', 'Bring at least 8 spare dirt, cobblestone or planks before mining.')
    if (this.record.depth + this.record.length >= 4 && inventoryCount(this.bot, 'torch') < 1) {
      throw new MiningError('MINING_LOW_SUPPLIES', 'Bring torches for the underground passage before continuing.')
    }
  }

  /** @param {Position} p */
  assertMiningHeight (p) {
    const limit = minimumMiningY(this.bot, this.record.minY)
    if (p.y < limit || Math.floor(this.bot.entity.position.y) < limit) {
      throw new MiningError('MINING_MIN_Y', `Mining limit Y=${limit} reached; do not excavate below it. Return along the saved passage.`)
    }
  }

  /** @param {Position} p */
  async excavate (p) {
    this.assertMiningHeight(p)
    this.resources()
    this.inspectColumn(p)
    // Clear headroom first, the lowest block last; never stand on this column while digging it.
    for (let y = 2; y >= 0; y--) {
      this.check()
      this.inspectColumn(p)
      const block = this.block(vec(p).offset(0, y, 0))
      if (air.has(block.name)) continue
      await this.action(() => this.bot.tool.equipForBlock(block, { requireHarvest: true, getFromChest: false }))
      const item = this.bot.heldItem
      if (item) {
        const maximum = this.bot.registry.itemsByName[item.name]?.maxDurability
        if (maximum && maximum - item.durabilityUsed < 12) throw new MiningError('MINING_TOOL_LOW', 'Tool durability is low; keep a reserve and return for a replacement.')
      }
      await this.action(() => {
        this.assertMiningHeight(p)
        return this.bot.dig(block, true, 'raycast')
      })
      if (!air.has(this.block(block.position).name)) throw new MiningError('MINING_UNCONFIRMED', `Removal not confirmed at ${block.position}.`)
      this.record.dug++
    }
    const route = [...this.record.route, plain(p)]
    await this.verifyPath(p, this.record.entry, route)
    protectFloor(this.bot, vec(p).offset(0, -1, 0))
    // Save before moving: a disconnect during movement still retains the new safe step.
    this.record.route = route
    this.record.steps++
    await this.checkpoint()
    await this.walk(p, route)
    await sleep(600, undefined, { signal: this.controller.signal })
    if (this.record.steps % 4 === 0) await this.light()
    await this.checkpoint()
  }

  async light () {
    const p = this.record.route[Math.max(0, this.record.route.length - 2)]
    const target = this.block(p)
    if (target.name === 'torch') return
    const torch = this.bot.inventory.items().find(i => i.name === 'torch')
    if (!torch) throw new MiningError('MINING_LOW_SUPPLIES', 'Torches depleted; return before extending the mine.')
    await this.action(() => this.bot.equip(torch, 'hand'))
    await this.action(() => this.bot.placeBlock(this.block(vec(p).offset(0, -1, 0)), new Vec3(0, 1, 0)))
    if (this.block(p).name !== 'torch') throw new MiningError('MINING_LIGHT_FAILED', 'Server did not confirm the torch placement.')
  }

  async returnToEntry () {
    this.returnRequested = true
    this.record.phase = 'returning'
    await this.checkpoint()
    this.validateRoute(this.record.route)
    // Visit recorded steps, so the returned result is backed by actual traversal.
    let nearest = 0
    let distance = Infinity
    this.record.route.forEach((p, i) => {
      const d = this.bot.entity.position.distanceTo(vec(p).offset(0.5, 0, 0.5))
      if (d < distance) { distance = d; nearest = i }
    })
    if (distance > 2) throw new MiningError('MINING_OFF_ROUTE', 'Bot is outside the saved mine corridor; locate the entrance or last safe step first.')
    for (let i = nearest; i >= 0; i--) {
      this.checkPause()
      await this.walk(this.record.route[i], this.record.route)
    }
    this.record.returned = key(this.bot.entity.position) === key(this.record.entry)
    if (!this.record.returned) throw new MiningError('MINING_NOT_RETURNED', 'Entrance arrival was not confirmed.')
  }

  /** @param {boolean} returnOnly */
  async execute (returnOnly) {
    const handle = this.ctx.locks.begin('mine_resources', () => this.cancel('Cancelled by player, shutdown or another action.'))
    this.record.actionId = handle.actionId
    this.record.origin = handle.origin
    this.record.createdBy ??= handle.origin
    const interrupted = () => this.cancel('Connection ended, dimension changed or bot died; explicit resume is required.')
    this.bot.once('end', interrupted)
    this.bot.once('death', interrupted)
    this.bot.on('respawn', interrupted)
    const originalMovements = this.bot.pathfinder.movements
    try {
      this.stopMotion()
      this.record.returned = false
      this.record.reason = ''
      this.record.phase = returnOnly ? 'returning' : 'running'
      await this.checkpoint()
      this.validateRoute(this.record.route)
      if (!returnOnly) {
        this.resources()
        const route = plan(this.record)
        const last = this.record.route[this.record.route.length - 1]
        if (this.bot.entity.position.distanceTo(vec(last).offset(0.5, 0, 0.5)) > 1.5) {
          await this.walk(last, this.record.route)
        }
        for (let i = this.record.steps + 1; i < route.length; i++) {
          this.checkPause()
          if (this.returnRequested || (i > this.record.depth && this.record.count > 0 && this.record.gained >= this.record.count)) break
          await this.excavate(route[i])
        }
      }
      this.checkPause()
      await this.returnToEntry()
      const complete = this.record.count === 0 ? this.record.steps === this.record.depth + this.record.length
        : this.record.steps >= this.record.depth && this.record.gained >= this.record.count
      this.record.phase = complete ? 'completed' : 'partial'
      this.record.reason = this.record.phase === 'partial' ? 'Passage limit or early return reached before collecting the requested inventory gain.' : ''
    } catch (error) {
      this.record.reason = error instanceof Error ? error.message : String(error)
      if (error instanceof MiningError && error.code === 'MINING_PAUSED') {
        this.record.phase = 'paused'
      } else if (this.controller.signal.aborted) {
        this.record.phase = 'cancelled'
      } else {
        this.record.phase = 'blocked'
        try { await this.returnToEntry() } catch (returnError) {
          this.record.reason += ` Return failed: ${returnError instanceof Error ? returnError.message : String(returnError)}`
        }
        this.record.phase = 'blocked'
      }
    } finally {
      this.stopMotion()
      this.bot.pathfinder.setMovements(restrictMovements(originalMovements))
      this.bot.removeListener('end', interrupted)
      this.bot.removeListener('death', interrupted)
      this.bot.removeListener('respawn', interrupted)
      try { await this.checkpoint() } finally { handle.release(this.record.phase, this.record.reason) }
    }
  }
}

module.exports = { MineTask, newRecord, plan, inventoryCount }
