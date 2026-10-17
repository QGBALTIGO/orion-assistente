import assert from "node:assert/strict";
import {test} from "node:test";
import {allowAssistantRecipient,allowAssistantAudio,pilotNumber} from "../src/ai-pilot.mjs";

const phone="5521997557364";
const other="5567999999999";
const env={QR_AI_ENABLED:"1",QR_AI_AUDIO_ENABLED:"1",
  QR_AI_PILOT_NUMBER:phone,
  QR_AI_API_URL:"http://orion-assistente.railway.internal:8080/internal/qr/ai/reply"};
test("Brazilian single-recipient pilot accepts only intended user",()=>{
  assert.equal(pilotNumber(env),phone);
  assert.equal(allowAssistantRecipient(phone,{chatbot:false},env),true);
  assert.equal(allowAssistantRecipient(other,{chatbot:true},env),false);
  assert.equal(allowAssistantAudio(phone,{chatbot:false,transcription:false},env),true);
  assert.equal(allowAssistantAudio(other,{chatbot:true,transcription:true},env),false);
});
test("pilot cannot run without explicit opt-in and endpoint",()=>{
  assert.equal(allowAssistantRecipient(phone,{chatbot:true},{...env,QR_AI_ENABLED:"0"}),false);
  assert.equal(allowAssistantRecipient(phone,{chatbot:true},{...env,QR_AI_API_URL:""}),false);
  assert.equal(allowAssistantAudio(phone,{chatbot:true},{...env,QR_AI_AUDIO_ENABLED:"0"}),false);
});
test("invalid pilot number fails closed, even with global chatbot enabled",()=>{
  const invalid={...env,QR_AI_PILOT_NUMBER:"552199755364"};
  assert.equal(pilotNumber(invalid),null);
  assert.equal(allowAssistantRecipient(phone,{chatbot:true},invalid),false);
  assert.equal(allowAssistantAudio(phone,{chatbot:true,transcription:true},invalid),false);
});
test("without pilot opt-in only normal chatbot setting controls responses",()=>{
  const normal={...env,QR_AI_PILOT_NUMBER:""};
  assert.equal(allowAssistantRecipient(phone,{chatbot:false},normal),false);
  assert.equal(allowAssistantRecipient(phone,{chatbot:true},normal),true);
  assert.equal(allowAssistantAudio(phone,{chatbot:true,transcription:false},normal),false);
});
