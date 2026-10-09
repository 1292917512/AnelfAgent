// @ts-check
/** Bounded, read-only recipe planning against the installed Minecraft recipe registry. */
import { performance } from 'node:perf_hooks'
import { ToolError } from '../util/errors.js'

/** @typedef {import('mineflayer').Bot} Bot */
/** @typedef {import('prismarine-recipe').Recipe} Recipe */
/** @typedef {{kind:'craft',item:string,recipe:Recipe,operations:number}|{kind:'place_table'}} Step */
/** @typedef {{stock:Map<number,number>,steps:Step[],table:boolean}} State */
export const productionItems = /** @type {const} */ ([
  'wooden_pickaxe', 'wooden_axe', 'wooden_shovel', 'wooden_hoe', 'wooden_sword',
  'stone_pickaxe', 'stone_axe', 'stone_shovel', 'stone_hoe', 'stone_sword',
  'shield', 'torch', 'crafting_table', 'stick',
  'oak_planks', 'birch_planks', 'spruce_planks', 'jungle_planks',
  'acacia_planks', 'dark_oak_planks', 'cherry_planks', 'mangrove_planks',
])

/** @param {Bot} bot @param {string} name */
export function itemCount (bot, name) {
  return bot.inventory.items().filter(item => item.name === name).reduce((sum, item) => sum + item.count, 0)
}

/** @param {State} state @returns {State} */
function copy (state) { return { stock: new Map(state.stock), steps: [...state.steps], table: state.table } }

/** count is final inventory quantity in ensure mode, and additional output in craft mode.
 * additional is read-only hypothetical stock for preflight, never an inventory receipt.
 * @param {Bot} bot @param {string} item @param {number} count @param {'ensure'|'craft'} mode @param {boolean} hasTable
 * @param {ReadonlyMap<number,number>} [additional]
 */
export function planProduction (bot, item, count, mode, hasTable, additional = new Map()) {
  const target = bot.registry.itemsByName[item]
  const maxCount = item.endsWith('_planks') ? 192 : 8
  if (!target || !productionItems.some(name => name === item) || !Number.isInteger(count) || count < 1 || count > maxCount) {
    throw new ToolError('INVALID_ARGS', `Supported wooden/stone tools, shield, torch, crafting_table, stick or building planks; count must be 1..${maxCount} output items.`)
  }
  const before = itemCount(bot, item), required = mode === 'craft' ? before + count : count
  /** @type {Map<number,number>} */
  const stock = new Map()
  for (const entry of bot.inventory.items()) stock.set(entry.type, (stock.get(entry.type) ?? 0) + entry.count)
  for (const [id, amount] of additional) stock.set(id, (stock.get(id) ?? 0) + amount)
  const deadline = performance.now() + 50
  let visited = 0
  /** @type {Map<string,number>} */
  const missing = new Map()
  /** @param {string} name @param {number} amount @param {State} state @param {string[]} trail @returns {Generator<State,void,unknown>} */
  function * ensure (name, amount, state, trail) {
    if (++visited > 2048 || performance.now() > deadline) throw new ToolError('TIMEOUT', 'Recipe search budget reached; no items were changed.')
    const definition = bot.registry.itemsByName[name]
    if (!definition) return
    const available = state.stock.get(definition.id) ?? 0
    if (available >= amount) { yield state; return }
    if (trail.includes(name) || trail.length >= 5) return
    if (!productionItems.some(value => value === name) && !name.endsWith('_planks')) {
      missing.set(name, Math.min(missing.get(name) ?? Infinity, amount - available))
      return
    }
    const recipes = bot.recipesAll(definition.id, null, true).filter(recipe =>
      recipe.result.count > 0 && recipe.delta.some(delta => delta.count < 0))
    // Existing ingredients are tried first; alternative recipes are still explored on failure.
    recipes.sort((a, b) => score(b) - score(a))
    /** @param {Recipe} recipe */
    function score (recipe) { return recipe.delta.filter(d => d.count < 0).reduce((sum, d) => sum + Math.min(state.stock.get(d.id) ?? 0, -d.count), 0) }
    for (const recipe of recipes) {
      const operations = Math.ceil((amount - available) / recipe.result.count)
      const inputs = recipe.delta.filter(delta => delta.count < 0)
      /** @param {number} index @param {State} current @returns {Generator<State,void,unknown>} */
      function * consume (index, current) {
        const input = inputs[index]
        if (!input) { yield current; return }
        const name = bot.registry.items[input.id]?.name
        if (!name) return
        for (const ready of ensure(name, -input.count * operations, current, [...trail, definition.name])) {
          const next = copy(ready)
          next.stock.set(input.id, (next.stock.get(input.id) ?? 0) + input.count * operations)
          yield * consume(index + 1, next)
        }
      }
      /** @returns {Generator<State,void,unknown>} */
      function * facility () {
        if (!recipe.requiresTable || state.table) { yield state; return }
        for (const ready of ensure('crafting_table', 1, state, [...trail, definition.name])) {
          const next = copy(ready), tableId = bot.registry.itemsByName.crafting_table.id
          next.stock.set(tableId, (next.stock.get(tableId) ?? 0) - 1)
          next.table = true
          next.steps.push({ kind: 'place_table' })
          yield next
        }
      }
      for (const withTable of facility()) for (const ready of consume(0, withTable)) {
        const next = copy(ready)
        for (const delta of recipe.delta.filter(delta => delta.count > 0)) {
          next.stock.set(delta.id, (next.stock.get(delta.id) ?? 0) + delta.count * operations)
        }
        next.steps.push({ kind: 'craft', item: name, recipe, operations })
        if (next.steps.length <= 32 && next.steps.reduce((n, step) => n + (step.kind === 'craft' ? step.operations : 1), 0) <= 64) yield next
      }
    }
  }
  const plan = ensure(item, required, { stock, steps: [], table: hasTable }, []).next().value
  if (!plan) throw new ToolError('MISSING_MATERIALS', 'No complete recipe plan using current inventory; nothing was consumed.',
    [`Alternative raw-material deficits (not a combined shopping list): ${JSON.stringify(Object.fromEntries(missing))}. Recipe variants are those supplied by the installed registry.`])
  const projected = new Map(stock), capacity = bot.inventory.inventoryEnd - bot.inventory.inventoryStart
  for (const step of plan.steps) {
    const changes = step.kind === 'craft' ? step.recipe.delta.map(d => ({ id: d.id, count: d.count * step.operations }))
      : [{ id: bot.registry.itemsByName.crafting_table.id, count: -1 }]
    for (const change of changes) projected.set(change.id, (projected.get(change.id) ?? 0) + change.count)
    const slots = Array.from(projected, ([id, amount]) => Math.ceil(Math.max(0, amount) / bot.registry.items[id].stackSize)).reduce((sum, n) => sum + n, 0)
    if (slots > capacity) throw new ToolError('INVENTORY_FULL_NO_CHEST', 'Planned output cannot fit in inventory; nothing was consumed. Free slots before retrying.')
  }
  return { item, count, mode, before, required, steps: plan.steps }
}
