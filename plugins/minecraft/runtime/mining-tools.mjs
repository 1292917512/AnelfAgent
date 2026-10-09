// @ts-check
/** Model Experience: mining tools expose progress, inventory gains and a minimum feet Y.
 * Token effect: a few hundred schema tokens, including one optional height limit; replaces per-block LLM calls.
 * Cache effect: schema changes once; task progress stays in tool results/events.
 */
import path from 'node:path'
import { z } from 'zod'
import { Vec3 } from 'vec3'
import safety from 'mineflayer/lib/anelf_mining/mining-safety.cjs'
import tasks from 'mineflayer/lib/anelf_mining/mining-task.cjs'
import stores from 'mineflayer/lib/anelf_mining/mining-store.cjs'
import { ToolError } from '../util/errors.js'
import { ActionController } from '../bot/anelf-actions.mjs'

/** @typedef {import('../context.js').ToolContext} Context */
/** @typedef {import('./registry.js').Registrar} Registrar */
/** @typedef {import('mineflayer/lib/anelf_mining/mining-task.cjs').Bot} Bot */
/** @typedef {{bot:Bot,world:string,store:stores.MiningStore,record:import('mineflayer/lib/anelf_mining/mining-store.cjs').MineRecord|null,task:tasks.MineTask|null}} Session */
/** @type {WeakMap<Context,{bot:Bot,world:string,ready:Promise<Session>}>} */
const sessions = new WeakMap()
const direction = z.enum(['north', 'south', 'east', 'west', 'forward'])
const input = {
  direction,
  depth: z.number().int().min(0).max(16).default(8).describe('Requested staircase descent, at most 16 levels; clamped at minY. 0 for a horizontal passage.'),
  minY: z.number().int().optional().describe('Lowest feet Y for mining. Defaults to the dimension bottom + 10; requests below that hard limit are clamped. At this height only the requested horizontal length may continue.'),
  length: z.number().int().min(0).max(32).default(16).describe('Maximum extra horizontal tunnel length after the stairs.'),
  item: z.string().default('cobblestone').describe('Inventory item to gather; gains are measured against the starting inventory.'),
  count: z.number().int().min(1).max(256).default(16)
}
const options = z.object(input)

/** @param {Context} ctx @returns {Promise<Session>} */
async function session (ctx) {
  const bot = ctx.manager.requireBot()
  const connection = ctx.manager.statusReport()
  const world = JSON.stringify([connection.host, connection.port, bot.username, bot.game.dimension])
  const existing = sessions.get(ctx)
  if (existing?.bot === bot && existing.world === world) return existing.ready
  const ready = (async () => {
    safety.installSafety(bot)
    const store = new stores.MiningStore(path.join(process.env.AWESOME_MINEFLAYER_MCP_HOME || path.resolve('state'), 'mines'))
    const record = await store.load(world)
    if (record) {
      // Construction validates saved topology and restores floor protection.
      new tasks.MineTask(ctx, bot, record, store)
      if (['running', 'returning', 'pausing'].includes(record.phase)) {
        record.phase = 'interrupted'
        record.reason = 'Runtime restarted; inspect mining_status and explicitly resume_mining or return_from_mine.'
        await store.save(record)
      }
    }
    return { bot, world, store, record, task: null }
  })()
  sessions.set(ctx, { bot, world, ready })
  return ready
}

/** @param {Bot} bot @param {z.infer<typeof direction>} value */
function resolveDirection (bot, value) {
  if (value !== 'forward') return value
  const dx = -Math.sin(bot.entity.yaw)
  const dz = -Math.cos(bot.entity.yaw)
  return Math.abs(dx) > Math.abs(dz) ? (dx > 0 ? 'east' : 'west') : (dz > 0 ? 'south' : 'north')
}

/** @param {Context} ctx @param {z.infer<typeof options>} args */
async function start (ctx, args) {
  const s = await session(ctx)
  if (s.task?.active || ctx.locks.isBusy()) throw new ToolError('BUSY', 'Stop the current action before starting another mine task.')
  if (s.record && !s.record.returned) throw new ToolError('BUSY', 'The previous mine has not confirmed a return. Use return_from_mine or resume_mining before replacing its checkpoint.')
  if (!s.bot.registry.itemsByName[args.item]) throw new ToolError('INVALID_ARGS', `Unknown inventory item: ${args.item}`)
  if (args.depth + args.length === 0) throw new ToolError('INVALID_ARGS', 'Specify a non-empty mine passage.')
  const record = tasks.newRecord(s.bot, { ...args, direction: resolveDirection(s.bot, args.direction) }, s.world)
  if (s.record) {
    const floors = [...s.record.protectedFloors, ...s.record.route.map(p => ({ ...p, y: p.y - 1 }))]
    record.protectedFloors = Array.from(new Map(floors.map(p => [safety.key(p), p])).values())
  }
  const task = new tasks.MineTask(ctx, s.bot, record, s.store)
  // Reject missing supplies/unsafe entrances before acknowledging a running task.
  task.resources()
  task.validateRoute(record.route)
  s.record = record
  s.task = task
  bindContinuation(ctx, task, s.world)
  return task.start()
}

/** @param {Context} ctx @param {tasks.MineTask} task @param {string} world */
function bindContinuation (ctx, task, world) {
  const locks = ctx.locks
  if (!(locks instanceof ActionController)) return
  locks.linkTask(task.record.id, world)
  locks.continuation(() => task.pause(), () => locks.run('resume_mining', 1, () => resumeSaved(ctx, task.returnRequested)))
}

/** @param {Context} ctx @param {boolean} returnOnly */
async function resumeSaved (ctx, returnOnly) {
  const s = await session(ctx)
  if (s.task?.active) {
    if (!returnOnly) throw new ToolError('BUSY', 'Mining is already running.')
    s.task.returnRequested = true
    return s.task.snapshot()
  }
  if (ctx.locks.isBusy()) throw new ToolError('BUSY', 'Stop the current action first.')
  if (!s.record) throw new ToolError('NOT_FOUND', 'There is no saved mine in this world.')
  if (Math.min(...s.record.route.map(p => s.bot.entity.position.distanceTo(new Vec3(p.x + 0.5, p.y, p.z + 0.5)))) > 2) {
    throw new ToolError('FORBIDDEN', 'Bot is outside the saved mine corridor; locate its entrance before resuming.')
  }
  const task = new tasks.MineTask(ctx, s.bot, s.record, s.store)
  task.returnRequested = returnOnly
  task.validateRoute(s.record.route)
  if (!returnOnly) task.resources()
  s.task = task
  bindContinuation(ctx, task, s.world)
  return task.start(returnOnly)
}

/** @param {Registrar} reg */
export function registerMining (reg) {
  reg({ name: 'mine_resources', group: 'build',
    description: 'Start a BACKGROUND mine task at the current feet position. Builds a 1-wide, 3-high stair passage, optionally extends a tunnel, verifies walking return routes, then actually returns to the entrance. Bring a suitable pickaxe, food, torches and 8 spare blocks. Excavates natural terrain only. Returns a task id, not completed work. Inspect mining_status; use cancel_task to stop immediately or return_from_mine to return safely.',
    inputSchema: input, annotations: { title: 'Mine and return', destructiveHint: true },
    handler: (args, ctx) => start(ctx, options.parse(args)) })
  reg({ name: 'mining_status', group: 'build', description: 'Read the current/last mine task, actual inventory gains, entrance and confirmed return status. A saved interrupted task requires explicit resume or return.',
    inputSchema: {}, annotations: { title: 'Mining status', readOnlyHint: true },
    handler: async (_args, ctx) => { const s = await session(ctx); return s.task?.snapshot() ?? (s.record ? { ...s.record, route: undefined, protectedFloors: undefined, active: false } : { active: false, task: null }) } })
  for (const returnOnly of [false, true]) {
    reg({ name: returnOnly ? 'return_from_mine' : 'resume_mining', group: 'build',
      description: returnOnly ? 'Return along the saved mine passage without digging or placing blocks. During mining, finish the current safe step then return. Fails if the recorded route is blocked.'
        : 'Explicitly resume an interrupted mine after checking terrain, resources and the saved return route. Never resumes automatically on reconnect.',
      inputSchema: {}, annotations: { title: returnOnly ? 'Return from mine' : 'Resume mine', destructiveHint: !returnOnly },
      handler: (_args, ctx) => resumeSaved(ctx, returnOnly) })
  }
}

/** @template {z.ZodRawShape} S @param {import('./registry.js').ToolDef<S>} def @returns {import('./registry.js').ToolDef<S>} */
export function wrapMiningTools (def) {
  const original = def.handler
  const legacy = def.name === 'dig_staircase' || def.name === 'dig_tunnel'
  const description = legacy ? 'Starts a background 1-wide, 3-high mine passage with protected floors and verified walking return. Depth <=16, length <=32. Always returns to the entrance. Read mining_status for actual completion; cancel_task stops immediately.'
    : def.name === 'goto' ? def.description + ' Travel never digs or scaffolds; underground targets require mine_resources.'
      : def.name === 'collect_block' ? def.description + ' Travel never digs a path. Results report actual inventory gains; buried targets require mine_resources.' : def.description
  return { ...def, description, handler: async (args, ctx) => {
    try {
      const bot = ctx.manager.botOrNull()
      const s = bot && ctx.manager.status === 'online' ? await session(ctx) : null
      if (legacy) {
        const spec = z.object({ direction, depth: z.number().int().min(1).max(16).optional(),
          length: z.number().int().min(1).max(32).optional(), width: z.literal(1).optional(), height: z.number().min(2).max(3).optional() }).parse(args)
        return start(ctx, { direction: spec.direction, depth: def.name === 'dig_staircase' ? (spec.depth ?? 8) : 0,
          length: def.name === 'dig_tunnel' ? (spec.length ?? 8) : 0, item: 'cobblestone', count: 0 })
      }
      if (def.name === 'configure_movements') {
        const settings = z.object({ canDig: z.boolean().optional(), allow1by1towers: z.boolean().optional(),
          allowParkour: z.boolean().optional(), maxDropDown: z.number().optional(), scaffoldingBlocks: z.array(z.string()).optional() }).parse(args)
        if (settings.canDig || settings.allow1by1towers || settings.allowParkour || (settings.maxDropDown ?? 1) > 1 || settings.scaffoldingBlocks?.length) {
          throw new ToolError('FORBIDDEN', 'Travel cannot dig, tower, parkour or drop more than one block. Use a mine task for excavation.')
        }
      }
      const before = def.name === 'collect_block' && s ? new Map(s.bot.inventory.items().map(i => [i.name, tasks.inventoryCount(s.bot, i.name)])) : null
      const result = await original(args, ctx)
      if (before && s) {
        const names = new Set(s.bot.inventory.items().map(i => i.name))
        const inventoryGains = Array.from(names, name => ({ name, count: tasks.inventoryCount(s.bot, name) - (before.get(name) ?? 0) })).filter(i => i.count > 0)
        if (!inventoryGains.length) throw new ToolError('NOT_FOUND', 'No inventory gain was observed. Do not report resources collected; use a reachable exposed block or mine_resources.')
        return { ok: true, inventoryGains }
      }
      if (s && ['get_state', 'pathfinder_status'].includes(def.name) && result && typeof result === 'object') {
        return { ...result, activeAction: s.task?.active ? 'mine_resources' : ctx.locks.current, miningTask: s.task?.snapshot() ?? null }
      }
      return result
    } catch (error) {
      if (error instanceof safety.MiningError) throw new ToolError('FORBIDDEN', `${error.code}: ${error.message}`)
      throw error
    }
  } }
}
