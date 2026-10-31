// @ts-check
/** Bounded walking on known, level ground with explicitly bounded obstacle clearing. */
import { setImmediate as yieldFrame, setTimeout as delay } from 'node:timers/promises'
import { createHash } from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'
import { Vec3 } from 'vec3'
import pathfinder from 'mineflayer-pathfinder'
import safety from 'mineflayer/lib/anelf_mining/mining-safety.cjs'
import { clear, safeStanding, solid, occupied } from '../bot/anelf-survival-observation.mjs'

const { Movements, goals } = pathfinder
const routeModes = /** @type {const} */ (['leaf', 'high', 'scaffold'])
/** @typedef {typeof routeModes[number]} RouteMode */
// Tree-climbing scaffold band: 16 reaches tall jungle trunks and the hanging
// crowns of earlier partial harvests while every placed block stays inside
// the recorded, dismantled-on-return support budget.
export const scaffoldHeight = 16
/** @typedef {{position:Vec3,type:number,entry?:{x:number,y:number,z:number}}} Support */

/** @param {unknown} name */
export function isLeafBlockName (name) {
  return typeof name === 'string' && /^[a-z0-9]+(?:_[a-z0-9]+)*_leaves$/.test(name)
}

/** Pathfinder placement coordinates identify the reference block, followed by its face offset.
 * @param {{x:number,y:number,z:number,dx:number,dy:number,dz:number}} place
 */
function placementTarget (place) { return new Vec3(place.x + place.dx, place.y + place.dy, place.z + place.dz).floored() }

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
    if (raycastThroughCovers(bot, eye, direction.normalize(), 4, position)) return true
  }
  return false
}

/** Ray to the target, hopping over collision-less covers (leaf litter, grass, vines).
 * Such blocks neither block walking nor the dig packet, but the raw raycast hits
 * them first and rejected every covered surface block (e.g. dirt under leaf litter).
 * @param {import('mineflayer').Bot} bot @param {Vec3} eye @param {Vec3} direction @param {number} range @param {Vec3} target
 */
function raycastThroughCovers (bot, eye, direction, range, target) {
  let traveled = 0
  for (let hops = 0; hops < 4; hops++) {
    const hit = bot.world.raycast(eye.plus(direction.scaled(traveled)), direction, range - traveled)
    if (!hit || !('position' in hit) || !(hit.position instanceof Vec3)) return false
    if (hit.position.equals(target)) return true
    // The raycast result is the world block itself. Collision-less covers and
    // harvestable leaves do not hide a face: covers never block walking or the
    // dig packet, and leaves are exactly what gathering is allowed to clear.
    // Cocoa pods are solid-bodied trunk attachments; like collision-less
    // covers they hide nothing a harvest needs to see.
    if (hit.boundingBox !== 'empty' && !isLeafBlockName(hit.name) && hit.name !== 'cocoa') return false
    const exit = rayExitDistance(hit.position, eye, direction, traveled)
    if (!Number.isFinite(exit)) return false
    traveled += exit
    if (traveled >= range) return false
    // The cover leads straight into the target cell; no further ray is needed.
    if (eye.plus(direction.scaled(traveled)).floored().equals(target)) return true
    traveled += 1e-4
  }
  return false
}

/** Ray distance from a start point to the exit face of a cell along its direction.
 * @param {Vec3} cell @param {Vec3} start @param {Vec3} direction @param {number} entered
 */
function rayExitDistance (cell, start, direction, entered) {
  let exit = Infinity
  for (const axis of /** @type {const} */ (['x', 'y', 'z'])) {
    const d = direction[axis]
    if (Math.abs(d) < 1e-9) continue
    const bound = d > 0 ? cell[axis] + 1 : cell[axis]
    const t = (bound - start[axis]) / d
    if (t > entered + 1e-9) exit = Math.min(exit, t - entered)
  }
  return exit
}

/** An entity other than a player overlaps the cell. A companion player
 * hugging the bot is normal in this game and must not veto the entry stand;
 * mobs, armor stands and objects still do.
 * @param {import('mineflayer').Bot} bot @param {Vec3} p
 */
function occupiedByNonPlayer (bot, p) {
  for (const entity of Object.values(bot.entities ?? {})) {
    if (!entity.position || entity.isValid === false || entity.name === 'item') continue
    if (entity.type === 'player' || entity.name === 'player') continue
    const { x, y, z } = entity.position, half = (entity.width || 0.6) / 2
    if (x + half > p.x && x - half < p.x + 1 && z + half > p.z && z - half < p.z + 1 &&
      y + (entity.height || 1.8) > p.y && y < p.y + 1) return true
  }
  return false
}

export class GatherNavigation {
  /**
   * @param {import('mineflayer').Bot} bot @param {Vec3} entry @param {AbortSignal} signal
   * @param {string} [world] @param {Vec3} [boundsCenter]
   * Stands stay within 16 blocks of the entry, or additionally within 12 of an
   * explicit bounds center: chunk-clamped digging anchors that on the chunk
   * center so corner targets stay reachable even when work starts at an edge.
   */
  constructor (bot, entry, signal, world = '', boundsCenter = null) {
    this.bot = bot; this.entry = entry; this.signal = signal; this.world = world
    this.boundsCenter = boundsCenter
    /** Corridor bounds (deterministic cells, mine-preset style). While set,
     * stands and route restrictions follow corridor membership instead of the
     * altitude bands. @type {Map<string, {minY:number, maxY:number}> | null} */
    this.corridor = null
    /** @type {Map<string,Support>} */
    this.scaffolds = new Map()
    /** @type {Map<string,Support>} */
    this.persistedScaffolds = new Map()
  }

  scaffoldFile () {
    if (!this.world) return null
    const root = process.env.AWESOME_MINEFLAYER_MCP_HOME || path.resolve('state')
    const key = createHash('sha256').update(this.world).digest('hex')
    return path.join(root, 'scaffolds', `${key}.json`)
  }

  async persistScaffolds () {
    const file = this.scaffoldFile()
    if (!file) return
    if (!this.persistedScaffolds.size) {
      await fs.rm(file, { force: true })
      return
    }
    const value = { version: 1, world: this.world, scaffolds: [...this.persistedScaffolds.values()] }
    await fs.mkdir(path.dirname(file), { recursive: true })
    const temporary = `${file}.tmp`
    await fs.writeFile(temporary, JSON.stringify(value, null, 2), 'utf8')
    await fs.rename(temporary, file)
  }

  async restoreScaffolds () {
    const file = this.scaffoldFile()
    if (!file) return
    let value
    try { value = JSON.parse(await fs.readFile(file, 'utf8')) } catch (error) {
      if (error instanceof Error && 'code' in error && error.code === 'ENOENT') return
      throw new Error(`GATHER_SCAFFOLD_STATE: Cannot read temporary support record: ${String(error)}`)
    }
    if (value?.version !== 1 || value.world !== this.world || !Array.isArray(value.scaffolds)) return
    for (const entry of value.scaffolds) {
      const position = entry?.position
      if (!position || ![position.x, position.y, position.z].every(Number.isInteger) || !Number.isInteger(entry.type)) continue
      const record = { position: new Vec3(position.x, position.y, position.z), type: entry.type, entry: entry.entry }
      const key = safety.key(record.position)
      this.persistedScaffolds.set(key, record)
      if (record.position.distanceTo(this.entry) <= 16) this.scaffolds.set(key, record)
    }
  }

  /** @param {{x:number,y:number,z:number}} p @param {RouteMode} mode @param {number} [feetY] */
  breakable (p, mode, feetY = this.entry.y) {
    const point = new Vec3(p.x, p.y, p.z).floored()
    const block = this.bot.blockAt(point)
    if (mode === 'leaf') return point.y >= this.entry.y && point.y <= this.entry.y + 1 && isLeafBlockName(block?.name)
    const currentFeetY = Math.floor(feetY)
    if (point.y < currentFeetY + 1 || point.y > currentFeetY + 2) return false
    return isLeafBlockName(block?.name)
  }

  /** @param {{x:number,y:number,z:number}} p @param {RouteMode} mode */
  leafBreakable (p, mode = 'leaf') { return this.breakable(p, mode) }

  /** @param {Array<{x:number,y:number,z:number}>} blocks @param {RouteMode} mode @param {number} [feetY] */
  allowedBreaks (blocks, mode, feetY = this.entry.y) { return blocks.every(block => this.breakable(block, mode, feetY)) }

  /** @param {{x:number,y:number,z:number}} p @param {RouteMode} mode */
  safeRoutePoint (p, mode) {
    const point = new Vec3(p.x, p.y, p.z).floored()
    const anchor = this.boundsCenter
    if (this.corridor != null) {
      if (!this.inCorridor(point)) return false
    } else if (point.distanceTo(this.entry) > 16 && !(anchor && point.distanceTo(anchor) <= 12)) return false
    if (occupied(this.bot, point, true)) return false
    if (mode === 'scaffold' && point.y >= this.entry.y + 1 && point.y <= this.entry.y + scaffoldHeight) {
      const feet = this.bot.blockAt(point), head = this.bot.blockAt(point.offset(0, 1, 0))
      const floor = this.bot.blockAt(point.offset(0, -1, 0))
      return Boolean(floor) && (clear(feet) || isLeafBlockName(feet?.name)) && (clear(head) || isLeafBlockName(head?.name))
    }
    // A tree root can sit below the bot on a gentle slope. Allow one block of
    // descent for ordinary surface routes and a wider bounded band for high
    // routes; floor blocks are still never breakable by this navigation mode.
    const minY = mode === 'high' ? this.entry.y - 3 : this.entry.y - 1
    const maxY = mode === 'high' ? this.entry.y + 3 : this.entry.y + 1
    if (point.y < minY || point.y > maxY) return false
    const floor = this.bot.blockAt(point.offset(0, -1, 0))
    const feet = this.bot.blockAt(point), head = this.bot.blockAt(point.offset(0, 1, 0))
    const openFeet = clear(feet) || isLeafBlockName(feet?.name)
    const openHead = clear(head) || isLeafBlockName(head?.name)
    return solid(floor) && openFeet && openHead
  }

  /** @param {{x:number,y:number,z:number}} p */
  safe (p) {
    const point = new Vec3(p.x, p.y, p.z).floored()
    return point.y === this.entry.y && point.distanceTo(this.entry) <= 16 &&
      safeStanding(this.bot, point) && !occupiedByNonPlayer(this.bot, point)
  }

  /** @param {RouteMode} mode @param {boolean} [noBand] skip the altitude-band
   * restriction — used by the return iron rule, whose question is world
   * shape ("can I walk home without changing anything"), not band policy. */
  movements (mode = 'leaf', noBand = false) {
    const movement = new Movements(this.bot)
    movement.anelfGatherMode = mode
    if (mode === 'scaffold') {
      movement.anelfScaffoldingBlocks = this.scaffoldingIds()
    }
    safety.restrictMovements(movement)
    movement.blocksCantBreak ??= new Set()
    for (const [name, definition] of Object.entries(this.bot.registry?.blocksByName ?? {})) {
      if (!isLeafBlockName(name) && Number.isInteger(definition?.id)) movement.blocksCantBreak.add(definition.id)
    }
    movement.exclusionAreasStep.push(block => {
      if (noBand) return 0
      const p = block.position
      if (this.corridor != null) {
        return this.inCorridor(p) || this.inCorridor(p.offset(0, -1, 0)) ? 0 : 100
      }
      // mineflayer-pathfinder asks this hook about the floor, feet and head
      // blocks around a route point. Checking the queried block itself would
      // reject every otherwise valid point because its floor is one block low.
      const routeYs = mode === 'high'
        ? [this.entry.y - 3, this.entry.y - 2, this.entry.y - 1, this.entry.y, this.entry.y + 1, this.entry.y + 2, this.entry.y + 3]
        : mode === 'scaffold' ? Array.from({ length: scaffoldHeight + 1 }, (_, index) => this.entry.y + index)
          : [this.entry.y - 1, this.entry.y, this.entry.y + 1]
      const allowed = routeYs.some(y => {
        const point = new Vec3(p.x, y, p.z)
        return p.y >= y - 1 && p.y <= y + 2 && this.safeRoutePoint(point, mode)
      })
      return allowed ? 0 : 100
    })
    return movement
  }

  /** Activate corridor bounds from deterministic cells (stairs/tunnels).
   * @param {{x:number,y:number,z:number}[]} cells */
  setCorridor (cells) {
    /** @type {Map<string, {minY:number, maxY:number}>} */
    const columns = new Map()
    for (const cell of cells) {
      const column = `${cell.x},${cell.z}`
      const band = columns.get(column) ?? { minY: cell.y, maxY: cell.y }
      band.minY = Math.min(band.minY, cell.y); band.maxY = Math.max(band.maxY, cell.y)
      columns.set(column, band)
    }
    this.corridor = columns
  }

  clearCorridor () { this.corridor = null }

  /** @param {Vec3} point */
  inCorridor (point) {
    const band = this.corridor?.get(`${point.x},${point.z}`)
    return band != null && point.y >= band.minY && point.y <= band.maxY
  }

  /** The return iron rule as a primitive: a walking route that breaks
   * nothing but leaves and places nothing. Every below-entry dig is only
   * committed while such a route back exists — the mine invariant promoted
   * to all work faces, replacing brain-specific guarantees.
   * @param {Vec3} from @param {Vec3} to @param {RouteMode} mode
   */
  async reachableNoDig (from, to, mode = 'leaf') {
    if (!this.safeRoutePoint(from, mode) || !this.safeRoutePoint(to, mode)) return false
    const movement = this.movements(mode, true)
    movement.canDig = false
    movement.scafoldingBlocks = []
    const search = this.bot.pathfinder.getPathFromTo(movement, from, new goals.GoalBlock(to.x, to.y, to.z), {
      timeout: 1000, tickTimeout: 25, searchRadius: 32, optimizePath: false,
    })
    for (const { result } of search) {
      this.signal.throwIfAborted()
      if (String(result.status) === 'partial') { await yieldFrame(); continue }
      return result.status === 'success' && result.path.every(p =>
        this.safeRoutePoint(p, mode) && p.toBreak.every(broken => isLeafBlockName(broken.name)) && !p.toPlace.length)
    }
    return false
  }

  scaffoldingIds () {
    // Routine gathering uses dirt only. Hard blocks and planks are emergency material.
    const names = ['dirt']
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
    const search = this.bot.pathfinder.getPathFromTo(this.movements(mode), from, new goals.GoalBlock(to.x, to.y, to.z), {
      timeout: mode === 'leaf' ? 400 : 1000, tickTimeout: 25, searchRadius: 32, optimizePath: false,
    })
    for (const { result } of search) {
      this.signal.throwIfAborted()
      if (String(result.status) === 'partial') { await yieldFrame(); continue }
      return result.status === 'success' && result.path.every(p => this.safeRoutePoint(p, mode) && this.allowedBreaks(p.toBreak, mode, p.y) &&
        (mode !== 'scaffold' ? !p.toPlace.length : p.toPlace.every(place => {
          const y = placementTarget(place).y
          return y >= this.entry.y && y <= this.entry.y + scaffoldHeight - 1
        })))
    }
    return false
  }

  /** @param {Vec3} to @param {RouteMode} mode */
  async walk (to, mode = 'leaf') {
    this.signal.throwIfAborted()
    // The server silently re-teleports the bot whenever client physics walks
    // into a block the client world does not show (world data divergence).
    // Normal walking never receives these forced syncs, so a burst of them is
    // an unambiguous position fight: abandon the leg at once — every stand
    // rotation inside this area would hit the same disagreement. Track syncs
    // during preflight too, and release the listener if preflight rejects.
    const forcedSyncs = []
    let resolveFight = null
    const positionFight = new Promise(resolve => { resolveFight = resolve })
    const onForced = () => {
      const now = Date.now()
      forcedSyncs.push(now)
      while (forcedSyncs.length && now - forcedSyncs[0] > 2000) forcedSyncs.shift()
      if (forcedSyncs.length >= 3) resolveFight()
    }
    if (this.bot._client?.on) this.bot._client.on('position', onForced)
    // Pathfinding on a just-joined world silently starves: while the goal's
    // chunk is still streaming in, A* keeps re-planning against null blocks
    // forever instead of failing. Wait for the world around the goal before
    // committing to a route — the worker dispatches the moment the bot spawns.
    try {
      for (let waited = 0; this.bot.blockAt(to) == null && waited < 10000; waited += 250) {
        this.signal.throwIfAborted()
        await delay(250, undefined, { signal: this.signal })
      }
      this.signal.throwIfAborted()
      if (!await this.reachable(this.bot.entity.position, to, mode)) throw new Error(`GATHER_NO_ROUTE: No confirmed ${mode} route.`)
      this.signal.throwIfAborted()
      this.bot.pathfinder.setMovements(this.movements(mode))
    } catch (error) {
      this.bot._client?.removeListener?.('position', onForced)
      throw error
    }
    // Only clear the goal this walk installed: when survival rescue preempts,
    // the walk's own cleanup must never kill the rescue's replacement goal.
    let installed = null
    const stop = () => {
      const active = this.bot.pathfinder.goal
      if (active == null || installed == null || active === installed) this.bot.pathfinder.setGoal(null)
    }
    let unsafe = false
    /** @param {import('mineflayer-pathfinder').PartiallyComputedPath} result */
    const validate = result => {
      const rejected = result.path.find(p => !this.safeRoutePoint(p, mode) || !this.allowedBreaks(p.toBreak, mode, p.y) ||
        (mode !== 'scaffold' ? p.toPlace.length : p.toPlace.some(place => {
          const y = placementTarget(place).y
          return y < this.entry.y || y > this.entry.y + scaffoldHeight - 1
        })))
      if (rejected) {
        const point = new Vec3(rejected.x, rejected.y, rejected.z).floored()
        console.error('[anelf] gather path rejected:', JSON.stringify({ mode, to, point,
          feet: this.bot.blockAt(point)?.name, head: this.bot.blockAt(point.offset(0, 1, 0))?.name,
          floor: this.bot.blockAt(point.offset(0, -1, 0))?.name,
          breaks: rejected.toBreak, places: rejected.toPlace }))
        unsafe = true; result.path.length = 0; stop()
      }
    }
    this.bot.on('path_update', validate)
    this.signal.addEventListener('abort', stop, { once: true })
    const timeout = new AbortController()
    const originalPlace = this.bot.placeBlock
    const placements = new Set()
    if (mode === 'scaffold') this.bot.placeBlock = (reference, face) => {
      const operation = (async () => {
        this.signal.throwIfAborted()
        const target = reference.position.plus(face).floored()
        const before = this.bot.blockAt(target)
        if (!before || before.boundingBox !== 'empty' || target.y < this.entry.y || target.y > this.entry.y + scaffoldHeight - 1) {
          throw new Error('GATHER_SCAFFOLD_TARGET: Placement is outside the verified empty scaffold area.')
        }
        const key = safety.key(target)
        const record = { position: target, type: this.bot.registry?.blocksByName?.dirt?.id ?? 0, entry: this.entry.clone() }
        // Write ahead of the packet so a disconnect cannot lose placement ownership.
        this.scaffolds.set(key, record); this.persistedScaffolds.set(key, record)
        await this.persistScaffolds()
        this.signal.throwIfAborted()
        await originalPlace.call(this.bot, reference, face)
        const block = this.bot.blockAt(target)
        if (block?.boundingBox === 'block') {
          record.type = block.type
          await this.persistScaffolds()
        }
      })()
      placements.add(operation)
      operation.then(() => placements.delete(operation), () => placements.delete(operation))
      return operation
    }
    let motion
    const stall = new AbortController()
    try {
      motion = this.bot.pathfinder.goto(installed = new goals.GoalBlock(to.x, to.y, to.z))
      // A walking bot must keep making progress unless the route itself is
      // being cleared (mining or placing). Survival rescue escalates a six
      // second frozen walk into a preemption, so gathering abandons the
      // stalled leg first and lets the bounded stand rotation pick a
      // different approach.
      const stalled = (async () => {
        let last = this.bot.entity.position.clone()
        let frozen = 0
        let hopped = false
        while (true) {
          await delay(250, undefined, { signal: stall.signal })
          const now = this.bot.entity.position
          const moved = now.distanceTo(last) > 0.1
          last = now.clone()
          frozen = moved || this.bot.pathfinder.isMining() || this.bot.pathfinder.isBuilding() ? 0 : frozen + 250
          if (frozen < 2500) continue
          if (!hopped) {
            // A bot frozen while "following a path" is usually physically
            // jammed in its own cell. One bounded hop re-seats the client
            // physics before the leg is abandoned.
            hopped = true
            frozen = 1250
            this.bot.setControlState('jump', true)
            setTimeout(() => { try { this.bot.setControlState('jump', false) } catch { /* already stopped */ } }, 400)
            continue
          }
          throw new Error('GATHER_TRAVEL_TIMEOUT: Walking stalled without progress.')
        }
      })()
      stalled.catch(() => {})
      await Promise.race([
        motion,
        stalled,
        positionFight.then(() => { throw new Error('GATHER_POSITION_FIGHT: The server keeps re-syncing the bot without displacement.') }),
        delay(15000, undefined, { signal: timeout.signal }).then(() => { throw new Error('GATHER_TRAVEL_TIMEOUT: Walking timed out.') })
      ])
      this.signal.throwIfAborted()
      if (unsafe || !this.safeRoutePoint(to, mode) || safety.key(this.bot.entity.position) !== safety.key(to)) throw new Error('GATHER_NOT_ARRIVED: Safe arrival was not confirmed.')
    } finally {
      stall.abort()
      timeout.abort(); stop()
      if (onForced && this.bot._client?.removeListener) this.bot._client.removeListener('position', onForced)
      if (motion) await motion.catch(() => {})
      await Promise.allSettled([...placements])
      if (mode === 'scaffold') this.bot.placeBlock = originalPlace
      this.signal.removeEventListener('abort', stop)
      this.bot.removeListener('path_update', validate)
    }
  }

  async cleanupScaffolding () {
    if (!this.scaffolds.size) return
    // Descend builder blocks are recorded mid-loop, so keep draining until the
    // registry is empty instead of clearing records that were never processed.
    while (this.scaffolds.size) {
      const supports = [...this.scaffolds.values()].sort((a, b) => b.position.y - a.position.y)
      for (const { position: point, type } of supports) {
      this.signal.throwIfAborted()
      if (this.bot.blockAt(point)?.boundingBox === 'empty') {
        const key = safety.key(point)
        this.scaffolds.delete(key); this.persistedScaffolds.delete(key); await this.persistScaffolds()
        continue
      }
      const block = this.bot.blockAt(point)
      const key = safety.key(point)
      if (block?.boundingBox === 'empty') {
        this.scaffolds.delete(key); this.persistedScaffolds.delete(key); await this.persistScaffolds()
        continue
      }
      if (!block) throw new Error('GATHER_SCAFFOLD_CHANGED: Temporary support is not loaded; refusing to dig it.')
      if (block.type !== type) {
        // The recorded block is gone and another block occupies the cell;
        // never dig a replacement that the bot did not place.
        this.scaffolds.delete(key); this.persistedScaffolds.delete(key); await this.persistScaffolds()
        continue
      }
      const landing = this.bot.blockAt(point.offset(0, -1, 0))
      const feet = this.bot.entity.position
      let descend = feet.floored().equals(point.offset(0, 1, 0))
      if (!descend && !reachableFace(this.bot, point, feet)) {
        const top = point.offset(0, 1, 0)
        if (!solid(landing) || !safeStanding(this.bot, top)) throw new Error('GATHER_SCAFFOLD_CLEANUP: No safe approach to recorded support.')
        await this.walk(top, 'scaffold')
        descend = true
      }
      this.signal.throwIfAborted()
      if (descend) {
        if (!solid(landing)) {
          // The landing below the support was dug away (our own pit): build
          // the way down with carried dirt instead of stranding the bot on
          // top of its own scaffold. The placed block becomes a recorded
          // support and is dismantled by a later iteration of this loop.
          const below = point.offset(0, -1, 0)
          const carried = this.bot.inventory?.items?.().some(entry => entry.name === 'dirt')
          if (!carried || landing?.boundingBox !== 'empty' || !this.bot.placeBlock) {
            throw new Error('GATHER_SCAFFOLD_CLEANUP: Descent needs solid ground immediately below the support.')
          }
          await this.bot.placeBlock(block, new Vec3(0, -1, 0))
          const placed = this.bot.blockAt(below)
          if (placed?.boundingBox !== 'block') {
            throw new Error('GATHER_SCAFFOLD_CLEANUP: Descent support placement was not confirmed.')
          }
          const belowKey = safety.key(below)
          const record = { position: below.clone(), type: placed.type, entry: this.entry.clone() }
          this.scaffolds.set(belowKey, record)
          this.persistedScaffolds.set(belowKey, record)
          await this.persistScaffolds()
        }
        const centered = point.offset(0.5, 1, 0.5)
        if (this.bot.entity.position.distanceTo(centered) > 0.18) await this.walk(point.offset(0, 1, 0), 'scaffold')
        await safety.descendTemporarySupport(this.bot, block, this.signal)
        const end = Date.now() + 2500
        while ((!this.bot.entity.onGround || Math.abs(this.bot.entity.position.y - point.y) > 0.15) && Date.now() < end) {
          await delay(50, undefined, { signal: this.signal })
        }
        if (!this.bot.entity.onGround || Math.abs(this.bot.entity.position.y - point.y) > 0.15) throw new Error('GATHER_SCAFFOLD_CLEANUP: Descent did not settle.')
      } else {
        if (!safety.canHarvestBlock(this.bot, block)) throw new Error('GATHER_SCAFFOLD_CLEANUP: Support is not safe to remove.')
        await this.bot.dig(block, true, 'raycast')
      }
      if (this.bot.blockAt(point)?.boundingBox !== 'empty') throw new Error('GATHER_SCAFFOLD_CLEANUP: Temporary support removal was not confirmed.')
      this.scaffolds.delete(key); this.persistedScaffolds.delete(key); await this.persistScaffolds()
      }
    }
    if (this.bot.entity.position.floored().y !== this.entry.y) await this.walk(this.entry, 'leaf')
  }
}
