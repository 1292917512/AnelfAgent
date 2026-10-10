// @ts-check
/** Model Experience: one bounded surface gathering task confirms pickup, return and optional chest delivery.
 * Token effect: two schemas replace per-block planning and inventory polling by the model.
 * Cache effect: stable schemas; progress and inventory facts remain in results/events.
 */
import { z } from 'zod'
import { ToolError } from '../util/errors.js'
import { inventoryClean } from './anelf-production-task.mjs'
import { supplyContainer } from './anelf-supply-task.mjs'
import { GatherTask } from './anelf-gather-task.mjs'

/** @type {WeakMap<import('../context.js').ToolContext,GatherTask>} */
const tasks = new WeakMap()
const position = { x: z.number().int(), y: z.number().int(), z: z.number().int() }

/** @param {import('./registry.js').Registrar} reg */
export function registerGathering (reg) {
  reg({ name: 'gather_resources', group: 'gathering', description:
    'Start BACKGROUND surface gathering in an explicitly authorized area (x,y,z center within 8 blocks, radius <=4). Choose stone/cobblestone or a log type; count is NEW matching inventory units, excluding existing stock and pre-supplies. If the tree species is unknown, first use find_blocks with the complete supported log-name list; never infer the species from stale task output. Once a log type is selected, the request stays on that species and selects one tree, continuing through its connected trunk/crown until no same-species logs remain in the bounded tree envelope, even when count is already met, so it never leaves a floating trunk; if the complete tree is above the bounded height, stop before digging and report the target as too high. Loaded targets from the start height through +3 are eligible: prefer level routes and low leaf clearing, then allow only bounded obstacles above the starting feet; a one-block scaffold from inventory dirt/cobblestone/stone/planks may be used for high targets and must be removed and confirmed afterward. Never digs support floors, tunnels or arbitrary travel blocks; use mine_resources underground. Requires two free inventory slots and a suitable durable tool (logs allow hands). Returns to start after completion or a recoverable failure, switching to the bounded scaffold route if the level return is blocked. Optional chest must be explicitly authorized and within sight/reach at start: withdraw uses final inventory targets; deposit=true stores only newly gathered output after confirmed return. Stop/death/disconnect never auto-return or resume. Admission is not completion: read gathering_status or wait for gather_progress. Does not recognize player buildings; confirm the area may be harvested.',
    inputSchema: { ...position, block: z.enum(['stone', 'cobblestone', 'oak_log', 'birch_log', 'spruce_log', 'jungle_log', 'acacia_log', 'dark_oak_log', 'cherry_log', 'mangrove_log']),
      count: z.number().int().min(1).max(32), radius: z.number().int().min(1).max(4).default(3),
      chest: z.object(position).optional(), deposit: z.boolean().default(false),
      withdraw: z.array(z.object({ item: z.string().regex(/^[a-z0-9_]+$/), count: z.number().int().min(1).max(512) })).max(8).default([]),
    }, handler: (args, ctx) => {
      const bot = ctx.manager.requireBot()
      if (bot.currentWindow || !inventoryClean(bot)) throw new ToolError('BUSY', 'Recover existing inventory windows first.')
      if (Math.hypot(args.x - bot.entity.position.x, args.y - bot.entity.position.y, args.z - bot.entity.position.z) > 8) throw new ToolError('FORBIDDEN', 'Gathering center must be within 8 blocks of the start; use goto goalType=near range=2 first, then retry from the arrived position.')
      if ((args.deposit || args.withdraw.length) && !args.chest) throw new ToolError('INVALID_ARGS', 'Supply and delivery require explicit chest coordinates.')
      if (args.chest) supplyContainer(bot, args.chest)
      const task = new GatherTask(ctx, bot, args)
      const result = task.start(); tasks.set(ctx, task); return result
    } })
  reg({ name: 'gathering_status', group: 'gathering', inputSchema: {}, annotations: { readOnlyHint: true },
    description: 'Read the latest bounded gather task: blocks removed, actual new inventory gains, remaining count, confirmed return and optional delivered count. Terminal facts are frozen; blocked/cancelled is not completion.',
    handler: (_args, ctx) => tasks.get(ctx)?.snapshot() ?? { active: false, task: null } })
}
