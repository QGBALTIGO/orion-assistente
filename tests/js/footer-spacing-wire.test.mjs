import test from 'node:test';
import assert from 'node:assert/strict';
import {urlButtonContent,isUrlButtonMessage} from '../../qr-bridge/src/url-button.mjs';
import {buildChatbotContent} from '../../qr-bridge/src/chatbot-message.mjs';

const validUrl='https://example.com/suporte';

test('native CTA button renders the spaced footer in its body without duplicating it',()=>{
  const content=urlButtonContent({
    type:'url_button',
    text:'Clique abaixo para falar com nossa equipe.\n\nAtendimento Empresa sintética',
    footer:'',button_text:'Abrir suporte',url:validUrl
  });
  assert.equal(content.interactiveMessage.body.text,
    'Clique abaixo para falar com nossa equipe.\n\nAtendimento Empresa sintética');
  assert.equal(content.interactiveMessage.footer,undefined);
  assert.equal(content.interactiveMessage.nativeFlowMessage.buttons[0].name,'cta_url');
  assert.equal(isUrlButtonMessage(content),true);
});

test('URL button without configured footer leaves body and CTA unchanged',()=>{
  const value=urlButtonContent({type:'url_button',text:'Olá',footer:'',
    button_text:'Abrir suporte',url:validUrl});
  assert.equal(value.interactiveMessage.body.text,'Olá');
  assert.equal(value.interactiveMessage.footer,undefined);
});

test('native WhatsApp list retains its selections with a spaced footer inside description',async()=>{
  const source={
    type:'list',title:'Serviços',description:'Escolha:\n\nAtendimento Empresa sintética',
    footer:'',button_text:'Ver opções',
    sections:[{title:'Suporte',rows:[{id:'ocb:synthetic-01',title:'Ajuda',description:''}]}]
  };
  const built=await buildChatbotContent(source,{
    sdk:{proto:{Message:{fromObject:obj=>obj}}},
    assertCurrent:async()=>{},isCurrent:()=>true
  });
  assert.equal(built.content.listMessage.description,
    'Escolha:\n\nAtendimento Empresa sintética');
  assert.equal(built.content.listMessage.footerText,'');
  assert.equal(built.content.listMessage.sections[0].rows[0].rowId,'ocb:synthetic-01');
  assert.equal(built.wireMode,'native');
  built.dispose();
});

test('fallback list text keeps its single footer and all options',async()=>{
  const source={type:'list',title:'Serviços',
    description:'Escolha:\n\nAtendimento Empresa sintética',footer:'',button_text:'Ver opções',
    sections:[{title:'Suporte',rows:[{id:'ocb:synthetic-02',title:'Ajuda',description:''}]}]};
  const built=await buildChatbotContent(source,{
    sdk:{proto:{Message:{fromObject:obj=>obj}}},
    assertCurrent:async()=>{},isCurrent:()=>true,interactiveMode:'text_fallback'
  });
  const rendered=built.content.extendedTextMessage.text;
  assert.ok(rendered.includes('Escolha:\n\nAtendimento Empresa sintética'));
  assert.equal(rendered.split('Atendimento Empresa sintética').length,2);
  assert.ok(rendered.includes('1. Ajuda'));
  assert.equal(built.wireMode,'text_fallback');
  built.dispose();
});
