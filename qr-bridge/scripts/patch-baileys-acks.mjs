import {createHash} from 'node:crypto';
import {readFileSync, renameSync, rmSync, statSync, writeFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

// Temporary, reviewed backport for the exact published Baileys 6.7.24 artifact.
// Sources: WhiskeySockets/Baileys#1910, #2858 and #2883. Do not carry this
// forward automatically: review upstream ACK handling when changing Baileys.
export const BAILEYS_ACK_VERSION = '6.7.24';
export const BAILEYS_ACK_ORIGINAL_SHA256 = 'f949a18ee1dfef50a00a767bc2fd5e696dc7b2c1af386a31b4ac7fbb167b6519';
export const BAILEYS_ACK_PATCHED_SHA256 = '874f303048ad7de6a064c973c3a714a2cae5ce3444381209095fae3649bd43a8';
const receivePath = 'lib/Socket/messages-recv.js';

// Exported so the offline regression suite can reconstruct the exact original
// artifact in memory even when npm's postinstall has already applied the patch.
export const BAILEYS_ACK_EDITS = Object.freeze([
  Object.freeze({
    before: `                    cleanMessage(msg, authState.creds.me.id);
                    await upsertMessage(msg, node.attrs.offline ? 'append' : 'notify');`,
    after: `                    cleanMessage(msg, authState.creds.me.id);
                    await upsertMessage(msg, node.attrs.offline ? 'append' : 'notify');
                    // ORION_ACK_BACKPORT: acknowledge only successfully handled messages.
                    // Ciphertext stubs retain their existing retry/NACK path. Unavailable
                    // envelopes without ciphertext were acknowledged before their PDO request.
                    if (msg.messageStubType !== proto.WebMessageInfo.StubType.CIPHERTEXT &&
                        !(getBinaryNodeChild(node, 'unavailable') && !encNode)) {
                        await sendMessageAck(node);
                        logger.debug({ id: node.attrs.id, kind: 'message' }, 'orion protocol ack sent');
                    }`,
  }),
  Object.freeze({
    before: `    const makeOfflineNodeProcessor = () => {`,
    after: `    // ORION_ACK_BACKPORT: top-level status stanzas also require a transport ACK.
    // This is not a read receipt and does not decrypt, store or forward a status.
    const handleStatus = async (node) => {
        try {
            if (node.tag !== 'status' || typeof node.attrs?.id !== 'string' || !node.attrs.id ||
                typeof node.attrs?.from !== 'string' || !node.attrs.from) {
                throw new Error('Invalid status stanza for acknowledgement');
            }
            await sendMessageAck(node);
            logger.debug({ id: node.attrs.id, kind: 'status' }, 'orion protocol ack sent');
        }
        catch (error) {
            // The legacy offline queue must not be stranded by a rejected ACK.
            onUnexpectedError(error, 'acking status stanza');
        }
    };
    const makeOfflineNodeProcessor = () => {`,
  }),
  Object.freeze({
    before: `            ['receipt', handleReceipt],
            ['notification', handleNotification]`,
    after: `            ['receipt', handleReceipt],
            ['notification', handleNotification],
            ['status', handleStatus]`,
  }),
  Object.freeze({
    before: `    ws.on('CB:ack,class:message', (node) => {`,
    after: `    // ORION_ACK_BACKPORT: preserve ordering with the existing offline queue.
    ws.on('CB:status', (node) => {
        processNode('status', node, 'acking status stanza', handleStatus);
    });
    ws.on('CB:ack,class:message', (node) => {`,
  }),
]);

export const sha256 = value => createHash('sha256').update(value).digest('hex');

export function patchBaileysAckSource(source, version) {
  if (version !== BAILEYS_ACK_VERSION) {
    throw new Error(`ACK backport requires Baileys ${BAILEYS_ACK_VERSION}; found ${version}. Review the backport before upgrading.`);
  }
  const currentHash = sha256(source);
  if (currentHash === BAILEYS_ACK_PATCHED_SHA256) return {source, changed: false};
  if (currentHash !== BAILEYS_ACK_ORIGINAL_SHA256) {
    throw new Error(`Refusing ACK backport: unexpected ${receivePath} SHA-256 ${currentHash}.`);
  }
  let patched = source;
  for (const {before, after} of BAILEYS_ACK_EDITS) {
    if (patched.split(before).length !== 2) {
      throw new Error('Refusing ACK backport: an expected source anchor is missing or duplicated.');
    }
    patched = patched.replace(before, after);
  }
  if (sha256(patched) !== BAILEYS_ACK_PATCHED_SHA256) {
    throw new Error('Refusing ACK backport: the resulting artifact does not match its reviewed SHA-256.');
  }
  return {source: patched, changed: true};
}

export function applyBaileysAckPatch({packageRoot, check = false} = {}) {
  const root = packageRoot ?? path.dirname(createRequire(import.meta.url).resolve('@whiskeysockets/baileys/package.json'));
  const metadata = JSON.parse(readFileSync(path.join(root, 'package.json'), 'utf8'));
  if (metadata.name !== '@whiskeysockets/baileys') throw new Error('Refusing ACK backport: unexpected package name.');
  const target = path.join(root, receivePath);
  const result = patchBaileysAckSource(readFileSync(target, 'utf8'), metadata.version);
  if (check && result.changed) throw new Error('Required Baileys ACK backport is missing; run the patch before starting the bridge.');
  if (result.changed) {
    const temporary = `${target}.orion-acks-${process.pid}.tmp`;
    try {
      writeFileSync(temporary, result.source, {flag: 'wx', mode: statSync(target).mode});
      renameSync(temporary, target);
    } finally {
      rmSync(temporary, {force: true});
    }
  }
  if (sha256(readFileSync(target)) !== BAILEYS_ACK_PATCHED_SHA256) {
    throw new Error('Baileys ACK backport read-back verification failed.');
  }
  return {version: metadata.version, changed: result.changed, sha256: BAILEYS_ACK_PATCHED_SHA256};
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2);
    if (args.some(arg => arg !== '--check') || args.length > 1) throw new Error('Usage: node scripts/patch-baileys-acks.mjs [--check]');
    const result = applyBaileysAckPatch({check: args.includes('--check')});
    console.log(`Baileys ${result.version} ACK backport ${result.changed ? 'applied' : 'verified'} (${result.sha256}).`);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
