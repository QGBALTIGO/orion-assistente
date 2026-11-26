import assert from 'node:assert/strict';
import {Readable} from 'node:stream';
import {test} from 'node:test';
import {createChannelRoutes} from '../src/channel-routes.mjs';
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

function fixture({stock, raw, extractMetadata, queryAvailable = true, now, membershipScope}) {
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
  const read = createGroupCatalogReader({
    client, extractMetadata, now, membershipScope, onFailure: event => events.push(event),
  });
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

test('concurrent reads of the same socket scope share one provider request', async () => {
  const stockResult = {[firstId + '@g.us']: {id: firstId + '@g.us', subject: 'Fixture', size: 0}};
  let resolveStock;
  const pending = new Promise(resolve => { resolveStock = resolve; });
  const d = fixture({stock: () => pending, raw: () => assert.fail('unexpected fallback')});
  const first = d.read();
  const second = d.read();
  resolveStock(stockResult);
  assert.deepEqual(await Promise.all([first, second]), [stockResult, stockResult]);
  assert.deepEqual(d.calls, ['list']);
  assert.deepEqual(await d.read(), stockResult);
  assert.deepEqual(d.calls, ['list', 'list']);
});

test('429 blocks new catalog reads with a doubled cooldown capped at one hour and resets after success', async () => {
  const stockResult = {[firstId + '@g.us']: {id: firstId + '@g.us', subject: 'Fixture', size: 0}};
  let clock = 1_000_000;
  let limited = true;
  const d = fixture({
    now: () => clock,
    stock: () => {
      if (!limited) return stockResult;
      throw Object.assign(new Error('synthetic private rate limit ' + participant), {
        isBoom: true, data: 429, output: {statusCode: 500},
      });
    },
    raw: () => assert.fail('rate limit must not trigger a fallback'),
  });
  const cooldown = seconds => error => {
    assert.equal(error.groupCatalogReason, 'provider_rate_limited');
    assert.equal(error.retryAfterSeconds, seconds);
    return true;
  };
  let stockAttempts = 0;
  for (const seconds of [300, 600, 1200, 2400, 3600, 3600]) {
    await assert.rejects(d.read(), cooldown(seconds));
    stockAttempts++;
    assert.equal(d.calls.length, stockAttempts);
    clock += seconds * 1000 - 1;
    await assert.rejects(d.read(), cooldown(1));
    assert.equal(d.calls.length, stockAttempts);
    clock++;
  }
  limited = false;
  assert.deepEqual(await d.read(), stockResult);
  limited = true;
  await assert.rejects(d.read(), cooldown(300));
  assert.deepEqual(d.calls, Array(stockAttempts + 2).fill('list'));
  assert.ok(!JSON.stringify(d.events).includes(participant));
});

test('a rate limit from parser recovery only pauses that socket reader', async () => {
  const now = () => 1_000_000;
  const blocked = fixture({
    now,
    stock: parserError,
    raw: () => {
      throw Object.assign(new Error('synthetic rate limit'), {isBoom: true, data: 429});
    },
  });
  const unaffected = fixture({now, stock: () => ({})});
  const limited = {groupCatalogReason: 'provider_rate_limited', retryAfterSeconds: 300};
  await assert.rejects(blocked.read(), limited);
  await assert.rejects(blocked.read(), limited);
  assert.deepEqual(blocked.calls, ['list', 'read-iq']);
  assert.deepEqual(await unaffected.read(), {});
  assert.deepEqual(unaffected.calls, ['list']);
});

test('a participating group ID is reused within two minutes and looked up again at expiry', async () => {
  const groupId = firstId + '@g.us';
  let clock = 1_000_000;
  let roster = {[groupId]: {id: groupId, subject: 'Fixture', size: 0}};
  const d = fixture({
    stock: () => roster,
    now: () => clock,
    membershipScope: () => 'device:generation:1',
  });
  await d.read();
  roster = {};
  clock += 119_999;
  assert.equal(await d.read.confirmMembership(groupId), true);
  assert.deepEqual(d.calls, ['list']);
  clock++;
  assert.equal(await d.read.confirmMembership(groupId), false);
  assert.deepEqual(d.calls, ['list', 'list']);
});

test('a scope change refreshes participation and a scope without identity never reuses membership', async () => {
  const groupId = firstId + '@g.us';
  let scope = 'device:generation:1';
  let roster = {[groupId]: {id: groupId, subject: 'Fixture', size: 0}};
  const d = fixture({stock: () => roster, membershipScope: () => scope});
  assert.equal(await d.read.confirmMembership(groupId), true);
  scope = 'device:generation:2';
  roster = {};
  assert.equal(await d.read.confirmMembership(groupId), false);
  scope = null;
  roster = {[groupId]: {id: groupId, subject: 'Fixture', size: 0}};
  assert.equal(await d.read.confirmMembership(groupId), true);
  roster = {};
  assert.equal(await d.read.confirmMembership(groupId), false);
  assert.deepEqual(d.calls, ['list', 'list', 'list', 'list']);
});

test('a stale in-flight roster cannot prove participation for either scope after a scope change', async () => {
  const groupId = firstId + '@g.us';
  let scope = 'device:generation:1';
  let finishPreviousRead;
  const previousRead = new Promise(resolve => { finishPreviousRead = resolve; });
  let firstRequest = true;
  const d = fixture({
    membershipScope: () => scope,
    stock: () => {
      if (!firstRequest) return {};
      firstRequest = false;
      return previousRead;
    },
  });
  const originalRequest = assert.rejects(d.read.confirmMembership(groupId), /group_catalog_scope_changed/);
  assert.deepEqual(d.calls, ['list']);
  scope = 'device:generation:2';
  await assert.rejects(d.read.confirmMembership(groupId), /group_catalog_scope_changed/);
  assert.deepEqual(d.calls, ['list']);
  finishPreviousRead({[groupId]: {id: groupId, subject: 'Fixture', size: 0}});
  await originalRequest;
  assert.equal(await d.read.confirmMembership(groupId), false);
  assert.deepEqual(d.calls, ['list', 'list']);
});

test('cached participation is unavailable during provider cooldown', async () => {
  const groupId = firstId + '@g.us';
  let clock = 1_000_000;
  let limited = false;
  let roster = {[groupId]: {id: groupId, subject: 'Fixture', size: 0}};
  const d = fixture({
    now: () => clock,
    membershipScope: () => 'device:generation:1',
    stock: () => {
      if (limited) throw Object.assign(new Error('synthetic limit'), {isBoom: true, data: 429});
      return roster;
    },
  });
  assert.equal(await d.read.confirmMembership(groupId), true);
  limited = true;
  const cooldown = {groupCatalogReason: 'provider_rate_limited', retryAfterSeconds: 300};
  await assert.rejects(d.read(), cooldown);
  await assert.rejects(d.read.confirmMembership(groupId), cooldown);
  assert.deepEqual(d.calls, ['list', 'list']);
  clock += 300_000;
  limited = false;
  roster = {};
  assert.equal(await d.read.confirmMembership(groupId), false);
  assert.deepEqual(d.calls, ['list', 'list', 'list']);
});

test('cached participation cannot authorize an add after the fresh destination loses self admin', async () => {
  const groupId = firstId + '@g.us';
  const catalogAdmin = {
    id: groupId, subject: 'Fixture', size: 1, participants: [{id: participant, admin: 'admin'}],
  };
  const d = fixture({stock: () => ({[groupId]: catalogAdmin}), membershipScope: () => 'device:generation:1'});
  await d.read();
  d.client.user = {id: participant};
  d.client.groupMetadata = async id => {
    assert.equal(id, groupId);
    d.calls.push('fresh-metadata');
    return {...catalogAdmin, participants: [{id: participant, admin: null}]};
  };
  const input = {
    device_id: '27b464f4-9ff9-4472-87ec-c055e9352412',
    generation: '690c2416-2c6c-4a90-a85f-63a760873446',
    settings_revision: 1,
    group_id: groupId,
    operation_id: '5fc95916-02b2-4e40-8ac4-fb0b63af1d72',
    claim_token: '13c09517-431a-4ac1-8994-553a2d4ff55f',
    phone: '+15559999900',
  };
  const preflight = [];
  const groupAdd = createGroupAddSocket({
    client: d.client,
    isConnected: () => true,
    isCurrent: () => true,
    deviceSnapshot: () => ({id: input.device_id, generation: input.generation, settings_revision: input.settings_revision}),
    assertMutationReady: async () => {},
    mutationCurrent: () => true,
    verifyParticipatingGroup: d.read.confirmMembership,
    onPreflightFailure: event => preflight.push(event),
  });
  await assert.rejects(groupAdd('add', input, {
    authorize: async () => {},
    beforeAttempt: async () => assert.fail('no attempt may be reserved'),
    onAttempt: () => assert.fail('no attempt may be marked'),
  }), {groupAddReason: 'not_admin'});
  assert.deepEqual(d.calls, ['list', 'fresh-metadata']);
  assert.deepEqual(preflight, [{stage: 'destination_metadata', reason: 'not_admin'}]);
});

test('private catalog reads expose only bounded cooldown fields and hide them from mutation and receipt routes', async () => {
  const scope = {
    device_id: '27b464f4-9ff9-4472-87ec-c055e9352412',
    generation: '690c2416-2c6c-4a90-a85f-63a760873446',
    settings_revision: 1,
  };
  const memberScope = {...scope, group_id: firstId + '@g.us'};
  const operation = {
    ...memberScope,
    operation_id: '5fc95916-02b2-4e40-8ac4-fb0b63af1d72',
    claim_token: '13c09517-431a-4ac1-8994-553a2d4ff55f',
    phone: '+15559999900',
  };
  let seconds = 300;
  const managerCalls = [];
  const refuse = tool => async (_id, _key, action) => {
    managerCalls.push([tool, action]);
    throw Object.assign(new Error('private provider data ' + participant), {
      groupCatalogReason: 'provider_rate_limited',
      retryAfterSeconds: seconds,
    });
  };
  const route = createChannelRoutes({
    env: {QR_CHANNELS_ENABLED: '1'},
    brokerFactory: () => ({}),
    sessionFactory: () => assert.fail('no socket or provider may be created'),
    managerFactory: () => ({
      deviceGroupAdd: refuse('group-add'),
      deviceGroupContacts: refuse('group-contacts'),
    }),
  });
  async function request(tool, action, input) {
    const req = Readable.from([Buffer.from(JSON.stringify(input))]);
    req.url = '/internal/channels/260eb770-da4d-45b1-8d9a-e37221548d12/device/' + tool + '/' + action;
    req.method = 'POST';
    req.headers = {'x-orion-channel-key': 'synthetic-key-'.repeat(3)};
    const response = {};
    const handled = await route(req, {
      writeHead: (status, headers) => Object.assign(response, {status, headers}),
      end: data => { response.body = JSON.parse(data); },
    });
    assert.equal(handled, true);
    assert.equal(response.status, 409);
    assert.equal(response.headers['Cache-Control'], 'no-store');
    return response.body;
  }
  for (const [tool, code] of [['group-add', 'group_add_unavailable'], ['group-contacts', 'group_contacts_unavailable']]) {
    for (const valid of [1, 300, 3600]) {
      seconds = valid;
      for (const [action, input] of [['groups', scope], ['members', memberScope]]) {
        assert.deepEqual(await request(tool, action, input), {
          error: code, reason: 'provider_rate_limited', retry_after_seconds: valid,
        });
      }
    }
    for (const invalid of [0, 3601, '300', 1.5, Infinity]) {
      seconds = invalid;
      assert.deepEqual(await request(tool, 'groups', scope), {error: code});
    }
  }
  seconds = 300;
  for (const action of ['add', 'lookup']) {
    assert.deepEqual(await request('group-add', action, operation), {error: 'group_add_unavailable'});
  }
  assert.equal(managerCalls.length, 24);
  assert.deepEqual(managerCalls.slice(-2), [['group-add', 'add'], ['group-add', 'lookup']]);
});
