// Built-in Node checks. No downloaded packages; this is not a browser/UI test.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '../static/lab10.js'), 'utf8');
const exposed = source.replace(/\}\)\(\);\s*$/, 'globalThis.lab10Test = {validItem, readPlan, COURSES, STORAGE_KEY};\n})();');
assert.notEqual(source, exposed, 'Expected script wrapper for test-only exports');
const values = new Map();
let denied = false;
const sandbox = {
  document: {addEventListener() {}},
  window: { localStorage: {
    getItem(key) {
      if (denied) { const error = new Error('blocked'); error.name = 'SecurityError'; throw error; }
      return values.has(key) ? values.get(key) : null;
    },
  }},
};
vm.createContext(sandbox);
vm.runInContext(exposed, sandbox);
const {validItem, readPlan, COURSES, STORAGE_KEY} = sandbox.lab10Test;
let checks = 0;
function check(description, run) { run(); checks++; console.log('PASS', description); }
const good = {courseId: 'html', hours: 2, priority: 'high'};
check('Six unique catalog IDs', () => assert.equal(new Set(COURSES.map(item => item.id)).size, 6));
check('Allowlisted item accepted', () => assert.equal(validItem(good), true));
check('Hours boundaries 1 and 40 accepted', () => [1,40].forEach(hours => assert.equal(validItem({...good,hours}), true)));
check('Invalid hours rejected', () => [-1,0,41,1.5,'2',NaN,Infinity,null].forEach(hours => assert.equal(validItem({...good,hours}), false)));
check('Priority must be a string enum', () => [['high'], {}, null, 'urgent', '', '__proto__', 1].forEach(priority => assert.equal(validItem({...good,priority}), false)));
check('Unknown course and HTML injection rejected', () => ['', 'unknown', '<img src=x onerror=alert(1)>'].forEach(courseId => assert.equal(validItem({...good,courseId}), false)));
check('Additional properties rejected', () => assert.equal(validItem({...good, password:'not-real'}), false));
check('Missing key returns empty plan', () => assert.equal(readPlan().exists, false));
check('JSON roundtrip preserves bounded data', () => {
  values.set(STORAGE_KEY, JSON.stringify({version:1,items:[good]}));
  assert.equal(JSON.stringify(readPlan().items), JSON.stringify([good]));
});
check('Invalid JSON and overlong values return visible error', () => {
  for (const raw of ['{broken', 'x'.repeat(4097), 'null', '[]']) { values.set(STORAGE_KEY,raw); assert.ok(readPlan().error); }
});
check('Duplicate, unversioned and unrecognized properties rejected', () => {
  for (const data of [{version:1,items:[good,good]}, {items:[good]}, {version:2,items:[good]}, {version:1,items:[good],user:'not-real'}]) {
    values.set(STORAGE_KEY, JSON.stringify(data)); assert.ok(readPlan().error);
  }
});
check('Storage denial distinguished and no deletion performed', () => {
  denied = true; const state = readPlan(); assert.ok(state.error.includes('тыйым')); assert.equal(state.exists, false);
});
console.log(`${checks} logic checks passed. No browser, FastAPI, session or SQLite writes tested.`);
