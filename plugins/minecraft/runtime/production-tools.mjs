// @ts-check
/** Model Experience: one preparation task can gather the deficit in an explicit area, return and craft.
 * Token effect: one optional area schema replaces separate model rounds for shortage gathering and crafting.
 * Cache effect: only installed schemas change; live task results remain in tool results/events.
 */
import { z } from 'zod'
import { ToolError } from '../util/errors.js'
import { planProduction, productionItems } from './anelf-production-plan.mjs'
import { findTable, inventoryClean, validateProductionSpace, ProductionTask } from './anelf-production-task.mjs'
import { PreparationTask } from './anelf-preparation-task.mjs'
import { preparationLogs } from './anelf-preparation-plan.mjs'

/** @typedef {import('../context.js').ToolContext} Context */
/** @typedef {import('./registry.js').Registrar} Registrar */
/** @type {WeakMap<Context,ProductionTask|PreparationTask>} */
const tasks = new WeakMap()

/** @param {Registrar} reg */
export function registerProduction (reg) {
  reg({ name: 'prepare_item', group: 'crafting', description:
    'Start BACKGROUND preparation of wooden/stone tools, a shield, torches, crafting_table, sticks or building planks. count is output items, not recipe operations; ensure reuses existing targets, craft makes additional output. Default uses inventory only. Optional gather explicitly authorizes one log type in a nearby area: preflights the minimum extra logs within maxCount, prefers level routes and low leaf clearing, then permits bounded obstacles above the starting feet and one inventory scaffold block for targets up to three blocks higher; every temporary support must be removed and confirmed before replanning from real inventory and crafting. Center within 8 blocks, radius <=4; never excavates support floors, tunnels or arbitrary routes. Cannot identify player buildings: only use an authorized area. No need to gather if materials already suffice. Reuses a visible workbench within 4 blocks or places one beside the start; requires two recovery slots. No chest use, delivery, retries, task expansion or automatic resumption. Returns task id, NOT completion; wait for production_progress or read production_status. One owner and final report cover all stages; stop drains actual work and inventory.',
  inputSchema: {
    item: z.enum(productionItems), count: z.number().int().min(1).max(192).default(1),
    mode: z.enum(['ensure', 'craft']).default('ensure'),
    gather: z.object({ block: z.enum(preparationLogs), x: z.number().int(), y: z.number().int(), z: z.number().int(),
      radius: z.number().int().min(1).max(4).default(3), maxCount: z.number().int().min(1).max(32).default(8),
    }).optional(),
  }, handler: (args, ctx) => {
    const bot = ctx.manager.requireBot()
    if (!inventoryClean(bot)) throw new ToolError('BUSY', 'Close other windows and recover the cursor/crafting grid before production.')
    if (args.gather) {
      const area = args.gather
      if (Math.hypot(area.x - bot.entity.position.x, area.y - bot.entity.position.y, area.z - bot.entity.position.z) > 8) {
        throw new ToolError('FORBIDDEN', 'Gathering center must be within 8 blocks of the preparation position; use goto goalType=near range=2 first, then retry from the arrived position.')
      }
      const task = new PreparationTask(ctx, bot, { ...args, gather: area })
      const result = task.start(); tasks.set(ctx, task); return result
    }
    const table = findTable(bot), plan = planProduction(bot, args.item, args.count, args.mode, Boolean(table))
    validateProductionSpace(bot, plan)
    const task = new ProductionTask(ctx, bot, plan, table)
    const result = task.start()
    tasks.set(ctx, task)
    return result
  } })
  reg({ name: 'production_status', group: 'crafting', annotations: { readOnlyHint: true }, inputSchema: {},
    description: 'Read the last preparation task: stage, plannedGather, optional gathering pickup/return facts, actual new output versus reused inventory, workbench and cleanup. Collected logs are not a crafted tool; terminal facts stay frozen and reconnect never resumes work.',
    handler: (_args, ctx) => tasks.get(ctx)?.snapshot() ?? { active: false, task: null } })
}
