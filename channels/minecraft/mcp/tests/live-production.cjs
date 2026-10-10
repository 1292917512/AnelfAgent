// Real server cases use the isolated harness in live-survival.cjs --suite production.
const assert = require('node:assert/strict')
const { setTimeout: delay } = require('node:timers/promises')

module.exports = async function productionCases ({ test, call, command, waitFor, bot, ctx, username, metadata }) {
  const count = name => bot.inventory.items().filter(item => item.name === name).reduce((sum, item) => sum + item.count, 0)
  async function setup () { await call('configure_survival', { enabled: false, intervalMs: 250 }) }
  async function give (name, amount) {
    const before = count(name)
    await command(`give ${username} minecraft:${name} ${amount}`)
    await waitFor(`inventory received ${name}`, () => count(name) >= before + amount)
  }
  async function finish () {
    let state
    await waitFor('production terminal after actual cleanup', async () => {
      state = await call('production_status')
      return state.active === false
    }, 60000)
    assert.equal(ctx.locks.action, null, 'Terminal notification must follow control release')
    return state
  }
  async function craft (item = 'wooden_pickaxe', mode = 'craft', amount = 1, origin) {
    const admitted = await call('prepare_item', { item, mode, count: amount }, origin)
    assert.equal(admitted.phase, 'running')
    return finish()
  }
  async function readyTable () {
    await command('setblock 2 64 0 crafting_table')
    await waitFor('existing table loaded', () => bot.blockAt(bot.entity.position.floored().offset(2, 0, 0))?.name === 'crafting_table')
  }

  for (const wood of ['oak', 'birch']) await test(`production_${wood}_logs_to_pickaxe`, async () => {
    await setup(); await give(`${wood}_log`, 3)
    const state = await craft()
    assert.equal(state.phase, 'completed', state.reason)
    assert.equal(state.created, 1); assert.equal(count('wooden_pickaxe'), 1)
    assert.equal(state.inventoryClean, true)
    assert.equal(state.table.placed, true)
    assert.equal(bot.blockAt(bot.entity.position.floored().offset(
      state.table.position.x - Math.floor(bot.entity.position.x),
      state.table.position.y - Math.floor(bot.entity.position.y),
      state.table.position.z - Math.floor(bot.entity.position.z)))?.name, 'crafting_table')
    assert.equal(count(`${wood}_log`), 0)
  })
  await test('production_reuses_existing_table_and_partial_materials', async () => {
    await setup(); await readyTable(); await give('oak_planks', 3); await give('stick', 2)
    const state = await craft()
    assert.equal(state.phase, 'completed', state.reason)
    assert.equal(state.stepsTotal, 1)
    assert.equal(state.table.reused, true); assert.equal(state.table.placed, false)
    assert.equal(state.inventoryClean, true)
  })
  await test('production_places_carried_workbench', async () => {
    await setup(); await give('crafting_table', 1); await give('oak_planks', 3); await give('stick', 2)
    const state = await craft()
    assert.equal(state.phase, 'completed', state.reason)
    assert.equal(state.stepsTotal, 2)
    assert.equal(state.table.placed, true)
    assert.equal(count('crafting_table'), 0)
  })
  await test('production_existing_tool_is_not_reported_as_new', async () => {
    await setup(); await give('wooden_pickaxe', 1)
    const state = await craft('wooden_pickaxe', 'ensure')
    assert.equal(state.phase, 'completed', state.reason)
    assert.equal(state.created, 0); assert.equal(state.reused, 1); assert.equal(state.stepsTotal, 0)
    await assert.rejects(call('prepare_item', { item: 'wooden_pickaxe', count: 1, mode: 'craft' }), /MISSING_MATERIALS/)
    assert.equal(count('wooden_pickaxe'), 1)
  })
  await test('production_additional_tool_really_increases_inventory', async () => {
    await setup(); await readyTable(); await give('wooden_pickaxe', 1); await give('oak_log', 2)
    const state = await craft()
    assert.equal(state.phase, 'completed', state.reason)
    assert.equal(state.created, 1); assert.equal(state.available, 2); assert.equal(state.reused, 0)
  })
  await test('production_insufficient_materials_do_not_consume_logs', async () => {
    await setup(); await give('oak_log', 2)
    await assert.rejects(call('prepare_item', { item: 'wooden_pickaxe', count: 1, mode: 'craft' }), /MISSING_MATERIALS/)
    assert.equal(count('oak_log'), 2); assert.equal(count('oak_planks'), 0)
  })
  await test('production_full_inventory_stops_before_crafting', async () => {
    await setup(); await give('oak_log', 3); await give('cobblestone', 2240)
    assert.equal(bot.inventory.emptySlotCount(), 0)
    await assert.rejects(call('prepare_item', { item: 'wooden_pickaxe', count: 1, mode: 'craft' }), /INVENTORY_FULL/)
    assert.equal(count('oak_log'), 3)
  })
  await test('production_stick_quantity_uses_recipe_batches', async () => {
    await setup(); await give('oak_planks', 4)
    const state = await craft('stick', 'craft', 5)
    assert.equal(state.phase, 'completed', state.reason)
    assert.equal(state.created, 8); assert.equal(count('stick'), 8)
  })
  await test('production_stop_during_real_grid_click_restores_materials', async () => {
    await setup(); await give('oak_log', 3)
    const old = metadata('production-before-stop')
    let sawGrid = false, stopping
    const dropped = []
    const spawn = entity => { if (entity.name === 'item') dropped.push(entity.id) }
    const grid = packet => {
      if (packet.windowId !== 0 || !packet.items.slice(1, 5).some(item => item.itemCount > 0) || sawGrid) return
      sawGrid = true
      stopping = call('cancel_task')
    }
    bot.on('entitySpawn', spawn); bot._client.on('window_items', grid)
    try {
      await call('prepare_item', { item: 'wooden_pickaxe', count: 1, mode: 'craft' }, old)
      await waitFor('server-confirmed input grid followed by stop', () => sawGrid)
      await stopping
      const state = await finish()
      assert.equal(state.phase, 'cancelled', state.reason)
      assert.equal(state.inventoryClean, true, state.reason)
      assert.equal(count('wooden_pickaxe'), 0)
      assert.ok(count('oak_log') * 4 + count('oak_planks') >= 12, 'Interrupted first recipe must retain all input material')
      await delay(800)
      assert.deepEqual(dropped, [], 'Stopping must not eject transient crafting materials')
      await assert.rejects(call('prepare_item', { item: 'wooden_pickaxe', count: 1, mode: 'craft' }, old), /CANCELLED|predates/)
    } finally {
      bot.removeListener('entitySpawn', spawn); bot._client.removeListener('window_items', grid)
    }
  })
  await test('production_partial_batch_reports_delivered_sticks', async () => {
    await setup(); await give('oak_planks', 4)
    let stopping
    const grid = packet => {
      if (stopping || packet.windowId !== 0) return
      const produced = packet.items.slice(9).some(item => item.itemId === bot.registry.itemsByName.stick.id && item.itemCount === 4)
      if (produced && packet.items.slice(1, 5).some(item => item.itemCount > 0)) stopping = call('cancel_task')
    }
    bot._client.on('window_items', grid)
    try {
      await call('prepare_item', { item: 'stick', count: 8, mode: 'craft' })
      await waitFor('first batch delivered and next recipe interrupted', () => Boolean(stopping))
      await stopping
      const state = await finish()
      assert.equal(state.phase, 'cancelled', state.reason)
      assert.equal(state.created, 4); assert.equal(state.available, 4)
      assert.equal(state.inventoryClean, true)
      assert.equal(count('oak_planks'), 2)
    } finally { bot._client.removeListener('window_items', grid) }
  })
  await test('production_hurt_during_crafting_drains_before_survival', async () => {
    await setup(); await give('oak_log', 3)
    await call('configure_survival', { enabled: true, intervalMs: 250 })
    const old = metadata('production-before-hurt')
    let damage
    const grid = packet => {
      if (!damage && packet.windowId === 0 && packet.items.slice(1, 5).some(item => item.itemCount > 0)) {
        damage = command(`damage ${username} 2 minecraft:generic`)
      }
    }
    bot._client.on('window_items', grid)
    try {
      await call('prepare_item', { item: 'wooden_pickaxe', count: 1, mode: 'craft' }, old)
      await waitFor('real damage during crafting', () => Boolean(damage))
      await damage
      await waitFor('production and survival both drained', async () =>
        !(await call('production_status')).active && !ctx.locks.survival.pending && !ctx.locks.action)
      const state = await call('production_status')
      assert.equal(state.phase, 'cancelled', state.reason)
      assert.equal(state.inventoryClean, true)
      assert.equal(count('wooden_pickaxe'), 0)
      await assert.rejects(call('prepare_item', { item: 'wooden_pickaxe', count: 1, mode: 'craft' }, old), /CANCELLED|interrupted/)
      assert.equal(metadata().epoch, old.epoch, 'Local danger must revoke the request without a host stop')
    } finally { bot._client.removeListener('window_items', grid) }
  })
}
