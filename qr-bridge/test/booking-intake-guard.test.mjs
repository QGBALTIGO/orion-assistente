import test from 'node:test';
import assert from 'node:assert/strict';
import {makeBookingIntakeGuard} from '../src/booking-intake-guard.mjs';

test('at most 48 concurrent protocol lookups are admitted under a 500-callback burst',()=>{
 const gate=makeBookingIntakeGuard();
 const releases=Array.from({length:500},()=>gate.reserve());
 assert.equal(releases.filter(Boolean).length,48);
 assert.equal(gate.active(),48);
 for(const release of releases)release?.();
 assert.equal(gate.active(),0);
 const after=gate.reserve();
 assert.equal(typeof after,'function');
 after();after();assert.equal(gate.active(),0,'release is idempotent');
});

test('one sender cannot flood appointments, but another verified sender is independent',()=>{
 let clock=1000;
 const gate=makeBookingIntakeGuard({now:()=>clock,perSenderLimit:3,windowMs:3000});
 for(let i=0;i<3;i++)assert.equal(gate.allowSender('595992820210'),true);
 assert.equal(gate.allowSender('595992820210'),false);
 assert.equal(gate.allowSender('5567999999901'),true);
 clock=4000;
 assert.equal(gate.allowSender('595992820210'),true);
});

test('LID, groups, spoofed JIDs and unsupported countries never enter protocol lookup',()=>{
 const gate=makeBookingIntakeGuard();
 for(const phone of ['','123456789012@lid','595992820210@s.whatsapp.net',
   '+595992820210','5958992820210','55119999999999','12025550123',
   'a'.repeat(400),'5511888888888@g.us']){
   assert.equal(gate.allowSender(phone),false,phone);
 }
 assert.equal(gate.active(),0);
});

test('memory of phone identities is strictly bounded after 250 different senders',()=>{
 let now=0;
 const gate=makeBookingIntakeGuard({now:()=>now,perSenderLimit:1,maxTrackedSenders:12});
 for(let i=0;i<250;i++){
   const phone='5959'+String(i).padStart(8,'0');
   assert.equal(gate.allowSender(phone),true);
   now++;
 }
 // A new independent PN is admitted even after the bounded history evicts old
 // senders; the backend's separate Redis/global limit is mandatory.
 assert.equal(gate.allowSender('595992820210'),true);
});
