import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import {
  applyBaileysAckPatch, BAILEYS_ACK_EDITS, BAILEYS_ACK_ORIGINAL_SHA256,
  BAILEYS_ACK_PATCHED_SHA256, BAILEYS_ACK_VERSION, patchBaileysAckSource, sha256,
} from '../scripts/patch-baileys-acks.mjs';

const packageRoot = path.dirname(createRequire(import.meta.url).resolve('@whiskeysockets/baileys/package.json'));
const installed = fs.readFileSync(path.join(packageRoot, 'lib/Socket/messages-recv.js'), 'utf8');
let original = installed;
if (sha256(installed) === BAILEYS_ACK_PATCHED_SHA256) {
  for (const {before, after} of [...BAILEYS_ACK_EDITS].reverse()) original = original.replace(after, before);
}
assert.equal(sha256(original), BAILEYS_ACK_ORIGINAL_SHA256, 'Tests require the exact pinned npm artifact');
const patched = patchBaileysAckSource(original, BAILEYS_ACK_VERSION).source;

const peer = '5567999999999@s.whatsapp.net';
const self = '5567888888888:1@s.whatsapp.net';
const ciphertext = 2;
const missingKeys = 'synthetic missing keys';
const clone = value => JSON.parse(JSON.stringify(value));
const flush = async () => { await new Promise(resolve => setImmediate(resolve)); };
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};
const node = (id, {tag = 'message', offline, content, ...attrs} = {}) => ({
  tag, attrs: {id, from: peer, type: 'text', ...(offline === undefined ? {} : {offline}), ...attrs},
  content: content ?? (tag === 'message' ? [{tag: 'enc', attrs: {type: 'msg'}}] : []),
});

// Execute the actual pinned/patched compiled ACK, receive and offline handlers.
// Only their transport, crypto and application dependencies are synthetic. This
// exercises the library's branches and event registration without a real socket,
// real auth state, phone, message, or changes to the installed dependency.
function makeHarness(source = patched, options = {}) {
  const section = (start, end) => {
    const from = source.indexOf(start), to = source.indexOf(end, from + start.length);
    assert.ok(from >= 0 && to > from, `Expected compiled handler bounds: ${start}`);
    return source.slice(from, to);
  };
  const executable = [
    section('    const sendMessageAck =', '    const rejectCall ='),
    section('    const handleMessage =', '    const fetchMessageHistory ='),
    section('    const processNodeWithBuffer =', '    // Handles newsletter notifications'),
    section('    // recv a message', "    ev.on('call'"),
    'globalThis.handlers = {handleMessage, processNode};',
  ].join('\n');
  const wire = [], attempts = [], receipts = [], upserts = [], retries = [], logs = [], errors = [], timeline = [];
  const ws = new EventEmitter();
  ws.isOpen = true;
  const ev = new EventEmitter();
  ev.buffer = () => timeline.push('buffer');
  ev.flush = () => timeline.push('flush');
  const getBinaryNodeChild = (current, tag) => Array.isArray(current?.content) ? current.content.find(child => child.tag === tag) : undefined;
  const placeholderResendCache = new Map();
  placeholderResendCache.del = key => placeholderResendCache.delete(key);
  let mutex = Promise.resolve();
  const context = {
    ws, ev, placeholderResendCache, getBinaryNodeChild,
    authState: {creds: {me: {id: self}}}, signalRepository: {},
    proto: {WebMessageInfo: {StubType: {CIPHERTEXT: ciphertext}}, Message: {ProtocolMessage: {Type: {SHARE_PHONE_NUMBER: 11}}}},
    MISSING_KEYS_ERROR_TEXT: missingKeys, NO_MESSAGE_FOUND_ERROR_TEXT: 'no message found',
    NACK_REASONS: {ParsingError: 487}, retryRequestDelayMs: 0, sendActiveReceipts: false,
    logger: Object.fromEntries(['debug', 'info', 'warn', 'error'].map(level => [level, (...args) => logs.push({level, args})])),
    onUnexpectedError: (error, label) => errors.push({message: error.message, label}),
    shouldIgnoreJid: () => options.ignore === true,
    processingMutex: {mutex: work => { const next = mutex.then(work); mutex = next.catch(() => {}); return next; }},
    retryMutex: {mutex: work => Promise.resolve().then(work)},
    delay: async () => {},
    getHistoryMsg: () => options.history ? {} : undefined,
    jidNormalizedUser: jid => jid.replace(/:\d+@/, '@'),
    isJidUser: jid => jid.endsWith('@s.whatsapp.net'),
    decodeMessageNode: current => ({fullMessage: {key: {id: current.attrs.id, remoteJid: current.attrs.from}}}),
    requestPlaceholderResend: async () => options.placeholderResponse,
    decryptMessageNode: current => {
      const message = {
        key: {id: current.attrs.id, remoteJid: current.attrs.from, fromMe: options.fromMe === true, participant: current.attrs.participant},
        message: {conversation: 'Synthetic content, never sent'},
      };
      return {
        fullMessage: message, author: 'linked-device@s.whatsapp.net', category: options.category,
        decrypt: async () => {
          timeline.push(`decrypt:${current.attrs.id}`);
          if (options.decryptGate) await options.decryptGate;
          if (options.decryptThrows) throw new Error('synthetic decrypt failure');
          if (options.ciphertext || options.missingKeys) {
            message.messageStubType = ciphertext;
            message.messageStubParameters = [options.missingKeys ? missingKeys : 'bad mac'];
            delete message.message;
          }
        },
      };
    },
    cleanMessage: message => {
      timeline.push(`clean:${message.key.id}`);
      if (options.cleanThrows) throw new Error('synthetic clean failure');
    },
    upsertMessage: async (message, mode) => {
      timeline.push(`upsert:${message.key.id}`);
      if (options.upsertGate) await options.upsertGate;
      if (options.upsertThrows) throw new Error('synthetic upsert failure');
      upserts.push({message, mode});
    },
    sendNode: async stanza => {
      attempts.push(clone(stanza));
      if (options.ackGateId === stanza.attrs.id) await options.ackGate;
      if (options.ackFailureId === stanza.attrs.id) throw new Error('synthetic ack send failure');
      wire.push(clone(stanza));
      timeline.push(`ack:${stanza.attrs.id}`);
    },
    sendReceipt: async (...args) => { receipts.push(args); timeline.push(`receipt:${args[2][0]}`); },
    sendRetryRequest: async (current, forceKeys) => retries.push({id: current.attrs.id, forceKeys}),
    handleCall: async () => {}, handleReceipt: async () => {}, handleNotification: async () => {}, handleBadAck: async () => {},
  };
  vm.runInNewContext(executable, context, {filename: 'pinned-baileys-ack-handlers.js'});
  return {wire, attempts, receipts, upserts, retries, logs, errors, timeline, ws, handle: context.handlers.handleMessage};
}

test('unpached 6.7.24 reproduces both missing message ACK and missing status handler', async () => {
  const h = makeHarness(original);
  await h.handle(node('M0'));
  h.ws.emit('CB:status', node('S0', {tag: 'status'}));
  await flush();
  assert.equal(h.upserts.length, 1);
  assert.equal(h.wire.length, 0);
  assert.equal(h.ws.listenerCount('CB:status'), 0);
});

test('backport is exact, version-locked and idempotent', () => {
  assert.equal(sha256(patched), BAILEYS_ACK_PATCHED_SHA256);
  assert.equal(patchBaileysAckSource(patched, '6.7.24').changed, false);
  assert.throws(() => patchBaileysAckSource(original, '7.0.0-rc14'), /requires Baileys/);
  assert.throws(() => patchBaileysAckSource(patched, '6.7.25'), /requires Baileys/);
  assert.throws(() => patchBaileysAckSource(original + '\n', '6.7.24'), /unexpected.*SHA-256/);
  assert.throws(() => patchBaileysAckSource(patched.replace('orion protocol ack sent', 'tampered'), '6.7.24'), /unexpected.*SHA-256/);
});

test('installation verifies before writing and --check never modifies the artifact', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'orion-baileys-acks-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const target = path.join(root, 'lib/Socket/messages-recv.js');
  fs.mkdirSync(path.dirname(target), {recursive: true});
  fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify({name: '@whiskeysockets/baileys', version: '6.7.24'}));
  fs.writeFileSync(target, original);
  assert.throws(() => applyBaileysAckPatch({packageRoot: root, check: true}), /backport is missing/);
  assert.equal(fs.readFileSync(target, 'utf8'), original);
  assert.equal(applyBaileysAckPatch({packageRoot: root}).changed, true);
  assert.equal(fs.readFileSync(target, 'utf8'), patched);
  assert.equal(applyBaileysAckPatch({packageRoot: root}).changed, false);
  assert.equal(applyBaileysAckPatch({packageRoot: root, check: true}).changed, false);
  fs.writeFileSync(target, patched + '\n');
  assert.throws(() => applyBaileysAckPatch({packageRoot: root}), /unexpected.*SHA-256/);
  assert.equal(fs.readFileSync(target, 'utf8'), patched + '\n');
});

test('decrypted message receives one transport ACK after processing, preserving receipt behavior', async () => {
  const h = makeHarness();
  await h.handle(node('M1'));
  assert.deepEqual(h.wire, [{tag: 'ack', attrs: {id: 'M1', to: peer, class: 'message', type: 'text'}}]);
  assert.equal(h.receipts.length, 1);
  assert.equal(h.receipts[0][3], 'inactive');
  assert.ok(h.timeline.indexOf('ack:M1') > h.timeline.indexOf('upsert:M1'));
  assert.ok(h.timeline.indexOf('upsert:M1') > h.timeline.indexOf('clean:M1'));
  const marker = h.logs.find(entry => entry.args[1] === 'orion protocol ack sent');
  assert.deepEqual(clone(marker.args[0]), {id: 'M1', kind: 'message'});
});

test('message is not ACKed while decryption or application upsert remains pending', async () => {
  const decrypt = deferred(), upsert = deferred();
  const h = makeHarness(patched, {decryptGate: decrypt.promise, upsertGate: upsert.promise});
  const pending = h.handle(node('WAIT'));
  await flush();
  assert.equal(h.wire.length, 0);
  decrypt.resolve();
  await flush();
  assert.ok(h.timeline.includes('upsert:WAIT'));
  assert.equal(h.wire.length, 0);
  upsert.resolve();
  await pending;
  assert.equal(h.wire.length, 1);
});

for (const failure of ['decryptThrows', 'cleanThrows', 'upsertThrows']) {
  test(`failed ${failure} never receives a successful ACK`, async () => {
    const h = makeHarness(patched, {[failure]: true});
    await h.handle(node('FAILED'));
    assert.equal(h.wire.length, 0);
    assert.equal(h.logs.filter(entry => entry.args[1] === 'orion protocol ack sent').length, 0);
    assert.equal(h.logs.filter(entry => entry.level === 'error').length, 1);
  });
}

test('ciphertext failure keeps retry behavior and receives no positive ACK', async () => {
  const h = makeHarness(patched, {ciphertext: true});
  await h.handle(node('CIPHER'));
  await flush();
  assert.deepEqual(h.retries, [{id: 'CIPHER', forceKeys: false}]);
  assert.equal(h.wire.length, 0);
  assert.equal(h.upserts[0].message.messageStubType, ciphertext);
});

test('missing-key failure keeps the existing NACK and no successful marker', async () => {
  const h = makeHarness(patched, {missingKeys: true});
  await h.handle(node('KEYS'));
  assert.equal(h.wire.length, 1);
  assert.equal(h.wire[0].attrs.error, '487');
  assert.equal(h.retries.length, 0);
  assert.equal(h.upserts.length, 0);
  assert.equal(h.logs.filter(entry => entry.args[1] === 'orion protocol ack sent').length, 0);
});

for (const settings of [{ignore: true}, {msmsg: true}, {unavailable: true}, {unavailable: true, placeholderResponse: 'RESOLVED'}]) {
  test(`preexisting ACK paths are not duplicated: ${JSON.stringify(settings)}`, async () => {
    const h = makeHarness(patched, settings);
    const content = settings.msmsg ? [{tag: 'enc', attrs: {type: 'msmsg'}}]
      : settings.unavailable ? [{tag: 'unavailable', attrs: {}}] : undefined;
    await h.handle(node('ALREADY', {content}));
    assert.equal(h.wire.length, 1);
    assert.equal(h.retries.length, 0);
  });
}

for (const settings of [{category: 'peer', expected: 'peer_msg'}, {fromMe: true, expected: 'sender'}, {history: true, expected: 'inactive'}]) {
  test(`message ACK preserves ${JSON.stringify(settings)} receipt semantics`, async () => {
    const h = makeHarness(patched, settings);
    await h.handle(node('RECEIPTS'));
    assert.equal(h.wire.length, 1);
    assert.equal(h.receipts[0][3], settings.expected);
    if (settings.history) assert.equal(h.receipts[1][3], 'hist_sync');
    assert.equal(h.receipts.some(receipt => receipt[3] === 'read'), false);
  });
}

test('live status ACK preserves routing attributes and does not decrypt, read or upsert content', async () => {
  const h = makeHarness();
  h.ws.emit('CB:status', node('STATUS', {tag: 'status', type: 'media', participant: peer, recipient: self}));
  await flush();
  assert.deepEqual(h.wire, [{tag: 'ack', attrs: {id: 'STATUS', to: peer, class: 'status', participant: peer, recipient: self, type: 'media'}}]);
  assert.equal(h.upserts.length, 0);
  assert.equal(h.receipts.length, 0);
  assert.equal(h.retries.length, 0);
  assert.equal(h.timeline.some(value => value.startsWith('decrypt:')), false);
  const marker = h.logs.find(entry => entry.args[1] === 'orion protocol ack sent');
  assert.deepEqual(clone(marker.args[0]), {id: 'STATUS', kind: 'status'});
  assert.equal(h.ws.listenerCount('CB:status'), 1);
});

test('offline status ACK follows the same ordered queue as messages', async () => {
  const gate = deferred();
  const h = makeHarness(patched, {ackGateId: 'FIRST', ackGate: gate.promise});
  h.ws.emit('CB:status', node('FIRST', {tag: 'status', offline: 'true'}));
  h.ws.emit('CB:message', node('SECOND', {offline: 'true'}));
  h.ws.emit('CB:status', node('THIRD', {tag: 'status', offline: 'true'}));
  await flush();
  assert.equal(h.upserts.length, 0);
  assert.equal(h.wire.length, 0);
  gate.resolve();
  await flush();
  assert.deepEqual(h.wire.map(value => value.attrs.id), ['FIRST', 'SECOND', 'THIRD']);
  assert.equal(h.upserts[0].mode, 'append');
});

test('failed offline status ACK is reported and cannot strand subsequent messages', async () => {
  const h = makeHarness(patched, {ackFailureId: 'BAD'});
  h.ws.emit('CB:status', node('BAD', {tag: 'status', offline: 'true'}));
  h.ws.emit('CB:message', node('AFTER', {offline: 'true'}));
  h.ws.emit('CB:status', node('LAST', {tag: 'status', offline: 'true'}));
  await flush();
  assert.deepEqual(h.wire.map(value => value.attrs.id), ['AFTER', 'LAST']);
  assert.deepEqual(h.errors, [{message: 'synthetic ack send failure', label: 'acking status stanza'}]);
  assert.equal(h.logs.filter(entry => entry.args[1] === 'orion protocol ack sent' && entry.args[0].id === 'BAD').length, 0);
});

test('failed message ACK is reported and does not claim a successful send', async () => {
  const h = makeHarness(patched, {ackFailureId: 'BADMSG'});
  await h.handle(node('BADMSG'));
  assert.equal(h.upserts.length, 1);
  assert.equal(h.attempts.length, 1);
  assert.equal(h.wire.length, 0);
  assert.equal(h.logs.filter(entry => entry.level === 'error').length, 1);
  assert.equal(h.logs.filter(entry => entry.args[1] === 'orion protocol ack sent').length, 0);
});

test('malformed statuses and unknown stanza classes are not acknowledged', async () => {
  const h = makeHarness();
  h.ws.emit('CB:status', {tag: 'status', attrs: {from: peer}});
  h.ws.emit('CB:status', {tag: 'status', attrs: {id: 'NO-SENDER'}});
  h.ws.emit('CB:status', {tag: 'message', attrs: {id: 'WRONG', from: peer}});
  h.ws.emit('CB:unknown', {tag: 'unknown', attrs: {id: 'UNKNOWN', from: peer}});
  await flush();
  assert.equal(h.wire.length, 0);
  assert.equal(h.errors.length, 3);
});
