// @ts-check
/** Model Experience: bounded gathering confirms pickup/return; auto logTarget ends only between whole trees.
 * Token effect: one optional integer replaces model polling for a carried-log crafting threshold.
 * Cache effect: the installed tool schema changes; progress/inventory facts stay in dynamic results/events.
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
    'Start BACKGROUND surface gathering in an explicitly authorized area (x,y,z center within 8 blocks, radius <=8). Choose dirt for surface excavation; grass blocks, podzol, mycelium and dirt paths are harvested too because all of them dig into the dirt item, or choose stone/cobblestone or a log type; block=auto is reserved for autonomous routine work: a nearest-first mix of surface soil and whole natural trees covering every trunk log species in this registry (pale oak included), while explicit player requests must name the block. Mixed auto orders stop digging soil as soon as carried dirt reaches 64, even within the current order; consumed dirt can be replenished in subsequent work. While the backpack holds fewer than ten dirt, the top soil layer of the passed chunk outranks every tree, so tree climbing never starves its scaffold reserve. count is NEW matching inventory units, excluding existing stock and pre-supplies. Default mode=count stops at the requested new inventory count. Explicit mode=tree gathers one connected log component; count is a hard maximum, checked before digging, and the complete connected tree including its highest log must be cleared or the task remains blocked. It preserves the initial component through cleanup without discovering more trees. Oversized or too-high trees are rejected before digging. In auto orders a tree that cannot be finished inside the remaining budget is skipped whole, never left floating. Loaded targets from three blocks below the start through sixteen blocks above it are eligible: prefer level routes and low leaf clearing, then allow only bounded leaf clearing above the current feet; routine temporary scaffolds use dirt only, maintain a 64-dirt reserve, and persist every placed support until removal is confirmed. Cobblestone, stone and planks are emergency escape material and are not consumed by routine gathering. Never digs support floors, tunnels or arbitrary travel blocks; use mine_resources underground. Requires two free inventory slots and a suitable durable tool (logs allow hands). Returns to start after completion or a recoverable failure, switching to the bounded scaffold route if the level return is blocked. Optional chest must be explicitly authorized and within sight/reach at start: withdraw uses final inventory targets; deposit=true stores only newly gathered output after confirmed return. Stop/death/disconnect never auto-return or resume; a persisted temporary-support record is checked on the next nearby task. Admission is not completion: read gathering_status or wait for gather_progress. Does not recognize player buildings; confirm the area may be harvested.',
    inputSchema: { ...position, block: z.enum(['stone', 'cobblestone', 'dirt', 'oak_log', 'birch_log', 'spruce_log', 'jungle_log', 'acacia_log', 'dark_oak_log', 'cherry_log', 'mangrove_log', 'pale_oak_log', 'auto']),
      count: z.number().int().min(1).max(64), radius: z.number().int().min(1).max(8).default(3),
      mode: z.enum(['count', 'tree']).default('count').describe('count collects exactly count new units; tree collects one connected log component only if it fits within count, the hard log budget'),
      chunk: z.object({ x: z.number().int(), z: z.number().int() }).optional().describe('Autonomous routine work only: chunk coordinates floor(pos/16). Dirt orders clamp the whole authorized face to that 16x16 chunk column for chunk-aligned square clearing; auto orders clamp only the top-priority soil layer while the backpack holds fewer than ten dirt.'),
      logsOnly: z.boolean().optional().describe('Mixed auto orders only: never dig soil this order — used when the autonomous dirt quota is already met.'),
      logTarget: z.number().int().min(1).max(64).optional().describe('Mixed auto orders only: finish the current whole tree and return once total carried natural logs reach this target. May complete before the new-unit count budget is exhausted.'),
      chest: z.object(position).optional(), deposit: z.boolean().default(false),
      withdraw: z.array(z.object({ item: z.string().regex(/^[a-z0-9_]+$/), count: z.number().int().min(1).max(512) })).max(8).default([]),
    }, handler: (args, ctx) => {
      const bot = ctx.manager.requireBot()
      if (args.logTarget !== undefined && args.block !== 'auto') throw new ToolError('INVALID_ARGS', 'logTarget requires an auto order.')
      if (bot.currentWindow || !inventoryClean(bot)) throw new ToolError('BUSY', 'Recover existing inventory windows first.')
      if (args.mode === 'tree' && !args.block.endsWith('_log')) throw new ToolError('INVALID_ARGS', 'Tree mode requires a log block type.')
      if (args.block === 'auto' && args.deposit) throw new ToolError('INVALID_ARGS', 'Auto orders mix output items and cannot deliver to a chest; name the block for deliveries.')
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
