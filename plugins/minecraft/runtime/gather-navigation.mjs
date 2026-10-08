// @ts-check
/** Bounded walking on known, level ground; never excavate a route or descend to resources. */
import { setImmediate as yieldFrame, setTimeout as delay } from 'node:timers/promises'
import { Vec3 } from 'vec3'
import pathfinder from 'mineflayer-pathfinder'
import safety from 'mineflayer/lib/anelf_mining/mining-safety.cjs'
import { safeStanding, occupied } from '../bot/anelf-survival-observation.mjs'

const { Movements, goals } = pathfinder

export class GatherNavigation {
  /** @param {import('mineflayer').Bot} bot @param {Vec3} entry @param {AbortSignal} signal */
  constructor (bot, entry, signal) { this.bot = bot; this.entry = entry; this.signal = signal }

  /** @param {{x:number,y:number,z:number}} p */
  safe (p) {
    const point = new Vec3(p.x, p.y, p.z).floored()
    return point.y === this.entry.y && point.distanceTo(this.entry) <= 16 &&
      safeStanding(this.bot, point) && !occupied(this.bot, point, true)
  }

  movements () {
    const movement = safety.restrictMovements(new Movements(this.bot))
    movement.exclusionAreasStep.push(block => {
      const p = block.position
      return p.y >= this.entry.y && p.y <= this.entry.y + 1 && this.safe(new Vec3(p.x, this.entry.y, p.z)) ? 0 : 100
    })
    return movement
  }

  /** @param {Vec3} from @param {Vec3} to */
  async reachable (from, to) {
    if (!this.safe(from) || !this.safe(to)) return false
    const search = this.bot.pathfinder.getPathFromTo(this.movements(), from, new goals.GoalBlock(to.x, to.y, to.z), {
      timeout: 200, tickTimeout: 10, searchRadius: 32, optimizePath: false,
    })
    for (const { result } of search) {
      this.signal.throwIfAborted()
      if (String(result.status) === 'partial') { await yieldFrame(); continue }
      return result.status === 'success' && result.path.every(p => this.safe(p) && !p.toBreak.length && !p.toPlace.length)
    }
    return false
  }

  /** @param {Vec3} to */
  async walk (to) {
    this.signal.throwIfAborted()
    if (!await this.reachable(this.bot.entity.position, to)) throw new Error('GATHER_NO_ROUTE: No confirmed level walking route; no digging or scaffolding was attempted.')
    this.signal.throwIfAborted()
    this.bot.pathfinder.setMovements(this.movements())
    const stop = () => this.bot.pathfinder.setGoal(null)
    let unsafe = false
    /** @param {import('mineflayer-pathfinder').PartiallyComputedPath} result */
    const validate = result => {
      if (result.path.some(p => !this.safe(p) || p.toBreak.length || p.toPlace.length)) {
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
      if (unsafe || !this.safe(to) || safety.key(this.bot.entity.position) !== safety.key(to)) throw new Error('GATHER_NOT_ARRIVED: Safe arrival was not confirmed.')
    } finally {
      timeout.abort(); stop()
      await motion.catch(() => {})
      this.signal.removeEventListener('abort', stop)
      this.bot.removeListener('path_update', validate)
    }
  }
}
