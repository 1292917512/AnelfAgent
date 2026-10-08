// @ts-check
/** Owns one bot action until its work and cancellation cleanup have both settled. */
import { AsyncLocalStorage } from 'node:async_hooks'
import { randomUUID } from 'node:crypto'
import { performance } from 'node:perf_hooks'
import { ActionLocks } from './action-locks.js'
import { ToolError } from '../util/errors.js'
import { RuntimeMetrics } from './anelf-metrics.mjs'
import { ActionContext } from './anelf-action-context.mjs'
import { attachAutonomousActions } from './anelf-autonomous.mjs'

/** @typedef {import('mineflayer').Bot} Bot */
/** @typedef {import('./action-locks.js').CancelReason} CancelReason */
/** @typedef {'running'|'pausing'|'paused'|'stopping'|'completed'|'cancelled'|'failed'|'interrupted'|'partial'|'blocked'} Phase */
/** @typedef {import('./anelf-action-context.mjs').Origin} Origin */
/** @typedef {{id:string,name:string,phase:Phase,priority:number,startedAt:number,started:number,
 * stopRequestedAt:number|null,stopStarted:number|null,finishedAt:number|null,reason:string,
 * controller:AbortController,runnerDone:boolean,retained:boolean,
 * handles:Set<(reason:CancelReason)=>unknown>,pending:number,dispose:Array<()=>void>,
 * done:Promise<void>,resolve:()=>void,outcome:Phase,cleanupErrors:string[],origin:Origin|null,taskId:string|null,world:string|null,
 * pause:(()=>void)|null,resume:(()=>Promise<unknown>)|null}} Action */
/** @typedef {{id:string,name:string,phase:Phase,startedAt:number,stopRequestedAt:number|null,
 * finishedAt:number|null,elapsedMs:number,stopLatencyMs:number|null,reason:string,
 * cleanupErrors:string[],origin:Origin|null,taskId:string|null,world:string|null}} Snapshot */

export class ActionController extends ActionLocks {
  /** @param {{push:(type:string,data:unknown)=>unknown}} events */
  constructor (events) {
    super()
    this.events = events
    /** @type {AsyncLocalStorage<Action>} */
    this.scope = new AsyncLocalStorage()
    /** @type {Action|null} */
    this.action = null
    /** @type {Snapshot|null} */
    this.last = null
    /** @type {Bot|null} */
    this.bot = null
    this.generation = 0
    this.requests = new ActionContext()
    /** @type {{action:Snapshot, bot:Bot|null, dimension:string|undefined, resume:()=>Promise<unknown>}|null} */
    this.paused = null
    this.autonomousEnabled = true
    this.metrics = new RuntimeMetrics()
  }

  get current () { return this.action?.name ?? null }
  isBusy () { return this.action !== null && this.scope.getStore() !== this.action }

  /** @param {Bot} bot */
  attach (bot) {
    if (this.bot === bot) return
    this.bot = bot
    this.autonomousEnabled = true
    const attachPlugins = () => { if (this.bot === bot) attachAutonomousActions(bot, this) }
    if (bot.autoEat) attachPlugins()
    else bot.once('inject_allowed', attachPlugins)
    this.metrics.attach(bot)
    const interrupt = () => {
      if (this.bot !== bot) return
      if (this.action) this.action.outcome = 'interrupted'
      this.cancelAll('shutdown')
    }
    bot.on('death', interrupt)
    bot.on('respawn', interrupt)
    bot.once('end', () => {
      interrupt()
      bot.removeListener('death', interrupt)
      bot.removeListener('respawn', interrupt)
      bot.removeListener('inject_allowed', attachPlugins)
      if (this.bot === bot) this.bot = null
    })
  }

  /** @param {Action} action @returns {Snapshot} */
  snapshot (action) {
    return { id: action.id, name: action.name, phase: action.phase, startedAt: action.startedAt,
      stopRequestedAt: action.stopRequestedAt, finishedAt: action.finishedAt,
      elapsedMs: Math.round(performance.now() - action.started),
      stopLatencyMs: action.stopStarted === null ? null : Math.round(performance.now() - action.stopStarted),
      reason: action.reason, cleanupErrors: [...action.cleanupErrors], origin: action.origin, taskId: action.taskId, world: action.world }
  }

  status () {
    return { active: this.action !== null, current: this.action ? this.snapshot(this.action) : null, last: this.last,
      paused: this.paused?.action ?? null }
  }

  /** @param {Action} action */
  publish (action) { this.events.push('action_progress', this.snapshot(action)) }

  /** @param {string} name @param {number} priority @returns {Action} */
  create (name, priority) {
    if (this.action) throw new ToolError('BUSY', `${this.action.name} still owns the bot; read action_status or cancel_task.`)
    /** @type {()=>void} */
    let resolve = () => {}
    /** @type {Promise<void>} */
    const done = new Promise(accept => { resolve = accept })
    /** @type {Action} */
    const action = { id: randomUUID(), name, phase: 'running', priority, startedAt: Date.now(), started: performance.now(),
      stopRequestedAt: null, stopStarted: null, finishedAt: null, reason: '', controller: new AbortController(),
      runnerDone: false, retained: false, handles: new Set(), pending: 0, dispose: [], done, resolve,
      outcome: 'completed', cleanupErrors: [], origin: this.requests.current(), taskId: null, world: null, pause: null, resume: null }
    this.action = action
    this.publish(action)
    return action
  }

  /** @param {Action} action */
  settle (action) {
    if (!action.runnerDone || action.retained || action.handles.size || action.pending || action.finishedAt !== null) return
    for (const dispose of action.dispose.splice(0)) dispose()
    action.phase = action.controller.signal.aborted
      ? (['interrupted', 'paused'].includes(action.outcome) ? action.outcome : 'cancelled') : action.outcome
    action.finishedAt = Date.now()
    if (this.action === action) this.action = null
    this.last = this.snapshot(action)
    if (action.phase === 'paused' && action.resume) {
      this.paused = { action: this.last, bot: this.bot, dimension: this.bot?.game?.dimension, resume: action.resume }
    }
    this.publish(action)
    action.resolve()
  }

  /** Track cancellation work even when an upstream callback does not await it.
   * @param {Action} action @param {()=>unknown} work
   */
  cleanup (action, work) {
    action.pending++
    try {
      const result = work()
      Promise.resolve(result).catch(error => { action.cleanupErrors.push(String(error)) }).finally(() => {
        action.pending--
        this.settle(action)
      })
    } catch (error) {
      action.cleanupErrors.push(String(error))
      action.pending--
    }
  }

  /** @param {Action} action @param {CancelReason} reason */
  cancel (action, reason) {
    if (action.controller.signal.aborted) return
    action.phase = action.outcome === 'paused' ? 'pausing' : 'stopping'
    action.reason = reason
    action.stopRequestedAt = Date.now()
    action.stopStarted = performance.now()
    action.controller.abort(new ToolError('CANCELLED', `${action.name} was cancelled (${reason}).`))
    action.retained = false
    for (const dispose of action.dispose.splice(0)) dispose()
    for (const cancel of action.handles) this.cleanup(action, () => cancel(reason))
    this.stopMotion(action)
    this.publish(action)
    this.settle(action)
  }

  /** Stop immediately, retaining ownership until asynchronous cleanup finishes.
   * @param {Action} action
   */
  stopMotion (action) {
    // setGoal(null) aborts immediately; pathfinder.stop() may walk to the next node first.
    const bot = this.bot
    if (bot) {
      this.cleanup(action, () => bot.pathfinder.setGoal(null))
      this.cleanup(action, () => bot.stopDigging())
      this.cleanup(action, () => bot.clearControlStates())
    }
  }

  /** @param {CancelReason} [reason] */
  cancelAll (reason = 'manual') {
    if (reason !== 'superseded') this.generation++
    this.paused = null
    this.autonomousEnabled = false
    if (this.action && this.action.outcome === 'paused') this.action.outcome = 'cancelled'
    if (!this.action) {
      this.bot?.pathfinder.setGoal(null)
      this.bot?.stopDigging()
      this.bot?.clearControlStates()
      return false
    }
    this.cancel(this.action, reason)
    return true
  }

  /** Legacy tool locks become child handles of the admitted action.
   * @param {string} name @param {(reason:CancelReason)=>unknown} [onCancel]
   */
  begin (name, onCancel) {
    let action = this.scope.getStore()
    if (!action) {
      action = this.create(name, 1)
      action.runnerDone = true
    }
    if (this.action !== action) throw new ToolError('CANCELLED', 'The action no longer owns this bot.')
    action.controller.signal.throwIfAborted()
    const cancel = onCancel ?? (() => {})
    action.handles.add(cancel)
    let released = false
    return { signal: action.controller.signal, actionId: action.id, origin: action.origin,
      /** @param {Phase} [outcome] @param {string} [reason] */
      release: (outcome, reason) => {
        if (released) return
        released = true
        if (outcome) action.outcome = outcome
        if (reason) action.reason = reason
        action.handles.delete(cancel)
        this.settle(action)
      } }
  }

  /** @template T @param {string} name @param {number} priority
   * @param {(signal:AbortSignal)=>Promise<T>} work @returns {Promise<T>}
   */
  async run (name, priority, work) {
    if (this.paused && priority < 2) throw new ToolError('BUSY', 'A task is paused. Use resume_action to continue it, or cancel_task to discard its continuation.')
    const generation = this.generation
    const previous = this.action
    if (previous) {
      if (!(priority > previous.priority || (previous.retained && priority >= previous.priority))) {
        throw new ToolError('BUSY', `${previous.name} is ${previous.phase}; read action_status or cancel_task before starting ${name}.`)
      }
      this.cancel(previous, 'superseded')
      // Cleanup keeps ownership. A timed-out admission never starts later in the background.
      /** @type {ReturnType<typeof setTimeout>|undefined} */
      let timer
      try {
        await Promise.race([previous.done, new Promise((resolve, reject) => {
          timer = setTimeout(() => reject(new ToolError('BUSY', 'Previous action is still stopping. Read action_status before retrying.')), 5000)
        })])
      } finally { clearTimeout(timer) }
    }
    if (generation !== this.generation) throw new ToolError('CANCELLED', 'A stop or lifecycle event cancelled the pending action.')
    if (this.requests.current()) this.requests.assertCurrent()
    if (!name.startsWith('auto_')) this.autonomousEnabled = true
    const action = this.create(name, priority)
    return this.scope.run(action, async () => {
      try {
        const result = await work(action.controller.signal)
        action.controller.signal.throwIfAborted()
        return result
      } catch (error) {
        if (!action.controller.signal.aborted) {
          action.outcome = 'failed'
          action.phase = 'stopping'
          action.reason = error instanceof Error ? error.message : String(error)
          this.stopMotion(action)
          this.publish(action)
        }
        throw action.controller.signal.aborted ? action.controller.signal.reason : error
      } finally {
        action.runnerDone = true
        this.settle(action)
      }
    })
  }

  /** @param {()=>void} pause @param {()=>Promise<unknown>} resume */
  continuation (pause, resume) {
    const action = this.scope.getStore()
    if (!action || action !== this.action) throw new ToolError('CANCELLED', 'No active owner for continuation.')
    action.pause = pause
    action.resume = resume
  }

  /** @param {string} taskId @param {string} world */
  linkTask (taskId, world) {
    const action = this.scope.getStore()
    if (action && action === this.action) { action.taskId = taskId; action.world = world }
  }

  pause () {
    this.autonomousEnabled = false
    const action = this.action
    if (!action) return { ok: true, supported: Boolean(this.paused), ...this.status() }
    if (!action.pause) {
      this.cancelAll('manual')
      return { ok: true, supported: false, ...this.status(), reason: 'This atomic action cannot be resumed safely; cancellation requested instead. Check action_status for cleanup.' }
    }
    if (action.phase !== 'pausing') {
      action.phase = 'pausing'
      action.stopRequestedAt = Date.now()
      action.stopStarted = performance.now()
      action.reason = 'Pause requested; waiting for a safe checkpoint.'
      action.pause()
      this.publish(action)
    }
    return { ok: true, supported: true, ...this.status() }
  }

  /** @param {(()=>Promise<unknown>)} [work] */
  async resume (work) {
    if (this.action) throw new ToolError('BUSY', 'Wait for the current action and pause cleanup to finish.')
    const saved = this.paused
    if (!saved) throw new ToolError('NOT_FOUND', 'There is no paused action in this connection.')
    if (saved.bot !== this.bot || saved.dimension !== this.bot?.game?.dimension || !this.bot || this.bot.health <= 0) {
      this.paused = null
      throw new ToolError('FORBIDDEN', 'Connection, dimension or life state changed; the old action cannot resume.')
    }
    this.paused = null
    try { return await (work ? work() : saved.resume()) } catch (error) {
      if (!this.action) this.paused = saved
      throw error
    }
  }

  /** @param {string} name @param {(signal:AbortSignal)=>Promise<void>} work */
  async autonomous (name, work) {
    if (!this.bot || this.bot.health <= 0 || this.action || this.paused || !this.autonomousEnabled) return
    await this.requests.run(undefined, () => this.run(name, 0, work))
  }

  /** Retain fire-and-forget pathfinding until arrival, loss of target, or cancellation.
   * @param {Bot} bot @param {boolean} continuous @param {number|undefined} [entityId]
   */
  retainGoal (bot, continuous, entityId) {
    const action = this.scope.getStore()
    if (!action || this.action !== action) throw new ToolError('INTERNAL', 'Persistent movement has no action owner.')
    action.controller.signal.throwIfAborted()
    action.retained = true
    const goal = bot.pathfinder.goal
    const finish = (failed = false) => {
      if (this.action !== action || !action.retained) return
      action.retained = false
      if (failed) {
        action.outcome = 'failed'
        action.reason = 'Movement target was lost or the pathfinder stopped.'
      }
      for (const dispose of action.dispose.splice(0)) dispose()
      bot.pathfinder.setGoal(null)
      this.settle(action)
    }
    const reached = () => { if (!continuous) finish() }
    const stopped = () => finish(true)
    /** @param {import('prismarine-entity').Entity} entity */
    const gone = entity => { if (entity.id === entityId) finish(true) }
    const changed = () => { if (bot.pathfinder.goal !== goal) finish(true) }
    /** @param {import('mineflayer-pathfinder').PartiallyComputedPath} result */
    const path = result => { if (result.status === 'noPath' || result.status === 'timeout') finish(true) }
    bot.on('goal_reached', reached)
    bot.on('path_stop', stopped)
    bot.on('goal_updated', changed)
    bot.on('entityGone', gone)
    bot.on('path_update', path)
    action.dispose.push(() => {
      bot.removeListener('goal_reached', reached)
      bot.removeListener('path_stop', stopped)
      bot.removeListener('goal_updated', changed)
      bot.removeListener('entityGone', gone)
      bot.removeListener('path_update', path)
    })
  }

  /** @param {Bot} bot */
  retainControls (bot) {
    const action = this.scope.getStore()
    if (!action || this.action !== action) throw new ToolError('CANCELLED', 'Manual movement has no owner.')
    action.retained = Object.values(bot.controlState).some(Boolean)
    this.settle(action)
  }

  /** @template T @param {()=>Promise<T>} work @returns {Promise<T>} */
  async updateControls (work) {
    const action = this.action
    if (!action || action.name !== 'set_control_state' || !action.retained) throw new ToolError('BUSY', 'Manual controls do not own the bot.')
    action.controller.signal.throwIfAborted()
    return this.scope.run(action, work)
  }
}
