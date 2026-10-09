// @ts-check
/** Model Experience: actions expose ownership, actual stopping and local survival status.
 * Token effect: a few hundred schema tokens; bounded snapshots are only returned on request.
 * Cache effect: schemas change on installation; timing and progress stay in results/events.
 */
import { z } from 'zod'
import inventorySafety from 'mineflayer/lib/anelf_inventory.js'
import { ActionController } from '../bot/anelf-actions.mjs'
import { ToolError } from '../util/errors.js'
import { workbenchPlacementHints } from './anelf-placement-hints.mjs'
import { registerProduction } from './anelf-production-tools.mjs'
import { registerSupplies } from './anelf-supply-tools.mjs'
import { registerGathering } from './anelf-gather-tools.mjs'
import { registerBuildSite } from './anelf-build-site.mjs'

/** @typedef {import('../context.js').ToolContext} Context */
/** @typedef {import('./registry.js').Registrar} Registrar */
const stops = new Set(['cancel_task', 'stop_pathfinding', 'cancel_collect', 'clear_control_states'])
const passive = new Set(['chat', 'whisper', 'wait_for_ticks', 'wait_for_message', 'autoeat_set_enabled', 'autoeat_configure', 'autoeat_cancel', 'configure_survival'])
const persistent = new Set(['set_goal', 'follow_entity', 'flee_from'])

/** @param {Context} ctx @returns {ActionController} */
function controller (ctx) {
  if (!(ctx.locks instanceof ActionController)) throw new ToolError('INTERNAL', 'Action controller is not installed; reinstall the Minecraft runtime.')
  return ctx.locks
}

/** @param {ActionController} locks @param {boolean} [reflex] */
async function stop (locks, reflex = false) {
  const enabled = locks.autonomousEnabled
  locks.cancelAll(reflex ? 'superseded' : 'manual')
  if (reflex) locks.autonomousEnabled = enabled
  // Let synchronous cancellation and its Promise cleanups settle without waiting on stalled work.
  await new Promise(resolve => setImmediate(resolve))
  const status = locks.status()
  return { ok: true, stopped: !status.active && !status.last?.cleanupErrors.length, ...status }
}

/** @param {Registrar} reg */
export function registerActions (reg) {
  registerProduction(reg)
  registerSupplies(reg)
  registerGathering(reg)
  registerBuildSite(reg)
  reg({ name: 'configure_survival', group: 'state', inputSchema: {
    enabled: z.boolean(), intervalMs: z.number().int().min(100).max(10000).default(250),
  }, description: 'Configure local survival observations; health/death/breath events are immediate. Does not resume stopped or paused work. Normal companion settings are synchronized by the Minecraft channel.',
  handler: (args, ctx) => controller(ctx).survival.configure(args) })
  reg({ name: 'get_survival_status', group: 'state', inputSchema: {}, annotations: { readOnlyHint: true },
    description: 'Read local survival settings, known life state, last observed danger and bounded rescue result. Unknown health is not death; interrupted work never resumes automatically.',
    handler: (_args, ctx) => controller(ctx).survival.status() })
  reg({ name: 'pause_action', group: 'state', inputSchema: {},
    description: 'Pause mining at a verified corridor step or pause persistent movement. pausing is not yet paused. Atomic inventory actions are cancelled and drained instead of replayed; supported=false means they cannot resume.',
    handler: (_args, ctx) => controller(ctx).pause() })
  reg({ name: 'resume_action', group: 'state', inputSchema: {},
    description: 'Explicitly resume a paused mining or persistent movement action after validating the same connection, dimension and current conditions. Never replays a cancelled craft or resumes a worker.',
    handler: (_args, ctx) => controller(ctx).resume() })
  reg({ name: 'action_status', group: 'state', inputSchema: {}, annotations: { readOnlyHint: true },
    description: 'Read the current/last action id, owner, phase and cancellation timing. stopping means cleanup is still running; no new conflicting work is accepted. cancelled does not mean inventory or mining goals were completed.',
    handler: (_args, ctx) => controller(ctx).status() })
  reg({ name: 'get_runtime_metrics', group: 'state', inputSchema: {}, annotations: { readOnlyHint: true },
    description: 'Read bounded executor tool timings, pathfinder timing and Node event-loop delay. Samples are per process; they do not measure LLM latency, Minecraft FPS or server TPS.',
    handler: (_args, ctx) => controller(ctx).metrics.snapshot() })
}

/** @template {z.ZodRawShape} S @param {import('./registry.js').ToolDef<S>} def @returns {import('./registry.js').ToolDef<S>} */
export function wrapActionTools (def) {
  const original = def.handler
  const description = stops.has(def.name)
    ? 'Request cancellation of the current action and clear movement immediately. Returns stopped=true only after work and cleanup settle; otherwise read action_status. Does not resume automatically.'
    : persistent.has(def.name) ? def.description + ' Holds action ownership until stopped or superseded; inspect action_status.' : def.description
  /** @type {import('./registry.js').ToolDef<S>} */
  const wrapped = { ...def, description, handler: async (args, ctx) => {
    const locks = controller(ctx)
    return locks.metrics.measure(def.name, async () => {
      if (def.name === 'get_connection_status') {
        const result = await original(args, ctx)
        if (result && typeof result === 'object') return { ...result, survival: locks.survival.status() }
        return result
      }
      if (!def.annotations?.readOnlyHint && !['chat', 'whisper', 'wait_for_ticks', 'wait_for_message'].includes(def.name)) locks.requests.assertCurrent()
      const reflex = locks.requests.current()?.actor === '@reflex'
      if (reflex && !def.annotations?.readOnlyHint && !passive.has(def.name) &&
          (locks.paused || !locks.autonomousEnabled || (locks.action && !['flee_from', 'respawn', 'stop_pathfinding', 'clear_control_states', 'set_control_state'].includes(def.name)))) {
        throw new ToolError('BUSY', 'Idle reflex cannot take control of an active or stopped task.')
      }
      if (stops.has(def.name) || (def.name === 'dig' && 'action' in args && args.action === 'stop')) return stop(locks, reflex)
      if (['pause_action', 'resume_action'].includes(def.name)) return original(args, ctx)
      if (def.annotations?.readOnlyHint || passive.has(def.name) || def.group === 'lifecycle') return original(args, ctx)
      // Returning from an active mine changes that task's destination at its next safe step.
      if (def.name === 'return_from_mine' && locks.current && ['mine_resources', 'dig_staircase', 'dig_tunnel', 'resume_mining', 'return_from_mine'].includes(locks.current)) return original(args, ctx)
      const bot = ctx.manager.requireBot()
      locks.attach(bot)
      /** @param {AbortSignal} signal */
      const run = async signal => {
        const backgroundInventory = ['prepare_item', 'manage_supplies', 'gather_resources'].includes(def.name)
        if (!backgroundInventory) inventorySafety.bindCraftAction(bot, signal)
        try {
          if (persistent.has(def.name)) {
            locks.continuation(() => {
              const active = locks.action
              if (active) { active.outcome = 'paused'; locks.cancel(active, 'manual') }
            }, async () => wrapped.handler(args, ctx))
          }
          const result = await original(args, ctx)
          signal.throwIfAborted()
          if (persistent.has(def.name)) {
            const opts = z.object({ entityId: z.number().optional(), dynamic: z.boolean().optional() }).parse(args)
            locks.retainGoal(bot, def.name === 'follow_entity' || (def.name === 'set_goal' && opts.dynamic === true), opts.entityId)
          } else if (def.name === 'set_control_state') locks.retainControls(bot)
          return result
        } catch (error) {
          if (!signal.aborted && def.name === 'place_block' && 'itemName' in args && args.itemName === 'crafting_table' &&
              error instanceof ToolError && ['PLACEMENT_BLOCKED', 'INVALID_REFERENCE'].includes(error.code)) {
            const candidates = workbenchPlacementHints(bot)
            if (candidates.length) throw new ToolError(error.code, error.message, [
              `Locally checked empty spots with solid support, outside bodies and within sight/reach. If retry is allowed, use one exact place_block argument set: ${JSON.stringify(candidates)}. Recheck the destination block after placing; these are suggestions, not completed actions.`,
              ...(error.suggestions || [])
            ])
          }
          throw error
        } finally {
          // A tool timeout does not imply that craft's window clicks have stopped.
          if (!backgroundInventory) {
            await inventorySafety.waitForCraft(bot)
            inventorySafety.bindCraftAction(bot, null)
          }
        }
      }
      if (def.name === 'set_control_state' && locks.current === 'set_control_state') {
        return locks.updateControls(async () => {
          const result = await original(args, ctx)
          locks.retainControls(bot)
          return result
        })
      }
      if (locks.paused?.action.taskId && ['resume_mining', 'return_from_mine'].includes(def.name)) {
        return locks.resume(() => locks.run(def.name, 1, run))
      }
      return locks.run(def.name, def.name === 'flee_from' ? 2 : reflex || def.name === 'follow_entity' ? 0 : 1, run)
    })
  } }
  return wrapped
}

/** Bind per-call MCP metadata without putting trusted ownership in tool arguments.
 * @template T @param {Context} ctx @param {unknown} metadata @param {()=>T} work @returns {T}
 */
export function withActionRequest (ctx, metadata, work) {
  return controller(ctx).requests.run(metadata, work)
}
