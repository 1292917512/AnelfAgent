// @ts-check
/** Event-driven survival with bounded actions and no replay of interrupted work. */
import { randomUUID } from 'node:crypto'
import { performance } from 'node:perf_hooks'
import { setTimeout as delay } from 'node:timers/promises'
import pathfinder from 'mineflayer-pathfinder'
import { Vec3 } from 'vec3'
import { clear, escapeCandidates, observeSurvival, occupied, safeStanding, surfaceVisible } from './anelf-survival-observation.mjs'

/** @typedef {import('mineflayer').Bot & {isAlive?:boolean}} Bot */
/** @typedef {import('./anelf-actions.mjs').ActionController} Controller */
/** @typedef {import('./anelf-survival-observation.mjs').Hazard} Hazard */
/** @typedef {import('./anelf-survival-observation.mjs').Observation} Observation */
/** @typedef {{id:string,attempts:number,lastHazardAt:number,away:Vec3|null}} Episode */

export class SurvivalController {
  /** @param {Controller} locks */
  constructor (locks) {
    this.locks = locks
    this.runtimeId = randomUUID()
    this.enabled = false
    this.intervalMs = 250
    /** @type {Bot|null} */
    this.bot = null
    /** @type {Array<()=>void>} */
    this.listeners = []
    /** @type {Observation|null} */
    this.observation = null
    /** @type {Episode|null} */
    this.episode = null
    /** @type {Promise<void>|null} */
    this.pending = null
    this.dead = false
    this.ready = false
    this.nextCheck = 0
    this.nextAttempt = 0
    this.hurtUntil = 0
    this.frozenSince = 0
    this.nextIdle = 0
    /** @type {{id:string,kind:string,phase:string,reason:string}|null} */
    this.last = null
    /** @type {Map<string,number>} */
    this.announced = new Map()
  }

  /** @param {{enabled:boolean,intervalMs:number}} options */
  configure (options) {
    this.enabled = options.enabled
    this.intervalMs = options.intervalMs
    this.syncEating()
    if (!this.enabled && this.locks.action?.name.startsWith('auto_')) this.locks.cancel(this.locks.action, 'manual')
    return this.status()
  }

  status () {
    const o = this.observation
    return { version: 1, runtimeId: this.runtimeId, enabled: this.enabled, intervalMs: this.intervalMs,
      running: this.pending !== null, life: !this.bot ? 'unknown' : this.dead ? 'dead' : this.ready && o?.health != null && o.health > 0 ? 'alive' : 'unknown',
      health: o?.health ?? null, oxygen: o?.oxygen ?? null, position: o?.position ?? null,
      held: !this.locks.autonomousEnabled || Boolean(this.locks.paused), last: this.last }
  }

  syncEating () {
    if (!this.bot?.autoEat) return
    if (this.enabled) this.bot.autoEat.enableAuto()
    else this.bot.autoEat.disableAuto()
  }

  /** @param {Bot} bot */
  attach (bot) {
    this.detach()
    this.bot = bot
    this.runtimeId = randomUUID()
    this.ready = Boolean(bot.entity?.position && Number.isFinite(bot.health) && bot.health > 0 && bot.isAlive !== false)
    this.dead = false
    this.observation = this.episode = null
    this.nextCheck = this.nextAttempt = this.hurtUntil = this.frozenSince = this.nextIdle = 0
    this.announced.clear()
    const check = () => this.check()
    const health = () => {
      if (Number.isFinite(bot.health) && bot.health > 0 && this.observation?.health != null && bot.health < this.observation.health) {
        this.hurtUntil = performance.now() + 2500
      }
      this.check(true)
    }
    const death = () => {
      if (this.dead) return
      this.dead = true
      this.ready = false
      this.episode = null
      this.publish(randomUUID(), 'death', 'dead', 'Server confirmed death; previous work stays cancelled.')
    }
    const transition = () => { this.ready = false; this.observation = null; this.episode = null; this.hurtUntil = 0 }
    const spawn = () => {
      const died = this.dead
      this.dead = false
      this.ready = true
      this.observation = this.episode = null
      this.hurtUntil = this.frozenSince = 0
      this.syncEating()
      if (died) this.publish(randomUUID(), 'death', 'respawned', 'Spawn confirmed; previous work was not resumed.')
      this.check(true)
    }
    const injected = () => this.syncEating()
    const breath = () => this.check(true)
    /** @param {import('prismarine-entity').Entity} entity */
    const update = entity => { if (entity.id === bot.entity?.id) this.check(true) }
    const end = () => this.detach()
    bot.on('physicsTick', check)
    bot.on('health', health)
    bot.on('breath', breath)
    bot.on('death', death)
    bot.on('respawn', transition)
    bot.on('spawn', spawn)
    bot.on('entityUpdate', update)
    bot.on('inject_allowed', injected)
    bot.once('end', end)
    this.listeners.push(() => {
      bot.removeListener('physicsTick', check); bot.removeListener('health', health)
      bot.removeListener('breath', breath); bot.removeListener('death', death)
      bot.removeListener('respawn', transition); bot.removeListener('spawn', spawn)
      bot.removeListener('entityUpdate', update); bot.removeListener('inject_allowed', injected); bot.removeListener('end', end)
    })
    this.syncEating()
  }

  detach () {
    for (const dispose of this.listeners.splice(0)) dispose()
    this.bot = null
    this.ready = false
    this.observation = this.episode = null
  }

  /** Observation never awaits pathfinding, inventory cleanup or equipment work.
   * @param {boolean} [immediate]
   */
  check (immediate = false) {
    const bot = this.bot, now = performance.now()
    if (!bot || (!immediate && now < this.nextCheck)) return
    this.nextCheck = now + this.intervalMs
    const previous = this.observation
    try { this.observation = observeSurvival(bot) } catch { this.observation = null; return }
    const o = this.observation
    if (!this.enabled || !this.ready || this.dead || !o.known || o.health === null || o.health <= 0 || bot.isAlive === false) return
    const moving = bot.pathfinder?.isMoving() && !bot.pathfinder.isMining() && !bot.pathfinder.isBuilding()
    if (!moving || !previous?.position || !o.position || previous.position.distanceTo(o.position) > 0.15) this.frozenSince = now
    else if (!this.frozenSince) this.frozenSince = now
    const approaching = o.threat && (o.threatDistance < 2.5 ||
      (previous?.threat?.id === o.threat.id && o.threatDistance < previous.threatDistance - 0.1))
    /** @type {Hazard|null} */
    const kind = o.headWater ? 'drowning' : o.burning ? 'burning' : o.trapped ? 'trapped' :
      now < this.hurtUntil ? 'hurt' : approaching ? 'hostile' : moving && now - this.frozenSince >= 6000 ? 'stuck' : null
    if (!kind) {
      if (this.episode && !this.pending && now - this.episode.lastHazardAt >= 2500) {
        this.publish(this.episode.id, 'danger', 'clear', 'Danger signs cleared; interrupted work was not resumed.')
        this.episode = null
      }
      if (!this.pending && !this.episode) this.idle(bot, now)
      return
    }
    if (!this.episode) this.episode = { id: randomUUID(), attempts: 0, lastHazardAt: now, away: o.threat?.position.clone() ?? o.position }
    const episode = this.episode
    episode.lastHazardAt = now
    if (!this.locks.autonomousEnabled || this.locks.paused) {
      this.publish(episode.id, kind, 'held', 'Stopped or paused: danger observed, automatic movement remains disabled.')
      return
    }
    if (this.pending) {
      if (this.locks.action?.name === 'auto_idle') this.locks.cancel(this.locks.action, 'superseded')
      return
    }
    if (now < this.nextAttempt) return
    const interrupted = this.locks.action
    if (interrupted && interrupted.priority >= 2 && !interrupted.retained) return
    if (episode.attempts >= 2) {
      this.publish(episode.id, kind, 'blocked', 'Bounded rescue attempts exhausted; no automatic work resumption.')
      return
    }
    episode.attempts++
    this.nextAttempt = now + 3000
    if (interrupted) this.locks.requests.revoke(interrupted.origin)
    const runtimeId = this.runtimeId
    this.pending = this.locks.requests.run(undefined, () => this.locks.run(`auto_survival_${kind}`, 2, async signal => {
      if (bot !== this.bot || !this.enabled || !this.locks.autonomousEnabled || this.locks.paused) {
        throw new Error('Survival disabled or stopped before admission.')
      }
      this.publish(episode.id, kind, 'started', 'Local rescue owns the bot; prior work is cancelled.')
      if (kind === 'drowning') await this.surface(bot, signal)
      else if (kind === 'stuck') await this.unstuck(bot, signal)
      else await this.escape(bot, episode.away ?? bot.entity.position, signal, kind === 'burning')
      signal.throwIfAborted()
      this.publish(episode.id, kind, 'moved', 'Bounded rescue movement ended; wait for fresh danger observations.')
    })).catch(error => {
      if (runtimeId === this.runtimeId && bot === this.bot) this.publish(episode.id, kind,
        error?.code === 'CANCELLED' || error?.name === 'AbortError' ? 'cancelled' : 'blocked', String(error?.message ?? error))
    }).finally(() => { this.pending = null })
  }

  /** @param {Bot} bot @param {AbortSignal} signal */
  async surface (bot, signal) {
    if (!surfaceVisible(bot)) throw new Error('No known open water surface within eight blocks; refusing blind upward movement.')
    bot.setControlState('jump', true)
    try {
      const deadline = performance.now() + 5000
      while (observeSurvival(bot).headWater) {
        signal.throwIfAborted()
        if (performance.now() >= deadline) throw new Error('Surfacing time limit reached.')
        await delay(100, undefined, { signal })
      }
    } finally { bot.setControlState('jump', false) }
  }

  /** @param {Bot} bot @param {AbortSignal} signal */
  async unstuck (bot, signal) {
    if (!bot.entity.onGround || !safeStanding(bot, bot.entity.position.floored()) ||
        !clear(bot.blockAt(bot.entity.position.offset(0, 2, 0)))) throw new Error('No safe standing space for a bounded unstuck jump.')
    bot.setControlState('jump', true)
    try { await delay(300, undefined, { signal }) } finally { bot.setControlState('jump', false) }
  }

  /** @param {Bot} bot @param {Vec3} away @param {AbortSignal} signal @param {boolean} burning */
  async escape (bot, away, signal, burning) {
    const deadline = performance.now() + 30
    for (const point of escapeCandidates(bot, away, burning)) {
      signal.throwIfAborted()
      const remaining = deadline - performance.now()
      if (remaining <= 0) break
      const goal = new pathfinder.goals.GoalBlock(point.x, point.y, point.z)
      const result = bot.pathfinder.getPathTo(bot.pathfinder.movements, goal, Math.min(10, remaining))
      if (result.status !== 'success' || result.path.some(step => !safeStanding(bot, new Vec3(step.x, step.y, step.z), burning))) continue
      await this.navigate(bot, goal, signal, burning)
      return
    }
    throw new Error('No verified escape route through loaded safe blocks; refusing digging, pillaring or blind drops.')
  }

  /** @param {Bot} bot @param {import('mineflayer-pathfinder').goals.Goal} goal @param {AbortSignal} signal @param {boolean} [shallowWater] */
  async navigate (bot, goal, signal, shallowWater = false) {
    signal.throwIfAborted()
    const timeout = new AbortController()
    const stop = () => bot.pathfinder.setGoal(null)
    /** @type {Error|null} */
    let unsafe = null
    /** @param {import('mineflayer-pathfinder').PartiallyComputedPath} result */
    const validate = result => {
      if (result.path.some(step => !safeStanding(bot, new Vec3(step.x, step.y, step.z), shallowWater))) {
        unsafe = new Error('Updated route crosses unsafe or unknown standing space; local movement stopped.')
        // Pathfinder installs results.path after emitting; clearing the result prevents a stale step in this tick.
        result.path.length = 0
        stop()
      }
    }
    bot.on('path_update', validate)
    const motion = bot.pathfinder.goto(goal)
    signal.addEventListener('abort', stop, { once: true })
    try {
      await Promise.race([motion, delay(8000, undefined, { signal: timeout.signal }).then(() => { throw new Error('Rescue navigation time limit reached.') })])
      signal.throwIfAborted()
      if (unsafe) throw unsafe
    } finally {
      timeout.abort()
      bot.pathfinder.setGoal(null)
      await motion.catch(() => {})
      signal.removeEventListener('abort', stop)
      bot.removeListener('path_update', validate)
    }
  }

  /** @param {Bot} bot @param {number} now */
  idle (bot, now) {
    if (now < this.nextIdle || this.locks.action || this.locks.paused || !this.locks.autonomousEnabled) return
    this.nextIdle = now + 30000
    this.pending = this.locks.autonomous('auto_idle', async signal => {
      if (bot !== this.bot || !this.enabled) return
      const torch = bot.time?.isDay === false && bot.inventory.items().find(item => ['torch', 'soul_torch'].includes(item.name))
      if (torch && bot.entity.onGround) {
        const p = bot.entity.position.floored()
        const point = [p.offset(1, 0, 0), p.offset(-1, 0, 0), p.offset(0, 0, 1), p.offset(0, 0, -1)]
          .find(point => safeStanding(bot, point) && bot.blockAt(point)?.name === 'air' && !occupied(bot, point))
        if (point) {
          const reference = bot.blockAt(point.offset(0, -1, 0)), held = bot.heldItem
          if (!reference) return
          try {
            await bot.equip(torch, 'hand'); signal.throwIfAborted()
            await bot.placeBlock(reference, new Vec3(0, 1, 0)); signal.throwIfAborted()
          } finally {
            const restore = held && bot.inventory.items().find(item => item.type === held.type)
            if (restore) await bot.equip(restore, 'hand')
            else if (!held) await bot.unequip('hand')
          }
          return
        }
      }
      if (bot.inventory.emptySlotCount() < 1) return
      const drop = Object.values(bot.entities).filter(entity => entity.name === 'item' &&
        entity.position.distanceTo(bot.entity.position) <= 3 && safeStanding(bot, entity.position.floored()))
        .sort((a, b) => a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position))[0]
      if (drop) await this.navigate(bot, new pathfinder.goals.GoalNear(drop.position.x, drop.position.y, drop.position.z, 1), signal)
    }).catch(() => {}).finally(() => { this.pending = null })
  }

  /** @param {string} id @param {string} kind @param {string} phase @param {string} reason */
  publish (id, kind, phase, reason) {
    const key = `${id}:${phase}`, now = performance.now()
    this.last = { id, kind, phase, reason: reason.slice(0, 512) }
    if (now - (this.announced.get(key) ?? -Infinity) < 20000) return
    this.announced.set(key, now)
    if (this.announced.size > 128) { const key = this.announced.keys().next().value; if (key) this.announced.delete(key) }
    this.locks.events.push('survival_progress', { ...this.last, runtimeId: this.runtimeId,
      position: this.bot?.entity?.position?.clone() ?? null, health: this.bot?.health ?? null,
      oxygen: this.bot?.oxygenLevel ?? null, dimension: this.bot?.game?.dimension ?? null })
  }
}
