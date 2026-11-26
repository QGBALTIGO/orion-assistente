import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createDeviceGroupContacts, groupContactGroups} from '../src/device-group-contacts.mjs';
import {createGroupAddSocket} from '../src/device-group-add.mjs';
import {createGroupCatalogReader} from '../src/group-catalog-recovery.mjs';

const firstId = '120363000000000001';
const secondId = '120363000000000002';
const participant = '123456789@lid';
const firstGroup = {
  tag: 'group',
  attrs: {id: firstId, subject: 'Grupo sintético um', size: '1'},
  content: [{tag: 'participant', attrs: {jid: participant, type: 'admin'}}],
};
const secondGroup = {tag: 'group', attrs: {id: secondId, subject: 'Grupo sintético dois', size: '0'}, content: []};
const malformedGroup = {tag: 'group', attrs: {subject: 'metadado incompleto'}, content: []};

function envelope(groups) {
  return {
    tag: 'iq',
    attrs: {type: 'result'},
    content: [{tag: 'groups', attrs: {}, ...(groups === undefined ? {} : {content: groups})}],
  };
}

async function installedParser() {
  // This Baileys version requires the package entry to initialize before loading the parser.
  await import('@whiskeysockets/baileys');
  const {extractGroupMetadata} = await import('@whiskeysockets/baileys/lib/Socket/groups.js');
  return extractGroupMetadata;
}

function fixture({stock, raw, extractMetadata, queryAvailable = true}) {
  const calls = [];
  const events = [];
  const forbidden = name => async () => {
    calls.push(name);
    assert.fail('unexpected operation: ' + name);
  };
  const client = {
    async groupFetchAllParticipating() {
      calls.push('list');
      return stock();
    },
    ...(queryAvailable ? {async query(request) {
      assert.equal(request.tag, 'iq');
      assert.deepEqual(request.attrs, {to: '@g.us', xmlns: 'w:g2', type: 'get'});
      assert.equal(request.content?.length, 1);
      assert.equal(request.content[0].tag, 'participating');
      assert.deepEqual(request.content[0].content?.map(node => node.tag), ['participants', 'description']);
      calls.push('read-iq');
      return raw();
    }} : {}),
    groupMetadata: forbidden('metadata'),
    groupParticipantsUpdate: forbidden('participants'),
    groupInviteCode: forbidden('invite'),
    sendMessage: forbidden('message'),
  };
  const read = createGroupCatalogReader({client, extractMetadata, onFailure: event => events.push(event)});
  return {read, client, calls, events};
}

function parserError() {
  throw new TypeError('synthetic private parser details ' + participant);
}

test('stock group catalog success performs one read and projects its result unchanged', async () => {
  const stockResult = {[firstId + '@g.us']: {id: firstId + '@g.us', subject: 'Fixture', size: 0}};
  const d = fixture({stock: () => stockResult, raw: () => assert.fail('unexpected recovery')});
  assert.deepEqual(await d.read(), stockResult);
  assert.deepEqual(d.calls, ['list']);
  assert.deepEqual(d.events, []);
});

test('a malformed group that aborts the pinned Baileys parser is counted individually after a read-only retry', async () => {
  const extractMetadata = await installedParser();
  const d = fixture({
    stock: () => extractMetadata({tag: 'result', attrs: {}, content: [malformedGroup]}),
    raw: () => envelope([firstGroup, malformedGroup, secondGroup]),
    extractMetadata,
  });
  const recovered = await d.read();
  const view = groupContactGroups(recovered);
  assert.deepEqual(view.groups, [
    {id: firstId + '@g.us', name: 'Grupo sintético um', member_count: 1},
    {id: secondId + '@g.us', name: 'Grupo sintético dois', member_count: 0},
  ]);
  assert.equal(view.unavailable_groups, 1);
  assert.equal(recovered[firstId + '@g.us'].participants[0].id, participant);
  assert.deepEqual(d.calls, ['list', 'read-iq']);
  assert.ok(d.events.some(event => event.stage === 'sdk' && event.kind === 'parser'));
  assert.ok(d.events.some(event => event.stage === 'recovered' && event.omitted === 1));
  assert.ok(!JSON.stringify(d.events).includes(participant));
});

test('duplicate IDs from the fallback are not exposed as selectable groups', async () => {
  const extractMetadata = await installedParser();
  const d = fixture({
    stock: parserError,
    raw: () => envelope([firstGroup, secondGroup, {...firstGroup, attrs: {...firstGroup.attrs, subject: 'Duplicado'}}]),
    extractMetadata,
  });
  const view = groupContactGroups(await d.read());
  assert.deepEqual([...view.ids], [secondId + '@g.us']);
  assert.equal(view.unavailable_groups, 2);
  assert.deepEqual(d.calls, ['list', 'read-iq']);
});

test('both guarded group-list adapters expose recovered groups and keep participant mutations unreachable', async () => {
  const extractMetadata = await installedParser();
  const d = fixture({stock: parserError, raw: () => envelope([firstGroup, malformedGroup, secondGroup]), extractMetadata});
  d.client.user = {id: participant};
  const input = {
    device_id: '27b464f4-9ff9-4472-87ec-c055e9352412',
    generation: '690c2416-2c6c-4a90-a85f-63a760873446',
    settings_revision: 1,
  };
  const options = {
    client: d.client,
    isConnected: () => true,
    isCurrent: () => true,
    deviceSnapshot: () => ({id: input.device_id, generation: input.generation, settings_revision: input.settings_revision}),
    readParticipatingGroups: d.read,
  };
  const extractor = createDeviceGroupContacts(options);
  const groupAdd = createGroupAddSocket(options);
  let grants = 0;
  const scope = {authorize: async () => { grants++; }};
  const exported = await extractor('groups', input, scope);
  const targets = await groupAdd('groups', input, scope);
  assert.deepEqual(exported.groups.map(({id}) => id), [firstId + '@g.us', secondId + '@g.us']);
  assert.deepEqual(targets.groups.map(({id, can_add}) => [id, can_add]), [
    [firstId + '@g.us', true],
    [secondId + '@g.us', false],
  ]);
  assert.equal(exported.unavailable_groups, 1);
  assert.equal(targets.unavailable_groups, 1);
  assert.equal(grants, 6);
  assert.deepEqual(d.calls, ['list', 'read-iq', 'list', 'read-iq']);
});

test('a valid empty groups envelope is accepted', async () => {
  const extractMetadata = () => assert.fail('empty result must not call the parser');
  for (const groups of [undefined, []]) {
    const d = fixture({stock: parserError, raw: () => envelope(groups), extractMetadata});
    assert.deepEqual(groupContactGroups(await d.read()).groups, []);
    assert.deepEqual(d.calls, ['list', 'read-iq']);
  }
});

test('missing group envelope, all-invalid data and provider failures fail closed', async () => {
  const extractMetadata = await installedParser();
  const invalidResponses = [
    () => ({tag: 'iq', attrs: {type: 'result'}, content: []}),
    () => envelope([malformedGroup]),
    () => {throw new Error('synthetic private transport failure ' + participant);},
  ];
  for (const raw of invalidResponses) {
    const d = fixture({stock: parserError, raw, extractMetadata});
    await assert.rejects(d.read());
    assert.deepEqual(d.calls, ['list', 'read-iq']);
    assert.ok(d.events.some(event => event.stage === 'fallback'));
    assert.ok(d.events.every(event => Object.keys(event).every(key =>
      ['stage', 'kind', 'status', 'omitted'].includes(key))));
    assert.ok(!JSON.stringify(d.events).includes(participant));
  }
});

test('refuses recovery after an upstream refusal, missing query or stale authorization', async () => {
  const extractMetadata = () => assert.fail('unexpected parser call');
  const provider = fixture({
    stock: () => {
      throw Object.assign(new Error('synthetic private provider refusal ' + participant), {
        isBoom: true, data: 403, output: {statusCode: 500},
      });
    },
    extractMetadata,
  });
  await assert.rejects(provider.read());
  assert.deepEqual(provider.calls, ['list']);
  assert.deepEqual(provider.events, [{stage: 'sdk', kind: 'provider', status: 403}]);
  assert.ok(!JSON.stringify(provider.events).includes(participant));

  const unsupported = fixture({stock: parserError, extractMetadata, queryAvailable: false});
  await assert.rejects(unsupported.read());
  assert.deepEqual(unsupported.calls, ['list']);

  const stale = fixture({stock: parserError, extractMetadata});
  let rechecks = 0;
  await assert.rejects(stale.read({beforeRetry() {
    rechecks++;
    throw new Error('stale grant');
  }}));
  assert.equal(rechecks, 1);
  assert.deepEqual(stale.calls, ['list']);
});
