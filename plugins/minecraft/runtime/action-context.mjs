// @ts-check
/** Trusted host request provenance, transported outside the model-visible tool arguments. */
import { AsyncLocalStorage } from 'node:async_hooks'
import { z } from 'zod'
import { ToolError } from '../util/errors.js'

const schema = z.object({ producer: z.string().min(1).max(64), epoch: z.number().int(), floor: z.number().int().nonnegative(),
  requestId: z.string().min(1).max(128), scope: z.string().max(256), actor: z.string().max(128),
  delegationId: z.string().max(128), worldId: z.string().max(256) })
/** @typedef {z.infer<typeof schema>} Origin */

export class ActionContext {
  constructor () {
    /** @type {AsyncLocalStorage<Origin|null>} */
    this.scope = new AsyncLocalStorage()
    /** @type {Map<string,number>} */
    this.floors = new Map()
  }

  /** @template T @param {unknown} metadata @param {()=>T} work @returns {T} */
  run (metadata, work) {
    const origin = metadata === undefined ? null : schema.parse(metadata)
    if (origin) this.floors.set(origin.producer, Math.max(origin.floor, this.floors.get(origin.producer) ?? 0))
    return this.scope.run(origin, work)
  }

  current () { return this.scope.getStore() ?? null }

  assertCurrent () {
    const origin = this.current()
    if ((!origin && this.floors.size) || (origin && origin.epoch < (this.floors.get(origin.producer) ?? 0))) {
      throw new ToolError('CANCELLED', 'This request predates a stop/pause or has no host ownership. Wait for a new player request; do not retry the old job.')
    }
  }
}
