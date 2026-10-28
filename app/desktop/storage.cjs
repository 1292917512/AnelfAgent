// @ts-check
/** Check the selected filesystem without creating directories or downloading anything. */
const fs = require('node:fs');
const path = require('node:path');

/** @param {string} root @param {number} required @returns {import('../ui/lib/types').Storage} */
function inspectStorage(root, required) {
  try {
    let existing = path.resolve(root);
    while (!fs.existsSync(existing) && existing !== path.dirname(existing)) existing = path.dirname(existing);
    const stats = fs.statfsSync(existing);
    const free = stats.bavail * stats.bsize;
    return {path:root,free,required,sufficient:free >= required,error:''};
  } catch (error) {
    return {path:root,free:null,required,sufficient:false,error:String(error)};
  }
}

/** @param {import('../ui/lib/types').Storage[]} checks */
function requireStorage(checks) {
  for (const check of checks) if (!check.sufficient) {
    const available = check.free === null ? 'unknown' : (check.free / 1024 ** 3).toFixed(2) + ' GiB';
    throw new Error(`Insufficient disk space: ${check.path}; available ${available}, required ${check.required / 1024 ** 3} GiB. ${check.error}`);
  }
}
module.exports = {inspectStorage,requireStorage};
