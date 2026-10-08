// @ts-check
/** Model Experience: one background recipe task exposes actual inventory, placement and cleanup facts.
 * Token effect: two small schemas replace intermediate crafting/placement model rounds.
 * Cache effect: only installed schemas change; live task results remain in tool results/events.
 */
import { z } from 'zod'
import { ToolError } from '../util/errors.js'
import { planProduction, productionItems } from './anelf-production-plan.mjs'
import { findTable, inventoryClean, ProductionTask } from './anelf-production-task.mjs'
import { workbenchPlacementHints } from './anelf-placement-hints.mjs'

/** @typedef {import('../context.js').ToolContext} Context */
/** @typedef {import('./registry.js').Registrar} Registrar */
/** @type {WeakMap<Context,ProductionTask>} */
const tasks = new WeakMap()

/** @param {Registrar} reg */
export function registerProduction (reg) {
  reg({ name: 'prepare_item', group: 'crafting', description:
    'Start a BACKGROUND inventory-only preparation task for wooden tools, crafting_table or sticks. Plans all ingredients first, crafts intermediates, reuses a visible workbench within 4 blocks or places one beside the bot, and verifies server inventory. count means output items, not recipe operations. ensure reuses existing target items; craft makes additional items. No gathering, walking, delivery or automatic resumption. Returns task id, NOT completion; wait for production_progress or read production_status. Stops drain crafting before releasing ownership.',
  inputSchema: {
    item: z.enum(productionItems), count: z.number().int().min(1).max(8).default(1),
    mode: z.enum(['ensure', 'craft']).default('ensure'),
  }, handler: (args, ctx) => {
    const bot = ctx.manager.requireBot()
    if (!inventoryClean(bot)) throw new ToolError('BUSY', 'Close other windows and recover the cursor/crafting grid before production.')
    const table = findTable(bot), plan = planProduction(bot, args.item, args.count, args.mode, Boolean(table))
    if (plan.steps.length && bot.inventory.emptySlotCount() < 2) throw new ToolError('INVENTORY_FULL_NO_CHEST', 'Keep at least two empty inventory slots for intermediate output and safe recovery; nothing was consumed.')
    if (plan.steps.some(step => step.kind === 'place_table') && !workbenchPlacementHints(bot).length) {
      throw new ToolError('FORBIDDEN', 'No safe workbench space within reach; nothing was consumed. Move to an open, supported position.')
    }
    const task = new ProductionTask(ctx, bot, plan, table)
    const result = task.start()
    tasks.set(ctx, task)
    return result
  } })
  reg({ name: 'production_status', group: 'crafting', annotations: { readOnlyHint: true }, inputSchema: {},
    description: 'Read the last preparation task, world, actual new output versus reused inventory, workbench and cleanup confirmation. Terminal facts stay frozen; reconnect never resumes work. No task means no completion evidence.',
    handler: (_args, ctx) => tasks.get(ctx)?.snapshot() ?? { active: false, task: null } })
}
