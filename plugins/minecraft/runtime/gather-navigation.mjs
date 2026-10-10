// @ts-check
/** Bounded walking on known, level ground with explicitly bounded obstacle clearing. */
import { setImmediate as yieldFrame, setTimeout as delay } from 'node:timers/promises'
import { Vec3 } from 'vec3'
import pathfinder from 'mineflayer-pathfinder'
import safety from 'mineflayer/lib/anelf_mining/mining-safety.cjs'
import { clear, safeStanding, solid, occupied } from '../bot/anelf-survival-observation.mjs'

const { Movements, goals } = pathfinder
const routeModes = /** @type {const} */ (['leaf', 'high', 'scaffold'])
/** @typedef {typeof routeModes[number]} RouteMode */

/** @param {unknown} name */
export function isLeafBlockName (name) {
  return typeof name === 'string' && /^[a-z0-9]+(?:_[a-z0-9]+)*_leaves$/.test(name)
}

/** Check exposed full-block faces from actual or proposed feet using the installed world raycaster.
 * A hidden block center does not imply that all of its harvestable faces are hidden.
 * @param {import('mineflayer').Bot} bot @param {Vec3} position @param {Vec3} feet
 */
export function reachableFace (bot, position, feet) {
  const height = 'eyeHeight' in bot.entity ? bot.entity.eyeHeight : null
  if (typeof height !== 'number' || !Number.isFinite(height) || height <= 0) return false
  const eye = feet.offset(0, height, 0), center = position.offset(0.5, 0.5, 0.5)
  for (const axis of /** @type {const} */ (['x', 'y', 'z'])) {
    const delta = eye[axis] - center[axis]
    if (Math.abs(delta) <= 0.5) continue
    const target = center.clone()
    target[axis] += Math.sign(delta) * 0.499
    const direction = target.minus(eye)
    if (direction.norm() > 4) continue
    const hit = bot.world.raycast(eye, direction.normalize(), 4)
    if (hit && 'position' in hit && hit.position instanceof Vec3 && hit.position.equals(position)) return true
  }
  return false
}

export class GatherNavigation {
  /** @param {import('mineflayer').Bot} bot @param {Vec3} entry @param {AbortSignal} signal */
  constructor (bot, entry, signal) { this.bot = bot; this.entry = entry; this.signal = signal; this.scaffolds = new Map() }

  /** @param {{x:number,y:number,z:number}} p @param {RouteMode} mode @param {number} [feetY] */
  breakable (p, mode, feetY = this.entry.y) {
    const point = new Vec3(p.x, p.y, p.z).floored()
    const block = this.bot.blockAt(point)
    if (mode === 'leaf') return point.y >= this.entry.y && point.y <= this.entry.y + 1 && isLeafBlockName(block?.name)
    const currentFeetY = Math.floor(feetY)
    if (point.y < currentFeetY + 1 || point.y > currentFeetY + 2) return false
    return Boolean(block && block.boundingBox === 'block')
  }

  /** @param {{x:number,y:number,z:number}} p @param {RouteMode} mode */
  leafBreakable (p, mode = 'leaf') { return this.breakable(p, mode) }

  /** @param {Array<{x:number,y:number,z:number}>} blocks @param {RouteMode} mode @param {number} [feetY] */
  allowedBreaks (blocks, mode, feetY = this.entry.y) { return blocks.every(block => this.breakable(block, mode, feetY)) }

  /** @param {{x:number,y:number,z:number}} p @param {RouteMode} mode */
  safeRoutePoint (p, mode) {
    const point = new Vec3(p.x, p.y, p.z).floored()
    if (point.distanceTo(this.entry) > 16 || occupied(this.bot, point, true)) return false
    if (mode === 'scaffold' && point.y >= this.entry.y + 1 && point.y <= this.entry.y + 3) {
      const feet = this.bot.blockAt(point), head = this.bot.blockAt(point.offset(0, 1, 0))
      const floor = this.bot.blockAt(point.offset(0, -1, 0))
      return Boolean(floor) && (clear(feet) || isLeafBlockName(feet?.name)) && (clear(head) || isLeafBlockName(head?.name))
    }
    // A tree root can sit several blocks below the bot on a gentle slope.
    // Allow bounded descent through existing solid steps; floor blocks are
    // still never breakable by this navigation mode.
    const minY = mode === 'high' ? this.entry.y - 3 : this.entry.y
    const maxY = mode === 'high' ? this.entry.y + 1 : this.entry.y
    if (point.y < minY || point.y > maxY) return false
    const floor = this.bot.blockAt(point.offset(0, -1, 0))
    const feet = this.bot.blockAt(point), head = this.bot.blockAt(point.offset(0, 1, 0))
    const openFeet = clear(feet) || isLeafBlockName(feet?.name)
    const openHead = clear(head) || isLeafBlockName(head?.name) || (mode !== 'leaf' && head?.boundingBox === 'block')
    return solid(floor) && openFeet && openHead
  }

  /** @param {{x:number,y:number,z:number}} p */
  safe (p) {
    const point = new Vec3(p.x, p.y, p.z).floored()
    return point.y === this.entry.y && point.distanceTo(this.entry) <= 16 &&
      safeStanding(this.bot, point) && !occupied(this.bot, point, true)
  }

  /** @param {RouteMode} mode */
  movements (mode = 'leaf') {
    const movement = new Movements(this.bot)
    movement.anelfGatherMode = mode
    if (mode === 'scaffold') {
      movement.anelfScaffoldingBlocks = this.scaffoldingIds()
    }
    safety.restrictMovements(movement)
    movement.blocksCantBreak ??= new Set()
    if (mode === 'leaf') for (const [name, definition] of Object.entries(this.bot.registry?.blocksByName ?? {})) {
      if (!isLeafBlockName(name) && Number.isInteger(definition?.id)) movement.blocksCantBreak.add(definition.id)
    }
    movement.exclusionAreasStep.push(block => {
      const p = block.position
      // mineflayer-pathfinder asks this hook about the floor, feet and head
      // blocks around a route point. Checking the queried block itself would
      // reject every otherwise valid point because its floor is one block low.
      const routeYs = mode === 'high'
        ? [this.entry.y - 3, this.entry.y - 2, this.entry.y - 1, this.entry.y, this.entry.y + 1]
        : mode === 'scaffold' ? [this.entry.y, this.entry.y + 1, this.entry.y + 2, this.entry.y + 3]
          : [this.entry.y, this.entry.y + 1]
      const allowed = routeYs.some(y => {
        const point = new Vec3(p.x, y, p.z)
        return p.y >= y - 1 && p.y <= y + 2 && this.safeRoutePoint(point, mode)
      })
      return allowed ? 0 : 100
    })
    return movement
  }

  scaffoldingIds () {
    const names = ['dirt', 'cobblestone', 'netherrack', 'stone', 'oak_planks']
    const ids = []
    for (const name of names) {
      const item = this.bot.registry?.itemsByName?.[name]
      if (item && Number.isInteger(item.id) && this.bot.inventory?.items?.().some(entry => entry.name === name)) ids.push(item.id)
    }
    return ids
  }

  /** @param {Vec3} from @param {Vec3} to @param {RouteMode} mode */
  async reachable (from, to, mode = 'leaf') {
    if (!this.safeRoutePoint(from, mode) || !this.safeRoutePoint(to, mode)) return false
    if (mode === 'scaffold' && !this.scaffoldingIds().length) return false
    const search = this.bot.pathfinder.getPathFromTo(this.movements(mode), from, new goals.GoalBlock(to.x, to.y, to.z), {
      timeout: 200, tickTimeout: 10, searchRadius: 32, optimizePath: false,
    })
    for (const { result } of search) {
      this.signal.throwIfAborted()
      if (String(result.status) === 'partial') { await yieldFrame(); continue }
      return result.status === 'success' && result.path.every(p => this.safeRoutePoint(p, mode) && this.allowedBreaks(p.toBreak, mode, p.y) &&
        (mode !== 'scaffold' ? !p.toPlace.length : p.toPlace.every(place => {
          const y = Math.floor(place.y)
          return y >= this.entry.y && y <= this.entry.y + 2
        })))
    }
    return false
  }

  /** @param {Vec3} to @param {RouteMode} mode */
  async walk (to, mode = 'leaf') {
    this.signal.throwIfAborted()
    if (!await this.reachable(this.bot.entity.position, to, mode)) throw new Error(`GATHER_NO_ROUTE: No confirmed ${mode} route.`)
    this.signal.throwIfAborted()
    this.bot.pathfinder.setMovements(this.movements(mode))
    const stop = () => this.bot.pathfinder.setGoal(null)
    let unsafe = false
    /** @param {import('mineflayer-pathfinder').PartiallyComputedPath} result */
    const validate = result => {
      if (mode === 'scaffold') for (const place of result.path.flatMap(step => step.toPlace ?? [])) {
        const point = new Vec3(place.x, place.y, place.z).floored()
        if (point.y >= this.entry.y && point.y <= this.entry.y + 2) this.scaffolds.set(`${point.x},${point.y},${point.z}`, point)
      }
      if (result.path.some(p => !this.safeRoutePoint(p, mode) || !this.allowedBreaks(p.toBreak, mode, p.y) ||
        (mode !== 'scaffold' ? p.toPlace.length : p.toPlace.some(place => {
          const y = Math.floor(place.y)
          return y < this.entry.y || y > this.entry.y + 2
        })))) {
        unsafe = true; result.path.length = 0; stop()
      }
    }
    this.bot.on('path_update', validate)
    this.signal.addEventListener('abort', stop, { once: true })
    const timeout = new AbortController()
    const motion = this.bot.pathfinder.goto(new goals.GoalBlock(to.x, to.y, to.z))
    try {
      await Promise.race([motion, delay(15000, undefined, { signal: timeout.signal }).then(() => { throw new Error('GATHER_TRAVEL_TIMEOUT: Walking timed out.') })])
      this.signal.throwIfAborted()
      if (unsafe || !this.safeRoutePoint(to, mode) || safety.key(this.bot.entity.position) !== safety.key(to)) throw new Error('GATHER_NOT_ARRIVED: Safe arrival was not confirmed.')
    } finally {
      timeout.abort(); stop()
      await motion.catch(() => {})
      this.signal.removeEventListener('abort', stop)
      this.bot.removeListener('path_update', validate)
    }
  }

  async cleanupScaffolding () {
    if (!this.scaffolds.size) return
    if (this.bot.entity.position.floored().y !== this.entry.y) await this.walk(this.entry, 'scaffold')
    const supports = [...this.scaffolds.values()].sort((a, b) => b.y - a.y)
    for (const point of supports) {
      if (this.bot.blockAt(point)?.boundingBox === 'empty') continue
      const stand = [point.offset(1, 0, 0), point.offset(-1, 0, 0), point.offset(0, 0, 1), point.offset(0, 0, -1)]
        .find(candidate => this.safe(candidate))
      if (!stand) throw new Error('GATHER_SCAFFOLD_CLEANUP: No safe side position to remove temporary support.')
      await this.walk(stand, 'leaf')
      const block = this.bot.blockAt(point)
      if (!block || block.boundingBox === 'empty') continue
      await this.bot.dig(block, true, 'raycast')
      if (this.bot.blockAt(point)?.boundingBox !== 'empty') throw new Error('GATHER_SCAFFOLD_CLEANUP: Temporary support removal was not confirmed.')
    }
    this.scaffolds.clear()
  }
}
